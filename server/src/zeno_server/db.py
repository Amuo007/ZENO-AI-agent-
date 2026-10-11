"""SQLite storage for ZENO's state: users, their MCP servers, Bits, memories and conversations."""

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from zeno_server.config import settings

DATA_DIR = settings.data_dir
DB_PATH = DATA_DIR / "zeno.db"
# The database's name before the rename to ZENO; moved over on first start.
LEGACY_DB_PATH = DATA_DIR / "orion.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,         -- 'g_' + the Google account's subject id
    email TEXT NOT NULL,
    name TEXT,
    picture TEXT,
    voice TEXT,                  -- chosen voice id, e.g. 'kokoro:af_heart'; NULL = default
    memory_enabled INTEGER NOT NULL DEFAULT 1,  -- learn from conversations
    created_at REAL NOT NULL,
    last_login REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'pipedream' or 'custom'
    name TEXT NOT NULL,
    app_slug TEXT,               -- pipedream: the app's name slug
    icon TEXT,
    url TEXT,                    -- custom: MCP server URL (streamable HTTP)
    token TEXT,                  -- custom: optional bearer token
    enabled INTEGER NOT NULL DEFAULT 1,
    tools TEXT,                  -- JSON list of enabled tool names; NULL = the defaults
    created_at REAL NOT NULL
);

-- One row per task a Bit worked on.
CREATE TABLE IF NOT EXISTS bits (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    slot INTEGER NOT NULL,       -- 1 = BIT-01; each slot has its own workspace folder
    title TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL,        -- waiting, starting, running, asking, sleeping, paused,
                                 -- done, failed, stopped
    wait_for TEXT NOT NULL DEFAULT '[]',  -- JSON list of bit ids that must finish first
    activity TEXT,               -- what it's doing right now
    result TEXT,                 -- its final report
    error TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,  -- prompt tokens served from the provider's cache
    model TEXT,                  -- the model it worked with
    llm_calls INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'zeno',  -- 'zeno' (delegated by voice) or 'dashboard'
    reported INTEGER NOT NULL DEFAULT 0,  -- ZENO has told the user how it ended
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS bits_by_user ON bits (user_id, created_at);

-- A Bit's step-by-step log, shown live on the dashboard.
CREATE TABLE IF NOT EXISTS bit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bit_id TEXT NOT NULL,
    at REAL NOT NULL,
    kind TEXT NOT NULL,          -- status, think, tool, output, error, result
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bit_events_by_bit ON bit_events (bit_id, id);

-- Who published each file in a user's /workspace/shared, so Bits can't overwrite each other.
CREATE TABLE IF NOT EXISTS shared_files (
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    bit_name TEXT NOT NULL,      -- 'BIT-01'
    bit_id TEXT,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, name)
);

-- What passes between a Bit and the user (through ZENO or the dashboard): the Bit's questions
-- and updates, and the user's answers and messages.
CREATE TABLE IF NOT EXISTS bit_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bit_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- question, update (from the Bit); answer, message (to it)
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    answer TEXT,                 -- questions: the user's answer
    answered_at REAL,            -- questions: when it was answered (or dropped)
    delivered INTEGER NOT NULL DEFAULT 0  -- questions/updates: ZENO has told the user
);
CREATE INDEX IF NOT EXISTS bit_messages_by_user ON bit_messages (user_id, id);

-- The toolbox: scripts Bits wrote and saved for every Bit of the same user to use.
CREATE TABLE IF NOT EXISTS bit_tools (
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    usage TEXT NOT NULL,         -- how to run it, e.g. "python3 /workspace/tools/pdf_text/pdf_text.py FILE"
    files TEXT NOT NULL DEFAULT '[]',  -- JSON list of its file names
    made_by TEXT NOT NULL,       -- 'BIT-01'
    bit_id TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, name)
);

-- Things ZENO knows about a user.
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,          -- profile, preference, work, people, other
    text TEXT NOT NULL,
    source TEXT NOT NULL,        -- learned (from a conversation), told (asked to remember), added (dashboard)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_by_user ON memories (user_id, updated_at);

-- Past voice conversations, with a summary written after each one ends.
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    title TEXT,
    summary TEXT,
    messages TEXT NOT NULL DEFAULT '[]'   -- JSON list of {role, text}
);
CREATE INDEX IF NOT EXISTS conversations_by_user ON conversations (user_id, started_at);

