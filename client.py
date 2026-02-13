#!/usr/bin/env python3
"""
MCP Client — connects to multiple MCP servers (Task Tracker, Slack, etc.) and
runs an LLM agent (Ollama) with all tools merged. Add or remove servers by
editing MCP_SERVERS and CLI URL overrides.

Usage:
    uv run client.py --start-slack-docker   # Start Slack via Docker from .env if not running
    uv run client.py                        # Task Tracker via stdio, Slack via SSE (default URLs)
    uv run client.py --sse                  # Use SSE for Task Tracker too
    uv run client.py --no-verbose
"""

import argparse
import asyncio
import atexit
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# Reduce noise from MCP/LLM libraries (e.g. "Processing request of type ...")
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("llama_index").setLevel(logging.WARNING)

import nest_asyncio
nest_asyncio.apply()

from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
from llama_index.core.agent.workflow import FunctionAgent, ToolCallResult, ToolCall
from llama_index.core.workflow import Context


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Must be a model with tool/function-calling support (e.g. llama3.2, qwen2.5). gemma2:2b does not support tools.
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 120.0


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one MCP server. Add new servers by appending to MCP_SERVERS."""

    id: str
    name: str
    # Use stdio (client spawns process). If None, only SSE is used.
    stdio_command: tuple[str, list[str]] | None
    # Default SSE URL when not using stdio (or for servers that only support SSE).
    sse_default_url: str


# Task Tracker tool names (used to detect "claimed posted to Slack but only called task tools").
TASK_TRACKER_TOOLS = frozenset({"add_task", "list_tasks", "move_task", "delete_task"})

# Single source of truth: all MCP servers the client connects to.
# To add a new server: append a MCPServerConfig and add a CLI override (e.g. --github-url) if needed.
MCP_SERVERS: list[MCPServerConfig] = [
    MCPServerConfig(
        id="task-tracker",
        name="Task Tracker",
        stdio_command=("uv", ["run", "server.py", "--server_type=stdio"]),
        sse_default_url="http://127.0.0.1:8000/sse",
    ),
    MCPServerConfig(
        id="slack",
        name="Slack",
        stdio_command=None,
        sse_default_url="http://127.0.0.1:3000/sse",
    ),
]

# Slack Docker (when using --start-slack-docker)
SLACK_DOCKER_IMAGE = "ghcr.io/dvelopment/slack-mcp-server-sse:latest"
SLACK_DOCKER_CONTAINER_NAME = "local-mcp-slack"
SLACK_DOCKER_PORT = 3000
SLACK_HEALTH_URL = f"http://127.0.0.1:{SLACK_DOCKER_PORT}/health"
SLACK_READY_WAIT_SEC = 8
SLACK_READY_POLL_INTERVAL = 0.5


def _slack_docker_is_reachable() -> bool:
    """Return True if the Slack MCP server (e.g. Docker) is reachable."""
    try:
        with urlopen(SLACK_HEALTH_URL, timeout=2) as _:
            return True
    except (URLError, OSError):
        return False


def _start_slack_docker(env_file: Path) -> bool:
    """Start Slack MCP server via Docker with --env-file. Return True if we started it."""
    env_file = env_file.resolve()
    if not env_file.is_file():
        print(f"Slack env file not found: {env_file}", file=sys.stderr)
        print("  Copy .env.example to .env and set SLACK_BOT_TOKEN and SLACK_TEAM_ID.", file=sys.stderr)
        sys.exit(1)
    # Start in background; --rm so container is removed when stopped
    cmd = [
        "docker", "run", "-d", "--rm",
        "-p", f"{SLACK_DOCKER_PORT}:{SLACK_DOCKER_PORT}",
        "--env-file", str(env_file),
        "--name", SLACK_DOCKER_CONTAINER_NAME,
        SLACK_DOCKER_IMAGE,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").lower() + (e.stdout or "").lower()
        if "already in use" in err or "conflict" in err:
            # Container name exists; try starting it if it was stopped
            subprocess.run(
                ["docker", "start", SLACK_DOCKER_CONTAINER_NAME],
                capture_output=True,
                timeout=10,
            )
            return False
        print("Failed to start Slack Docker container:", e.stderr or e.stdout, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Docker not found. Install Docker or run the Slack MCP server manually.", file=sys.stderr)
        sys.exit(1)
    return True


def _wait_for_slack_ready() -> None:
    """Block until Slack MCP server is ready or timeout."""
    for _ in range(int(SLACK_READY_WAIT_SEC / SLACK_READY_POLL_INTERVAL)):
        if _slack_docker_is_reachable():
            return
        time.sleep(SLACK_READY_POLL_INTERVAL)
    print("Slack MCP server did not become ready in time.", file=sys.stderr)
    sys.exit(1)


def _ensure_slack_docker_running(env_file: Path) -> bool:
    """If Slack is not reachable, start Docker from env_file. Return True if we started the container."""
    if _slack_docker_is_reachable():
        return False
    print("Slack MCP server not running. Starting Docker container...")
    we_started = _start_slack_docker(env_file)
    _wait_for_slack_ready()
    if we_started:
        print("Slack MCP server (Docker) started.")
    return we_started


def _stop_slack_docker() -> None:
    """Stop the Slack Docker container if it exists."""
    subprocess.run(
        ["docker", "stop", SLACK_DOCKER_CONTAINER_NAME],
        capture_output=True,
        timeout=10,
    )


def _format_tool_output(out) -> str:
    """Turn tool output (possibly list of content) into a single string with newlines preserved."""
    if out is None:
        return ""
    if isinstance(out, list):
        parts = []
        for item in out:
            if hasattr(item, "text"):
                parts.append(item.text)
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else str(out)
    return str(out)


def _format_agent_response(text: str) -> str:
    """Format the agent's response for clean terminal output: emojis, indentation, no raw markdown."""
    if not text or not text.strip():
        return text
    lines = text.strip().split("\n")
    out = []
    for line in lines:
        # Replace common section headers with emoji versions
        line = re.sub(r"\*\*Todo\*\*", "Todo", line, flags=re.IGNORECASE)
        line = re.sub(r"\*\*In Progress\*\*", "In Progress", line, flags=re.IGNORECASE)
        line = re.sub(r"\*\*Done\*\*", "Done", line, flags=re.IGNORECASE)
        # List items: "- **#2:** task" or "- **#2** task" → "   • #2  task"
        line = re.sub(r"^(\s*)[\-*]\s*\*\*#?(\d+)\*\*:?\s*\*\*(.+?)\*\*", r"\1   • #\2  \3", line)
        line = re.sub(r"^(\s*)[\-*]\s*\*\*(.+?)\*\*", r"\1   • \2", line)
        line = re.sub(r"^(\s*)[\-*]\s+(.+)", r"\1   • \2", line)
        # Numbered list "1. #1 ..." → "   • #1 ..."
        line = re.sub(r"^(\s*)\d+\.\s+(.+)", r"\1   • \2", line)
        # Clean up (None) or similar
        line = re.sub(r"\(None\)", "(no tasks)", line, flags=re.IGNORECASE)
        # Remove remaining ** for bold (terminal doesn't render)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        out.append(line)
    return "\n".join(out)


