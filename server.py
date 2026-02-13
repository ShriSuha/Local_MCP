#!/usr/bin/env python3
"""
Task Tracker MCP Server — A Kanban-style task manager (Todo, In Progress, Done).
Uses FastMCP. Transport: stdio (default) or SSE over HTTP. Data in SQLite (sqlite.db).
"""

import argparse
import sqlite3
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

# -----------------------------------------------------------------------------
# Config & DB
# -----------------------------------------------------------------------------

DB_PATH = Path("sqlite.db")
TaskStatus = Literal["todo", "in_progress", "done"]

mcp = FastMCP("task-tracker")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo'
                CHECK (status IN ('todo', 'in_progress', 'done'))
            )
        """)
        conn.commit()


def format_board(rows: list, status_filter: str) -> str:
    """Format task board for clear terminal display (plain text, no markdown)."""
    if not rows:
        return "📋 No tasks yet. Add your first task to get started!" if status_filter == "all" else f"📋 No tasks with status '{status_filter}'."

    tasks = [dict(r) for r in rows]
    todo_list = [t for t in tasks if t["status"] == "todo"]
    in_progress_list = [t for t in tasks if t["status"] == "in_progress"]
    done_list = [t for t in tasks if t["status"] == "done"]

    sep = "────────────────────────────────────"
    lines = ["", "  📋 TASK BOARD", sep]

    def section(label: str, task_list: list) -> list[str]:
        out = [f"  {label}", sep]
        if task_list:
            for t in task_list:
                out.append(f"    #{t['id']}  {t['title']}")
                if t.get("description"):
                    out.append(f"         {t['description']}")
        else:
            out.append("    (none)")
        out.append("")
        return out

    if status_filter in ("all", "todo"):
        lines.extend(section("📝 TODO", todo_list))
    if status_filter in ("all", "in_progress"):
        lines.extend(section("🚀 IN PROGRESS", in_progress_list))
    if status_filter in ("all", "done"):
        lines.extend(section("✅ DONE", done_list))

    return "\n".join(lines).strip()


# -----------------------------------------------------------------------------
# MCP Tools
# -----------------------------------------------------------------------------


@mcp.tool()
def add_task(
    title: str,
    description: str = "",
    status: TaskStatus = "todo",
) -> str:
    """Add a new task to the Kanban board.

    Args:
        title: Task title (required).
        description: Optional task description.
        status: Initial column: "todo", "in_progress", or "done". Defaults to "todo".

    Returns:
        A confirmation message with the new task ID and title.
    """
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, description, status) VALUES (?, ?, ?)",
            (title, description, status),
        )
        conn.commit()
        task_id = cur.lastrowid
    return f"✅ Task created!\n\nID: {task_id}\nTitle: {title}\nStatus: {status}"


@mcp.tool()
def list_tasks(status: Literal["todo", "in_progress", "done", "all"] = "all") -> str:
    """List tasks on the Kanban board, optionally filtered by column.

    Args:
        status: Filter by "todo", "in_progress", "done", or "all" to see every column. Defaults to "all".

    Returns:
        A formatted board view (Todo / In Progress / Done) with task IDs, titles, and descriptions.
    """
    init_db()
    with get_conn() as conn:
        if status == "all":
            rows = conn.execute("SELECT id, title, description, status FROM tasks ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, description, status FROM tasks WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
    return format_board(rows, status)


@mcp.tool()
def move_task(task_id: int, new_status: TaskStatus) -> str:
    """Move a task to a different column (todo, in_progress, or done).

    Args:
        task_id: The ID of the task to move.
        new_status: Target column: "todo", "in_progress", or "done".

    Returns:
        Confirmation with the task title and old → new status.
    """
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"❌ Task #{task_id} not found."
        old_status = row["status"]
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
    labels = {"todo": "📝 Todo", "in_progress": "🚀 In Progress", "done": "✅ Done"}
    return f"✅ Task #{task_id} moved!\n\n{row['title']}\n{labels[old_status]} → {labels[new_status]}"


@mcp.tool()
def delete_task(task_id: int) -> str:
    """Remove a task from the board permanently.

    Args:
        task_id: The ID of the task to delete.

    Returns:
        Confirmation with the deleted task ID and title.
    """
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"❌ Task #{task_id} not found."
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    return f"🗑️ Task deleted!\n\nID: #{row['id']}\nTitle: {row['title']}"


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--server_type",
        type=str,
        default="stdio",
        choices=["sse", "stdio"],
        help="Use 'stdio' (default) for pipe-based clients, 'sse' for MCP over HTTP.",
    )
    args = parser.parse_args()
    if args.server_type == "stdio":
        print("🚀 Task Tracker MCP server starting (stdio)...")
    else:
        print("🚀 Task Tracker MCP server starting (SSE at http://127.0.0.1:8000/sse)...")
    mcp.run(args.server_type)