-- Server-wide settings edited on the dashboard, e.g. which AI models ZENO and the Bits use.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL           -- JSON
);
"""

ACTIVE_BIT_STATUSES = ("waiting", "starting", "running", "asking", "sleeping")
# A Bit keeping watch or waiting on the user when the server stopped; it resumes on startup.
RESUMABLE_BIT_STATUSES = ("asking", "sleeping", "paused")
MEMORY_KINDS = ("profile", "preference", "work", "people", "other")


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def init():
    if LEGACY_DB_PATH.exists() and not DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            old = LEGACY_DB_PATH.with_name(LEGACY_DB_PATH.name + suffix)
            if old.exists():
                old.rename(DB_PATH.with_name(DB_PATH.name + suffix))
    with _connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(mcp_servers)")}
        if columns and "user_id" not in columns:
            # Servers added before sign-in existed have no owner; start the table fresh.
            conn.execute("DROP TABLE mcp_servers")
        conn.executescript(SCHEMA)
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "voice" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN voice TEXT")
        if "memory_enabled" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1")
        bit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(bits)")}
        if "cached_tokens" not in bit_columns:
            conn.execute("ALTER TABLE bits ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0")
        if "model" not in bit_columns:
            conn.execute("ALTER TABLE bits ADD COLUMN model TEXT")


# --- Server settings ---------------------------------------------------------------------------


def get_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def put_setting(key: str, value):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def first_user_id() -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
    return row["id"] if row else None


# --- Users -------------------------------------------------------------------------------------


def set_voice(user_id: str, voice: str):
    with _connect() as conn:
        conn.execute("UPDATE users SET voice = ? WHERE id = ?", (voice, user_id))


def set_memory_enabled(user_id: str, enabled: bool):
    with _connect() as conn:
        conn.execute("UPDATE users SET memory_enabled = ? WHERE id = ?", (int(enabled), user_id))


def upsert_user(*, user_id: str, email: str, name: str | None, picture: str | None) -> dict:
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, picture, created_at, last_login) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET email = excluded.email, name = excluded.name, "
            "picture = excluded.picture, last_login = excluded.last_login",
            (user_id, email, name, picture, now, now),
        )
    return get_user(user_id)


def get_user(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# --- MCP servers -------------------------------------------------------------------------------


def _row(row: sqlite3.Row) -> dict:
    server = dict(row)
    server["enabled"] = bool(server["enabled"])
    server["tools"] = json.loads(server["tools"]) if server["tools"] is not None else None
    return server


def list_servers(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM mcp_servers WHERE user_id = ? ORDER BY created_at", (user_id,)
        ).fetchall()
    return [_row(row) for row in rows]


def get_server(user_id: str, server_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mcp_servers WHERE id = ? AND user_id = ?", (server_id, user_id)
        ).fetchone()
    return _row(row) if row else None


def add_server(
    user_id: str,
    *,
    kind: str,
    name: str,
    app_slug: str | None = None,
    icon: str | None = None,
    url: str | None = None,
    token: str | None = None,
) -> dict:
    server_id = _new_id()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mcp_servers "
            "(id, user_id, kind, name, app_slug, icon, url, token, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (server_id, user_id, kind, name, app_slug, icon, url, token, time.time()),
        )
    return get_server(user_id, server_id)


def update_server(
    user_id: str, server_id: str, *, enabled: bool | None = None, tools: list[str] | None = None
):
    with _connect() as conn:
        if enabled is not None:
            conn.execute(
                "UPDATE mcp_servers SET enabled = ? WHERE id = ? AND user_id = ?",
                (int(enabled), server_id, user_id),
            )
        if tools is not None:
            conn.execute(
                "UPDATE mcp_servers SET tools = ? WHERE id = ? AND user_id = ?",
                (json.dumps(tools), server_id, user_id),
            )
    return get_server(user_id, server_id)


def delete_server(user_id: str, server_id: str):
    with _connect() as conn:
        conn.execute(
            "DELETE FROM mcp_servers WHERE id = ? AND user_id = ?", (server_id, user_id)
        )


# --- Bits --------------------------------------------------------------------------------------


def bit_name(slot: int) -> str:
    return f"BIT-{slot:02d}"


def _bit(row: sqlite3.Row) -> dict:
    bit = dict(row)
    bit["name"] = bit_name(bit["slot"])
    bit["wait_for"] = json.loads(bit["wait_for"])
    bit["reported"] = bool(bit["reported"])
    return bit


def create_bit(
    user_id: str, *, slot: int, title: str, task: str, wait_for: list[str], source: str
) -> dict:
    bit_id = _new_id()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bits (id, user_id, slot, title, task, status, wait_for, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bit_id,
                user_id,
                slot,
                title,
                task,
                "waiting" if wait_for else "starting",
                json.dumps(wait_for),
                source,
                time.time(),
            ),
        )
    return get_bit(bit_id)


def get_bit(bit_id: str, user_id: str | None = None) -> dict | None:
    with _connect() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM bits WHERE id = ? AND user_id = ?", (bit_id, user_id)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM bits WHERE id = ?", (bit_id,)).fetchone()
    return _bit(row) if row else None


def list_bits(user_id: str, limit: int = 30) -> list[dict]:
    """The user's active Bits, then their most recent finished ones."""
    placeholders = ",".join("?" * len(ACTIVE_BIT_STATUSES))
    with _connect() as conn:
        active = conn.execute(
            f"SELECT * FROM bits WHERE user_id = ? AND status IN ({placeholders}) ORDER BY slot",
            (user_id, *ACTIVE_BIT_STATUSES),
        ).fetchall()
        finished = conn.execute(
            f"SELECT * FROM bits WHERE user_id = ? AND status NOT IN ({placeholders}) "
            "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT ?",
            (user_id, *ACTIVE_BIT_STATUSES, limit),
        ).fetchall()
    return [_bit(row) for row in [*active, *finished]]


