"""SQLite storage for conversations, messages, and every claim the model makes.

The `claims` table is the running record of unsupported claims: in Milestone 1
every row has source = NULL. Milestone 2 fills it in.
"""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).resolve().parent.parent / "data" / "nutrition.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    response_json TEXT,
    declined INTEGER NOT NULL DEFAULT 0,
    decline_category TEXT,
    guard_stage TEXT,
    guard_pattern TEXT,
    raw_model_output TEXT,
    model TEXT,
    prompt_version TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    text TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_or_create_conversation(conversation_id: Optional[str]) -> str:
    with connect() as conn:
        if conversation_id:
            row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row:
                return row["id"]
        new_id = str(uuid.uuid4())
        conn.execute("INSERT INTO conversations (id, created_at) VALUES (?, ?)", (new_id, _now()))
        return new_id


def get_conversation(conversation_id: str) -> Optional[dict]:
    with connect() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conv:
            return None
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        ).fetchall()
        return {"conversation": dict(conv), "messages": [dict(r) for r in rows]}


def history(conversation_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content, declined, decline_category FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_user_message(conversation_id: str, content: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
            (conversation_id, content, _now()),
        )
        return cur.lastrowid


def add_assistant_message(
    conversation_id: str,
    response: dict,
    *,
    declined: bool,
    decline_category: Optional[str],
    guard_stage: Optional[str],
    guard_pattern: Optional[str],
    raw_model_output: Optional[str],
    model: Optional[str],
    prompt_version: str,
) -> int:
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, response_json, declined, decline_category,
                   guard_stage, guard_pattern, raw_model_output, model, prompt_version, created_at)
               VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                response["answer"],
                json.dumps(response),
                int(declined),
                decline_category,
                guard_stage,
                guard_pattern,
                raw_model_output,
                model,
                prompt_version,
                now,
            ),
        )
        message_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO claims (message_id, text, source, created_at) VALUES (?, ?, ?, ?)",
            [(message_id, c["text"], c["source"], now) for c in response["claims"]],
        )
        return message_id


def unsupported_claims(limit: int = 200) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.id, c.text, c.source, c.created_at, m.id AS message_id, m.conversation_id,
                      (SELECT u.content FROM messages u WHERE u.conversation_id = m.conversation_id
                         AND u.id < m.id AND u.role = 'user' ORDER BY u.id DESC LIMIT 1) AS question
               FROM claims c JOIN messages m ON m.id = c.message_id
               WHERE c.source IS NULL ORDER BY c.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
