import sqlite3
import os
from pathlib import Path

BACKEND_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = str(BACKEND_DIR / "conversations.db")
MAX_TURNS = 5


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT NOT NULL,
            msg_role  TEXT NOT NULL,
            content   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.commit()
    return c


# Initialise table on import
_conn().close()


def get_history(username: str) -> list:
    """Returns last MAX_TURNS turns as Gemini-compatible history dicts."""
    c = _conn()
    rows = c.execute(
        """SELECT msg_role, content FROM history
           WHERE username = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (username, MAX_TURNS * 2),
    ).fetchall()
    c.close()
    return [{"role": r[0], "parts": [r[1]]} for r in reversed(rows)]


def save_turn(username: str, user_message: str, bot_answer: str):
    """Persists one user+model exchange."""
    c = _conn()
    c.execute(
        "INSERT INTO history (username, msg_role, content) VALUES (?, 'user', ?)",
        (username, user_message),
    )
    c.execute(
        "INSERT INTO history (username, msg_role, content) VALUES (?, 'model', ?)",
        (username, bot_answer),
    )
    c.commit()
    c.close()