def active_bits(user_id: str | None = None) -> list[dict]:
    placeholders = ",".join("?" * len(ACTIVE_BIT_STATUSES))
    query = f"SELECT * FROM bits WHERE status IN ({placeholders})"
    args: tuple = ACTIVE_BIT_STATUSES
    if user_id:
        query += " AND user_id = ?"
        args = (*args, user_id)
    with _connect() as conn:
        rows = conn.execute(query + " ORDER BY slot", args).fetchall()
    return [_bit(row) for row in rows]


def latest_bit_in_slot(user_id: str, slot: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bits WHERE user_id = ? AND slot = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, slot),
        ).fetchone()
    return _bit(row) if row else None


_BIT_FIELDS = {
    "status", "activity", "result", "error", "started_at", "finished_at", "reported", "model",
    "title",
}


def update_bit(bit_id: str, **fields):
    unknown = set(fields) - _BIT_FIELDS
    if unknown:
        raise ValueError(f"Unknown bit fields: {unknown}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE bits SET {assignments} WHERE id = ?", (*fields.values(), bit_id))


def add_bit_usage(bit_id: str, *, prompt_tokens: int = 0, completion_tokens: int = 0,
                  cached_tokens: int = 0, llm_calls: int = 0, tool_calls: int = 0):
    with _connect() as conn:
        conn.execute(
            "UPDATE bits SET prompt_tokens = prompt_tokens + ?, "
            "completion_tokens = completion_tokens + ?, cached_tokens = cached_tokens + ?, "
            "llm_calls = llm_calls + ?, tool_calls = tool_calls + ? WHERE id = ?",
            (prompt_tokens, completion_tokens, cached_tokens, llm_calls, tool_calls, bit_id),
        )


def unreported_bits(user_id: str, since: float) -> list[dict]:
    placeholders = ",".join("?" * len(ACTIVE_BIT_STATUSES))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM bits WHERE user_id = ? AND reported = 0 "
            f"AND status NOT IN ({placeholders}) AND finished_at >= ? ORDER BY finished_at",
            (user_id, *ACTIVE_BIT_STATUSES, since),
        ).fetchall()
    return [_bit(row) for row in rows]


def mark_bits_reported(bit_ids: list[str]):
    with _connect() as conn:
        conn.executemany("UPDATE bits SET reported = 1 WHERE id = ?", [(i,) for i in bit_ids])


def interrupted_bits() -> list[dict]:
    """Bits that were working or paused when the server stopped."""
    statuses = (*ACTIVE_BIT_STATUSES, "paused")
    placeholders = ",".join("?" * len(statuses))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM bits WHERE status IN ({placeholders}) ORDER BY created_at", statuses
        ).fetchall()
    return [_bit(row) for row in rows]


def fail_bit(bit_id: str, error: str):
    update_bit(bit_id, status="failed", error=error, activity=None, finished_at=time.time())


def delete_bit(user_id: str, bit_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM bit_events WHERE bit_id = ?", (bit_id,))
        conn.execute("DELETE FROM bit_messages WHERE bit_id = ?", (bit_id,))
        conn.execute("DELETE FROM bits WHERE id = ? AND user_id = ?", (bit_id, user_id))


def bit_token_totals(user_id: str, since: float = 0) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS tasks, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(cached_tokens), 0) AS cached_tokens "
            "FROM bits WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ).fetchone()
    return dict(row)