def _build_system_prompt(server_names: list[str]) -> str:
    """Build system prompt from connected server names so adding a server doesn't require editing prompt text."""
    names = " and ".join(server_names)
    return f"""\
You are an AI assistant with access to: {names}.

- Task Tracker: use the task tools to add, list, move, or delete tasks (Todo / In Progress / Done). Always use the tools when the user asks about tasks. Use the exact parameter values the tools expect.
- Slack: use the Slack tools to send messages to channels or users, read channel history, search, etc. When the user asks to post or send something to Slack (e.g. "post tasks in #general"): (1) get the content (e.g. list_tasks), then (2) call the Slack tool (e.g. post message / send to channel) with that content. Do not say you have posted or sent to Slack unless you have actually called a Slack tool in this turn. For slack_post_message (and other Slack tools), channel_id must be the Slack channel ID (e.g. C01234567), not the channel name like #all-learn-hack. To post to a channel by name: call slack_list_channels, find the channel whose name matches (e.g. "all-learn-hack" without the #), and use that channel's "id" field as channel_id in slack_post_message.

Use the right tool(s) for each request. You can combine tools (e.g. list tasks then post to Slack).
"""


# -----------------------------------------------------------------------------
# Agent setup
# -----------------------------------------------------------------------------

async def get_agent(
    tools: list,
    model: str = DEFAULT_OLLAMA_MODEL,
    system_prompt: str | None = None,
) -> FunctionAgent:
    """Build a FunctionAgent with the given MCP tools and the local Ollama LLM."""
    if system_prompt is None:
        system_prompt = _build_system_prompt([s.name for s in MCP_SERVERS])
    llm = Ollama(model=model, request_timeout=OLLAMA_TIMEOUT)
    Settings.llm = llm

    agent = FunctionAgent(
        name="Agent",
        description="An agent with access to multiple MCP servers (Task Tracker, Slack, etc.).",
        tools=tools,
        llm=llm,
        system_prompt=system_prompt,
    )
    return agent


