#!/usr/bin/env python3
"""
Task Tracker MCP Client — connects to the Task Tracker MCP server and manages
a Kanban board (Todo / In Progress / Done) using an LLM agent (Ollama) and
MCP tools. Server stores data in SQLite.

Usage:
    uv run client.py              # stdio: client spawns server (default)
    uv run client.py --stdio
    uv run client.py --server-url http://127.0.0.1:8000/sse   # SSE: connect to running server
    uv run client.py --no-verbose
"""

import argparse
import asyncio
import logging
import re
import sys

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

DEFAULT_SERVER_URL = "http://127.0.0.1:8000/sse"
# Must be a model with tool/function-calling support (e.g. llama3.2, qwen2.5). gemma2:2b does not support tools.
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
# For stdio: command to run the server (client spawns it)
STDIO_SERVER_COMMAND = "uv"
STDIO_SERVER_ARGS = ["run", "server.py", "--server_type=stdio"]
OLLAMA_TIMEOUT = 120.0

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


SYSTEM_PROMPT = """\
You are an AI assistant for a Personal Kanban task board.

Use the tools to manage tasks: add tasks, list tasks (by column or all), move tasks between Todo / In Progress / Done, and delete tasks. Always use the tools when the user asks to add, list, move, or delete tasks.
"""


# -----------------------------------------------------------------------------
# Agent setup
# -----------------------------------------------------------------------------

async def get_agent(mcp_tools: McpToolSpec, model: str = DEFAULT_OLLAMA_MODEL) -> FunctionAgent:
    """Build a FunctionAgent with MCP tools and the local Ollama LLM."""
    llm = Ollama(model=model, request_timeout=OLLAMA_TIMEOUT)
    Settings.llm = llm

    tools = await mcp_tools.to_tool_list_async()
    agent = FunctionAgent(
        name="Agent",
        description="An agent that manages the Kanban task board.",
        tools=tools,
        llm=llm,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent


async def handle_user_message(
    message_content: str,
    agent: FunctionAgent,
    agent_context: Context,
    verbose: bool = True,
) -> str:
    """Run the agent on a user message; stream tool calls if verbose."""
    handler = agent.run(message_content, ctx=agent_context)
    async for event in handler.stream_events():
        if verbose and type(event) == ToolCall:
            print(f"  → Calling tool {event.tool_name} with kwargs {event.tool_kwargs}")
        elif verbose and type(event) == ToolCallResult:
            # Show only that the tool returned, not its full output
            print(f"  ← Tool {event.tool_name} returned.")

    response = await handler
    return str(response)


async def run_client(
    server_url: str | None = None,
    use_stdio: bool = True,
    model: str = DEFAULT_OLLAMA_MODEL,
    verbose: bool = True,
) -> None:
    """Connect to MCP server (stdio or SSE), build agent, and run the interactive loop."""
    if use_stdio:
        print("Starting MCP server via stdio (spawning process)...")
        mcp_client = BasicMCPClient(STDIO_SERVER_COMMAND, args=STDIO_SERVER_ARGS)
    else:
        url = server_url or DEFAULT_SERVER_URL
        print("Connecting to MCP server at", url)
        mcp_client = BasicMCPClient(url)
    mcp_tools = McpToolSpec(client=mcp_client)

    try:
        print("Loading tools and building agent (model:", model, ")...")
        agent = await get_agent(mcp_tools, model=model)
    except Exception as e:
        err = str(e).lower()
        if "connect" in err or "connection" in err or "refused" in err and not use_stdio:
            print(
                "\nCould not connect to the MCP server. Make sure the server is running:\n"
                "  uv run server.py --server_type=sse\n",
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
            response = await handle_user_message(
                user_input, agent, agent_context, verbose=verbose
            )
            formatted = _format_agent_response(response)
            print("\n  🤖 Agent:")
            print("  " + "\n  ".join(formatted.split("\n")))
        except Exception as e:
            print("Error:", e, file=sys.stderr)

        print()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Task Tracker MCP client: Kanban board via MCP server and Ollama.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Use SSE: connect to a running server (default is stdio: client spawns server).",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help="MCP server SSE URL when using --sse (default: http://127.0.0.1:8000/sse)",
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

    use_stdio = not args.sse

    asyncio.run(
        run_client(
            server_url=args.server_url if args.sse else None,
            use_stdio=use_stdio,
            model=args.model,
            verbose=not args.no_verbose,
        )
    )


if __name__ == "__main__":
    main()