MAX_EVENT_CHARS = 4000


def add_bit_event(bit_id: str, kind: str, text: str) -> None:
    if len(text) > MAX_EVENT_CHARS:
        text = f"{text[:MAX_EVENT_CHARS]}… [truncated]"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bit_events (bit_id, at, kind, text) VALUES (?, ?, ?, ?)",
            (bit_id, time.time(), kind, text),
        )


def list_bit_events(bit_id: str, after: int = 0, limit: int = 300) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, at, kind, text FROM bit_events WHERE bit_id = ? AND id > ? "
            "ORDER BY id LIMIT ?",
            (bit_id, after, limit),
        ).fetchall()
    return [dict(row) for row in rows]


# --- Bit questions and messages ---------------------------------------------------------------


def add_bit_message(bit: dict, kind: str, text: str) -> dict:
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO bit_messages (bit_id, user_id, kind, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (bit["id"], bit["user_id"], kind, text, time.time()),
        )
    return get_bit_message(cursor.lastrowid)


def _bit_message(row: sqlite3.Row) -> dict:
    message = dict(row)
    message["bit_name"] = bit_name(message.pop("slot")) if "slot" in message else None
    message["delivered"] = bool(message["delivered"])
    return message


_MESSAGE_SELECT = (
    "SELECT m.*, b.slot, b.title AS bit_title FROM bit_messages m JOIN bits b ON b.id = m.bit_id"
)


def get_bit_message(message_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(f"{_MESSAGE_SELECT} WHERE m.id = ?", (message_id,)).fetchone()
    return _bit_message(row) if row else None


def open_questions(user_id: str) -> list[dict]:
    """Questions Bits asked that the user hasn't answered yet, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            f"{_MESSAGE_SELECT} WHERE m.user_id = ? AND m.kind = 'question' "
            "AND m.answered_at IS NULL ORDER BY m.id",
            (user_id,),
        ).fetchall()
    return [_bit_message(row) for row in rows]


def open_question_for(bit_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            f"{_MESSAGE_SELECT} WHERE m.bit_id = ? AND m.kind = 'question' "
            "AND m.answered_at IS NULL ORDER BY m.id DESC LIMIT 1",
            (bit_id,),
        ).fetchone()
    return _bit_message(row) if row else None


def answer_question(message_id: int, answer: str | None):
    """Record the user's answer (or None: the question was dropped, e.g. the Bit stopped)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE bit_messages SET answer = ?, answered_at = ? WHERE id = ?",
            (answer, time.time(), message_id),
        )


def drop_open_questions(bit_id: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE bit_messages SET answered_at = ? WHERE bit_id = ? AND kind = 'question' "
            "AND answered_at IS NULL",
            (time.time(), bit_id),
        )


def undelivered_updates(user_id: str, since: float) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"{_MESSAGE_SELECT} WHERE m.user_id = ? AND m.kind = 'update' AND m.delivered = 0 "
            "AND m.created_at >= ? ORDER BY m.id",
            (user_id, since),
        ).fetchall()
    return [_bit_message(row) for row in rows]


def mark_messages_delivered(message_ids: list[int]):
    with _connect() as conn:
        conn.executemany(
            "UPDATE bit_messages SET delivered = 1 WHERE id = ?", [(i,) for i in message_ids]
        )


def list_bit_messages(bit_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"{_MESSAGE_SELECT} WHERE m.bit_id = ? ORDER BY m.id", (bit_id,)
        ).fetchall()
    return [_bit_message(row) for row in rows]


# --- Toolbox -----------------------------------------------------------------------------------


def _tool(row: sqlite3.Row) -> dict:
    tool = dict(row)
    tool["files"] = json.loads(tool["files"])
    return tool


