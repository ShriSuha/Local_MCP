# Local Task Tracker — MCP Server & Client

A **Task Tracker** with an **MCP server** (FastMCP) and a **client** that uses LlamaIndex + Ollama to manage a **Personal Kanban board** (Todo, In Progress, Done). All data is stored in **SQLite** (`sqlite.db`).

**Default: stdio** — the client spawns the server process and talks over stdin/stdout. One terminal, no HTTP.

---

## Overview

| Component | Role |
|-----------|------|
| **MCP Server** (`server.py`) | FastMCP server. Tools: `add_task`, `list_tasks`, `move_task`, `delete_task`. **Default transport: stdio**. Optional: SSE over HTTP with `--server_type=sse`. Data in **sqlite.db**. |
| **MCP Client** (`client.py`) | By default **spawns** the server via stdio and connects. Or use `--sse` to connect to a running server. Runs LlamaIndex agent (Ollama), interactive chat. |

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

### 2. Run the client (it starts the MCP server automatically)

From the project root:

```bash
uv run client.py
```

The client spawns the MCP server as a subprocess and talks to it over **stdio**. No separate server terminal, no HTTP.

### 3. Chat with your task board

Example prompts: *“Add a task: Review PR”*, *“Show all tasks”*, *“Move task 1 to in progress”*, *“Delete task 2”*. Type `exit` or `quit` to stop.

---

## Optional: SSE (two terminals)

If you prefer to run the server and client separately over HTTP (SSE):

**Terminal 1 — server:**
```bash
uv run server.py --server_type=sse
```
Server listens at **http://127.0.0.1:8000/sse**.

**Terminal 2 — client:**
```bash
uv run client.py --sse
```
Client connects to `http://127.0.0.1:8000/sse` by default. Use `--server-url URL` to override.

---

## Client options

| Option | Description | Default |
|--------|-------------|---------|
| (none) | Use **stdio**: spawn server, talk over pipes | stdio |
| `--sse` | Use **SSE**: connect to a running server | — |
| `--server-url` | SSE endpoint when using `--sse` | `http://127.0.0.1:8000/sse` |
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
2. **Ollama:** Install Ollama, start `ollama serve` in a terminal, then run `ollama pull qwen2.5:3b`. Keep the server running when you use the client.
3. **Run the app:** From the project root run `uv sync`, then `uv run client.py`. The client starts the MCP server and talks over stdio. Data in **sqlite.db**.
4. **SSE (optional):** Run `uv run server.py --server_type=sse` in one terminal, then `uv run client.py --sse` in another.