async def handle_user_message(
    message_content: str,
    agent: FunctionAgent,
    agent_context: Context,
    verbose: bool = True,
) -> tuple[str, set[str]]:
    """Run the agent on a user message; stream tool calls if verbose. Returns (response_text, tools_called)."""
    handler = agent.run(message_content, ctx=agent_context)
    last_tool_output = None
    tools_called: set[str] = set()
    async for event in handler.stream_events():
        if verbose and type(event) == ToolCall:
            print(f"  → Calling tool {event.tool_name} with kwargs {event.tool_kwargs}")
            tools_called.add(event.tool_name)
        elif type(event) == ToolCallResult:
            last_tool_output = getattr(event, "tool_output", None)

    response = await handler
    response_str = str(response).strip() if response else ""
    # If the LLM returned no text but a tool was run, show the tool result so the user sees feedback
    if not response_str and last_tool_output is not None:
        response_str = _format_tool_output(last_tool_output)
    return response_str, tools_called


def _create_mcp_client(
    config: MCPServerConfig,
    use_sse: bool,
    url_overrides: dict[str, str],
) -> BasicMCPClient:
    """Create a BasicMCPClient for one server. use_sse: use SSE for servers that support stdio."""
    if config.stdio_command is not None and not use_sse:
        cmd, args = config.stdio_command
        return BasicMCPClient(cmd, args=args)
    url = url_overrides.get(config.id, config.sse_default_url)
    return BasicMCPClient(url)