def list_tools(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bit_tools WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()
    return [_tool(row) for row in rows]


def get_tool(user_id: str, name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bit_tools WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()
    return _tool(row) if row else None


def put_tool(user_id: str, *, name: str, description: str, usage: str, files: list[str],
             made_by: str, bit_id: str):
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bit_tools (user_id, name, description, usage, files, made_by, bit_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET description = excluded.description, "
            "usage = excluded.usage, files = excluded.files, made_by = excluded.made_by, "
            "bit_id = excluded.bit_id, updated_at = excluded.updated_at",
            (user_id, name, description, usage, json.dumps(files), made_by, bit_id, now, now),
        )
    return get_tool(user_id, name)


def count_tool_use(user_id: str, names: list[str]):
    with _connect() as conn:
        conn.executemany(
            "UPDATE bit_tools SET uses = uses + 1 WHERE user_id = ? AND name = ?",
            [(user_id, name) for name in names],
        )


def delete_tool(user_id: str, name: str):
    with _connect() as conn:
        conn.execute("DELETE FROM bit_tools WHERE user_id = ? AND name = ?", (user_id, name))


# --- Shared files ------------------------------------------------------------------------------


def get_shared_file(user_id: str, name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM shared_files WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()
    return dict(row) if row else None


def put_shared_file(user_id: str, *, name: str, bit_name: str, bit_id: str, size: int):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO shared_files (user_id, name, bit_name, bit_id, size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, name) DO UPDATE SET "
            "bit_name = excluded.bit_name, bit_id = excluded.bit_id, size = excluded.size, "
            "created_at = excluded.created_at",
            (user_id, name, bit_name, bit_id, size, time.time()),
        )


def list_shared_files(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, bit_name, bit_id, size, created_at FROM shared_files "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_shared_file(user_id: str, name: str):
    with _connect() as conn:
        conn.execute("DELETE FROM shared_files WHERE user_id = ? AND name = ?", (user_id, name))


# --- Memories ----------------------------------------------------------------------------------


def list_memories(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, text, source, created_at, updated_at FROM memories "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_memory(user_id: str, memory_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, kind, text, source, created_at, updated_at FROM memories "
            "WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def add_memory(user_id: str, *, kind: str, text: str, source: str) -> dict:
    memory_id = _new_id()
    now = time.time()
    kind = kind if kind in MEMORY_KINDS else "other"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO memories (id, user_id, kind, text, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, user_id, kind, text.strip(), source, now, now),
        )
    return get_memory(user_id, memory_id)


def update_memory(user_id: str, memory_id: str, *, text: str | None = None,
                  kind: str | None = None) -> dict | None:
    with _connect() as conn:
        if text is not None:
            conn.execute(
                "UPDATE memories SET text = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (text.strip(), time.time(), memory_id, user_id),
            )
        if kind is not None and kind in MEMORY_KINDS:
            conn.execute(
                "UPDATE memories SET kind = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (kind, time.time(), memory_id, user_id),
            )
    return get_memory(user_id, memory_id)


def delete_memory(user_id: str, memory_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))


def delete_all_memories(user_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2]


def search_memories(user_id: str, query: str, limit: int = 8) -> list[dict]:
    """Memories ranked by how many of the query's words they contain."""
    query_words = set(words(query))
    scored = []
    for memory in list_memories(user_id):
        score = len(query_words & set(words(memory["text"])))
        if score:
            scored.append((score, memory["updated_at"], memory))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [memory for _, _, memory in scored[:limit]]


# --- Conversations -----------------------------------------------------------------------------


def start_conversation(user_id: str) -> str:
    conversation_id = _new_id()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, user_id, started_at) VALUES (?, ?, ?)",
            (conversation_id, user_id, time.time()),
        )
    return conversation_id


def end_conversation(conversation_id: str, messages: list[dict]):
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET ended_at = ?, messages = ? WHERE id = ?",
            (time.time(), json.dumps(messages), conversation_id),
        )


def set_conversation_summary(conversation_id: str, *, title: str | None, summary: str | None):
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, summary = ? WHERE id = ?",
            (title, summary, conversation_id),
        )


def list_conversations(user_id: str, limit: int = 50) -> list[dict]:
    """Past conversations that had something said in them, newest first, without messages."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, started_at, ended_at, title, summary, "
            "json_array_length(messages) AS message_count FROM conversations "
            "WHERE user_id = ? AND json_array_length(messages) > 0 "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(user_id: str, conversation_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
    if not row:
        return None
    conversation = dict(row)
    conversation["messages"] = json.loads(conversation["messages"])
    conversation.pop("user_id")
    return conversation


def delete_conversation(user_id: str, conversation_id: str):
    with _connect() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        )


def recent_conversation_summaries(user_id: str, limit: int = 5) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT started_at, title, summary FROM conversations "
            "WHERE user_id = ? AND summary IS NOT NULL ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def prune_empty_conversations(user_id: str):
    with _connect() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE user_id = ? AND ended_at IS NOT NULL "
            "AND json_array_length(messages) = 0",
            (user_id,),
        )
