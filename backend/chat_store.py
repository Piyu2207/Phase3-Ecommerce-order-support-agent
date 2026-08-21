from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHAT_DB = DATA_DIR / "chat_history.sqlite"


# ============================================================
# DATABASE
# ============================================================


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(CHAT_DB),
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def init_chat_store() -> None:
    """
    Initialize the single shared chat database.

    Database location:

        project_root/data/chat_history.sqlite
    """

    conn = get_connection()

    # --------------------------------------------------------
    # Conversations
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            thread_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT 'New chat',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

    # --------------------------------------------------------
    # Backward-compatible migration
    # --------------------------------------------------------

    conversation_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
    }

    if "user_id" not in conversation_columns:
        conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN user_id TEXT NOT NULL DEFAULT ''
            """)

    if "title" not in conversation_columns:
        conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN title TEXT NOT NULL DEFAULT 'New chat'
            """)

    if "created_at" not in conversation_columns:
        conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN created_at TEXT
            """)

    if "updated_at" not in conversation_columns:
        conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN updated_at TEXT
            """)

    # --------------------------------------------------------
    # Messages
    # --------------------------------------------------------

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(thread_id)
                REFERENCES conversations(thread_id)
                ON DELETE CASCADE
        )
        """)

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_conversations_user_updated
        ON conversations(user_id, updated_at DESC)
        """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_messages_thread
        ON messages(thread_id, id)
        """)

    conn.commit()
    conn.close()


# Initialize database when imported.
init_chat_store()


# ============================================================
# CONVERSATIONS
# ============================================================


def create_conversation(
    thread_id: str,
    user_id: str,
    title: str = "New chat",
) -> None:
    """
    Create a new conversation.

    IMPORTANT:
    Existing conversations are never overwritten.
    """

    conn = get_connection()

    conn.execute(
        """
        INSERT OR IGNORE INTO conversations
        (
            thread_id,
            user_id,
            title
        )
        VALUES (?, ?, ?)
        """,
        (
            thread_id,
            user_id,
            title.strip()[:100] or "New chat",
        ),
    )

    conn.commit()
    conn.close()


def conversation_exists(
    thread_id: str,
    user_id: str,
) -> bool:
    conn = get_connection()

    row = conn.execute(
        """
        SELECT 1
        FROM conversations
        WHERE thread_id = ?
          AND user_id = ?
        LIMIT 1
        """,
        (
            thread_id,
            user_id,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def update_conversation_title(
    thread_id: str,
    user_id: str,
    title: str,
) -> None:
    conn = get_connection()

    conn.execute(
        """
        UPDATE conversations
        SET
            title = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            title.strip()[:100] or "New chat",
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def touch_conversation(
    thread_id: str,
    user_id: str,
) -> None:
    conn = get_connection()

    conn.execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def get_conversations(
    user_id: str,
) -> list[dict[str, Any]]:
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            thread_id,
            user_id,
            title,
            created_at,
            updated_at
        FROM conversations
        WHERE user_id = ?
        ORDER BY
            updated_at DESC,
            created_at DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


# ============================================================
# MESSAGES
# ============================================================


def save_message(
    thread_id: str,
    user_id: str,
    role: str,
    content: str,
    result: dict[str, Any] | None = None,
) -> int:
    """
    Save a message to the specified conversation.

    Returns:
        Database message ID.
    """

    if not conversation_exists(
        thread_id,
        user_id,
    ):
        raise PermissionError(
            "This conversation does not belong " "to the signed-in user."
        )

    result_json = None

    if result is not None:
        try:
            result_json = json.dumps(
                result,
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            result_json = "{}"

    conn = get_connection()

    cursor = conn.execute(
        """
        INSERT INTO messages
        (
            thread_id,
            role,
            content,
            result_json
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            thread_id,
            role,
            content,
            result_json,
        ),
    )

    message_id = int(cursor.lastrowid)

    conn.execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()

    return message_id


def update_message(
    message_id: int,
    thread_id: str,
    user_id: str,
    content: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """
    Update one exact message.

    This is safer than:
        UPDATE latest assistant message
    """

    if not conversation_exists(
        thread_id,
        user_id,
    ):
        raise PermissionError(
            "This conversation does not belong " "to the signed-in user."
        )

    result_json = None

    if result is not None:
        result_json = json.dumps(
            result,
            ensure_ascii=False,
        )

    conn = get_connection()

    cursor = conn.execute(
        """
        UPDATE messages
        SET
            content = ?,
            result_json = ?
        WHERE id = ?
          AND thread_id = ?
        """,
        (
            content,
            result_json,
            message_id,
            thread_id,
        ),
    )

    conn.execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()

    updated = cursor.rowcount > 0

    conn.close()

    return updated


def update_last_assistant_message(
    thread_id: str,
    user_id: str,
    content: str,
    result: dict[str, Any] | None = None,
) -> int | None:
    """
    Backward-compatible helper.

    Prefer update_message() when the exact message ID
    is available.
    """

    if not conversation_exists(
        thread_id,
        user_id,
    ):
        raise PermissionError(
            "This conversation does not belong " "to the signed-in user."
        )

    conn = get_connection()

    row = conn.execute(
        """
        SELECT id
        FROM messages
        WHERE thread_id = ?
          AND role = 'assistant'
        ORDER BY id DESC
        LIMIT 1
        """,
        (thread_id,),
    ).fetchone()

    if row is None:
        conn.close()
        return None

    message_id = int(row["id"])

    result_json = None

    if result is not None:
        result_json = json.dumps(
            result,
            ensure_ascii=False,
        )

    conn.execute(
        """
        UPDATE messages
        SET
            content = ?,
            result_json = ?
        WHERE id = ?
          AND thread_id = ?
        """,
        (
            content,
            result_json,
            message_id,
            thread_id,
        ),
    )

    conn.execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()

    return message_id


# ============================================================
# LOAD MESSAGES
# ============================================================


def get_messages(
    thread_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """
    Load messages only when the thread belongs
    to the requested customer.
    """

    if not conversation_exists(
        thread_id,
        user_id,
    ):
        return []

    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            id,
            role,
            content,
            result_json,
            created_at
        FROM messages
        WHERE thread_id = ?
        ORDER BY id ASC
        """,
        (thread_id,),
    ).fetchall()

    conn.close()

    messages: list[dict[str, Any]] = []

    for row in rows:
        result: dict[str, Any] = {}

        if row["result_json"]:
            try:
                parsed = json.loads(row["result_json"])

                if isinstance(parsed, dict):
                    result = parsed

            except (
                json.JSONDecodeError,
                TypeError,
            ):
                result = {}

        messages.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "result": result,
                "created_at": row["created_at"],
            }
        )

    return messages


# ============================================================
# DELETE / CLEAR
# ============================================================


def clear_messages(
    thread_id: str,
    user_id: str,
) -> None:
    """
    Clear messages but keep the conversation.
    """

    if not conversation_exists(
        thread_id,
        user_id,
    ):
        return

    conn = get_connection()

    conn.execute(
        """
        DELETE FROM messages
        WHERE thread_id = ?
        """,
        (thread_id,),
    )

    conn.execute(
        """
        UPDATE conversations
        SET
            title = 'New chat',
            updated_at = CURRENT_TIMESTAMP
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def delete_conversation(
    thread_id: str,
    user_id: str,
) -> None:
    """
    Delete ONLY the specified user's conversation.
    """

    conn = get_connection()

    conn.execute(
        """
        DELETE FROM conversations
        WHERE thread_id = ?
          AND user_id = ?
        """,
        (
            thread_id,
            user_id,
        ),
    )

    conn.commit()
    conn.close()
