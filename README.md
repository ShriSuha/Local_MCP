# Local Task Tracker — MCP Server & Client

A **Task Tracker** MCP server (FastMCP) and a **multi-server MCP client** that connects to **Task Tracker** and **Slack** (and more) in one go. The client uses LlamaIndex + Ollama and merges all MCP tools into a single agent. Task data is stored in **SQLite** (`sqlite.db`).

**Multiple servers:** The client always connects to every server listed in `MCP_SERVERS` (see `client.py`). You can add new servers by appending a config and an optional CLI URL override.

---

## Overview

| Component | Role |
|-----------|------|
| **Task Tracker server** (`server.py`) | FastMCP server. Tools: `add_task`, `list_tasks`, `move_task`, `delete_task`. Default: stdio (client spawns it). Optional: SSE with `--server_type=sse`. Data in **sqlite.db**. |
| **MCP Client** (`client.py`) | Connects to **all** configured MCP servers (Task Tracker + Slack by default), merges their tools, runs one LlamaIndex agent (Ollama). Add servers via the `MCP_SERVERS` list. |

---

## Prerequisites

1. **Python 3.13+** on your system.
2. **uv** — used to install dependencies and run the project. See [Install uv](#install-uv) below.
3. **Ollama** — see [Ollama setup](#ollama-setup-pull-a-model--run-the-server) below to install it, pull a model, and run the server.

---

## Install uv

**uv** is a fast Python package installer and runner. Install it once, then you’ll use `uv sync` and `uv run client.py` in this project.

**Linux / macOS (recommended):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart your terminal (or run `source $HOME/.local/bin/env` if uv was added to your path).

**Alternative (pip):**

```bash
pip install uv
```

**Check that it worked:**

```bash
uv --version
```

You should see a version number (e.g. `uv 0.4.x`).

---

## Ollama setup: run the server, then pull a model

The client uses **Ollama** to run a local AI model. You need to (1) install Ollama, (2) **start the Ollama server**, (3) **pull a model** (the server must be running to pull), and (4) keep the server running when you use the client. Follow these steps once.

### Step 1: Install Ollama

- **Linux / macOS:** Open [https://ollama.com](https://ollama.com) and follow the install instructions for your OS.
- After installing, open a terminal and type `ollama --version` to confirm it works.

### Step 2: Start the Ollama server (must be running to pull or use models)

The Ollama server must be running for both **pulling models** and **using the client**. Start it first.

**Option A — run in a separate terminal (easiest):**

1. Open a **new terminal** (keep it open).
2. Run:
   ```bash
   ollama serve
   ```
3. You should see a message that the server is listening. **Leave this terminal open** — you’ll pull the model and run the client while it’s running.

**Option B — run in the background:**

```bash
nohup ollama serve > /tmp/ollama.log 2>&1 &
```

### Step 3: Pull a model (download it)

With the Ollama server running, open **another terminal** and download the model. This project uses **qwen2.5:3b** by default (a model that supports “tools”):

```bash
ollama pull qwen2.5:3b
```

- The first time, this downloads the model (may take a few minutes).
- When it finishes, the model is ready to use.



### Step 4: Check that Ollama is working

In any terminal, run:

```bash
ollama list
```

You should see `qwen2.5:3b` (or the model you pulled). If you get “ollama server not responding”, go back to Step 2 and start `ollama serve`.

---

## Quick Start (stdio — one terminal)

**Before you start:** Install [uv](#install-uv) and complete [Ollama setup](#ollama-setup-run-the-server-then-pull-a-model) (start `ollama serve`, then pull a model, and keep the server running).

### 1. Install project dependencies

```bash
uv sync
```

### 2. Run the Slack MCP server (required)

The client connects to **Task Tracker** and **Slack**. You need a Slack MCP server that exposes **SSE** (e.g. [dVelopment/slack-mcp-server-sse](https://github.com/dVelopment/slack-mcp-server-sse)) and a Slack app with a **bot token** and **Team ID**. The steps below use your **existing Slack workspace** — you create a small app in that workspace and get the token and Team ID from it.

**Get your Slack bot token and Team ID (from an existing workspace)**

1. **Create a Slack app in your workspace**
   - Go to [api.slack.com/apps](https://api.slack.com/apps) and sign in with your Slack account.
   - Click **Create New App** → **From scratch**.
   - Name the app (e.g. "Task Board MCP") and **select your existing workspace** from the dropdown → **Create App**.

2. **Add bot scopes**
   - In the app, open **OAuth & Permissions** (left sidebar).
   - Under **Scopes** → **Bot Token Scopes**, click **Add an OAuth Scope** and add:
     - `channels:history` — view messages in public channels  
     - `channels:read` — view channel list  
     - `chat:write` — send messages as the app  
     - `users:read` — view users  
   - (Optional: `reactions:write` if you want the agent to add emoji reactions.)

3. **Install the app to your workspace and copy the bot token**
   - Still in **OAuth & Permissions**, click **Install to Workspace** (top of the page).
   - Choose your **existing workspace** and approve the permissions.
   - You’ll see **Bot User OAuth Token** — it starts with `xoxb-`. Copy it; this is your **SLACK_BOT_TOKEN**.

4. **Get your workspace Team ID**
   - **From the Slack URL:** Open [Slack in the browser](https://app.slack.com) and switch to your workspace. The URL looks like `https://app.slack.com/client/T01234567/...`. The part after `/client/` that starts with **T** (e.g. `T01234567`) is your **SLACK_TEAM_ID**.
   - **From workspace settings:** In Slack, click your workspace name (top left) → **Settings & administration** → **Workspace settings**. Open the **Settings** tab; the **Workspace ID** (starts with **T**) is your **SLACK_TEAM_ID**.

5. **Invite the app to channels** (so the agent can post)
   - In your workspace, open any channel where the agent should post (e.g. `#general`).
   - Type `/invite @YourAppName` and send — or use **Channel details** (click the channel name) → **Integrations** → **Add apps** and add your app.

**Save your token and Team ID in a `.env` file (do not commit it):**

From the project root, copy the example and edit:

```bash
cp .env.example .env
# Edit .env and set:
#   SLACK_BOT_TOKEN=xoxb-your-actual-token
#   SLACK_TEAM_ID=your-actual-team-id
```

The Slack MCP server runs via **Docker** only. You can either:

- **Let the client start it** — use `--start-slack-docker` when you run the client (see step 3). The client will start the Slack Docker container from your `.env` if Slack is not already running, and stop it when you exit.
- **Or start Docker yourself** (e.g. in another terminal):

```bash
docker run -p 3000:3000 --env-file .env \
  ghcr.io/dvelopment/slack-mcp-server-sse:latest
```

Keep it running on port 3000 so the client's default `http://127.0.0.1:3000/sse` works.

### 3. Run the client

From the project root:

```bash
uv run client.py --start-slack-docker
```

- **`--start-slack-docker`** — If Slack is not running, the client starts the Slack MCP server via Docker using your `.env` (requires Docker installed). The client stops the container when you exit.
- Without `--start-slack-docker`, you must have the Slack server already running (e.g. you started the `docker run` above in another terminal).

The client spawns the **Task Tracker** MCP server via stdio and connects to **Slack** at `http://127.0.0.1:3000/sse`. One agent has all tools (tasks + Slack). To use a different env file: `uv run client.py --start-slack-docker --slack-env-file path/to/my.env`.

### 4. Chat with your task board and Slack

Example prompts: *“Add a task: Review PR”*, *“Show all tasks”*, *“Move task 1 to in progress”*, *“Delete task 2”*. Type `exit` or `quit` to stop.

---

## Optional: SSE for Task Tracker (two terminals)

By default the client spawns the Task Tracker via stdio. To run Task Tracker separately over HTTP (SSE):

**Terminal 1 — Task Tracker:**
```bash
uv run server.py --server_type=sse
```
Listens at **http://127.0.0.1:8000/sse**.

**Terminal 2 — Slack MCP server** (if not already running). From the project root: `docker run -p 3000:3000 --env-file .env ghcr.io/dvelopment/slack-mcp-server-sse:latest`. Or run the client with `--start-slack-docker` so it starts Slack for you.

**Terminal 3 — client:**
```bash
uv run client.py --sse
```
Client connects to Task Tracker at `http://127.0.0.1:8000/sse` and Slack at `http://127.0.0.1:3000/sse`. Use `--server-url` and `--slack-url` to override.

---

## Adding another MCP server

The client is built to connect to **multiple** MCP servers. The list is in `client.py`:

```python
MCP_SERVERS: list[MCPServerConfig] = [
    MCPServerConfig(id="task-tracker", name="Task Tracker", ...),
    MCPServerConfig(id="slack", name="Slack", ...),
    # Add another:
    # MCPServerConfig(id="github", name="GitHub", stdio_command=None, sse_default_url="http://127.0.0.1:4000/sse"),
]
```

1. **Append a `MCPServerConfig`** with a unique `id`, `name`, and either `stdio_command` (to spawn) or `sse_default_url` (to connect to a running server).
2. **Optional:** add a CLI override in `main()` (e.g. `--github-url`) and pass it in `url_overrides` so users can override the default URL.
3. **Optional:** extend `_build_system_prompt()` if the new server needs specific instructions for the agent.

---

## Client options

| Option | Description | Default |
|--------|-------------|---------|
| (none) | Task Tracker via **stdio** (spawned), Slack via SSE | — |
| `--sse` | Use **SSE** for Task Tracker (must be running separately) | stdio for task-tracker |
| `--server-url` | Task Tracker SSE URL when using `--sse` | `http://127.0.0.1:8000/sse` |
| `--slack-url` | Slack MCP server SSE URL | `http://127.0.0.1:3000/sse` |
| `--start-slack-docker` | Start Slack MCP server via Docker from `.env` if not running; stop on exit | off |
| `--slack-env-file` | Env file for Slack Docker (used with `--start-slack-docker`) | `.env` |
| `--model` | Ollama model (must support tools) | `qwen2.5:3b` |
| `--no-verbose` | Hide tool calls | verbose on |

---

## Project layout

```
Local_MCP/
├── README.md
├── pyproject.toml
├── server.py           # MCP server (FastMCP, stdio default, SQLite)
├── client.py            # MCP client (spawns server via stdio by default)
├── sqlite.db            # Task storage (created by server)
└── ...
```

---

## Summary

1. **uv:** Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`, then `uv --version` to confirm.
2. **Ollama:** Install Ollama, start `ollama serve`, then `ollama pull qwen2.5:3b`. Keep it running when you use the client.
3. **Slack MCP:** Get a bot token and Team ID from [api.slack.com/apps](https://api.slack.com/apps) (see step 2 above). Put them in `.env` (copy from `.env.example`). Run the client with `--start-slack-docker` so it starts the Slack Docker container for you, or run `docker run -p 3000:3000 --env-file .env ghcr.io/dvelopment/slack-mcp-server-sse:latest` yourself. The client connects by default at `http://127.0.0.1:3000/sse`.
4. **Run the client:** From the project root run `uv sync`, then `uv run client.py`. The client spawns the Task Tracker and connects to Slack; one agent has all tools. Data in **sqlite.db**.
5. **Add more servers:** Edit the `MCP_SERVERS` list in `client.py` and add CLI URL overrides as needed.