async def run_client(
    servers: list[MCPServerConfig],
    use_sse: bool = False,
    url_overrides: dict[str, str] | None = None,
    model: str = DEFAULT_OLLAMA_MODEL,
    verbose: bool = True,
    start_slack_docker: bool = False,
    slack_env_file: Path | None = None,
) -> None:
    """Connect to all configured MCP servers, merge their tools, and run the interactive loop."""
    url_overrides = url_overrides or {}
    slack_docker_started_by_us = False
    if start_slack_docker and slack_env_file is not None:
        slack_docker_started_by_us = _ensure_slack_docker_running(slack_env_file)
        if slack_docker_started_by_us:
            atexit.register(_stop_slack_docker)
    all_specs: list[McpToolSpec] = []
    for config in servers:
        if config.stdio_command is not None and not use_sse:
            print(f"Starting {config.name} MCP server via stdio (spawning process)...")
        else:
            url = url_overrides.get(config.id, config.sse_default_url)
            print(f"Connecting to {config.name} MCP server at {url}")
        client = _create_mcp_client(config, use_sse, url_overrides)
        all_specs.append(McpToolSpec(client=client))

    try:
        print("Loading tools and building agent (model:", model, ")...")
        tools: list = []
        for spec in all_specs:
            tools.extend(await spec.to_tool_list_async())
        server_names = [s.name for s in servers]
        system_prompt = _build_system_prompt(server_names)
        agent = await get_agent(tools=tools, model=model, system_prompt=system_prompt)
    except Exception as e:
        err = str(e).lower()
        if "connect" in err or "connection" in err or "refused" in err:
            print(
                "\nCould not connect to an MCP server. Make sure all servers are running.\n"
                "  Task Tracker (SSE): uv run server.py --server_type=sse\n"
                "  Slack: run Docker manually or use --start-slack-docker (with .env from .env.example)\n",
                file=sys.stderr,
            )
        raise

    agent_context = Context(agent)

    print("\nReady. Type your message and press Enter. Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("Bye.")
            break

        try:
            response, tools_called = await handle_user_message(
                user_input, agent, agent_context, verbose=verbose
            )
            formatted = _format_agent_response(response)
            print("\n  🤖 Agent:")
            if formatted.strip():
                print("  " + "\n  ".join(formatted.split("\n")))
            else:
                print("  (No response. Try rephrasing, e.g. \"move task 1 to in progress\" or \"move task 1 to in_progress\".)")
            # Warn if agent claimed it posted to Slack but only task-tracker tools were called
            if formatted.strip() and tools_called and tools_called <= TASK_TRACKER_TOOLS:
                lower = formatted.lower()
                if ("posted" in lower or "sent" in lower or "in the #" in lower) and "channel" in lower:
                    print("  ⚠️  (No Slack tool was called — the message was not actually posted.)")
        except Exception as e:
            print("Error:", e, file=sys.stderr)

        print()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP client: connects to Task Tracker + Slack (and more) MCP servers, single agent with merged tools.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Use SSE for Task Tracker (default: spawn via stdio). Slack always uses SSE.",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        metavar="URL",
        help="Task Tracker SSE URL when using --sse (default: http://127.0.0.1:8000/sse)",
    )
    parser.add_argument(
        "--slack-url",
        default=None,
        metavar="URL",
        help="Slack MCP server SSE URL (default: http://127.0.0.1:3000/sse)",
    )
    parser.add_argument(
        "--start-slack-docker",
        action="store_true",
        help="If Slack is not running, start it via Docker using --slack-env-file (requires Docker).",
    )
    parser.add_argument(
        "--slack-env-file",
        default=None,
        metavar="PATH",
        help="Env file for Slack Docker (SLACK_BOT_TOKEN, SLACK_TEAM_ID). Default: .env in current directory. Used with --start-slack-docker.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_OLLAMA_MODEL,
        help="Ollama model name",
    )
    parser.add_argument(
        "--no-verbose",
        action="store_true",
        help="Do not print tool calls and results",
    )
    args = parser.parse_args()

    # URL overrides per server id (add --new-server-url when you add a server to MCP_SERVERS)
    url_overrides: dict[str, str] = {}
    if args.sse and args.server_url:
        url_overrides["task-tracker"] = args.server_url
    if args.slack_url:
        url_overrides["slack"] = args.slack_url

    slack_env_file = Path(args.slack_env_file or ".env") if args.start_slack_docker else None

    asyncio.run(
        run_client(
            servers=MCP_SERVERS,
            use_sse=args.sse,
            url_overrides=url_overrides if url_overrides else None,
            model=args.model,
            verbose=not args.no_verbose,
            start_slack_docker=args.start_slack_docker,
            slack_env_file=slack_env_file,
        )
    )


if __name__ == "__main__":
    main()
