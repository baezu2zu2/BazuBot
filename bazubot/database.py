import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "bazubot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _has_legacy_tab_column(conn) -> bool:
    cols = conn.execute("PRAGMA table_info(cards)").fetchall()
    return any(c["name"] == "tab" for c in cols)


def init_db() -> None:
    with get_db() as conn:
        if _has_legacy_tab_column(conn):
            conn.execute("DROP TABLE IF EXISTS market")
            conn.execute("DROP TABLE IF EXISTS inventory")
            conn.execute("DROP TABLE IF EXISTS cards")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                rarity TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                user_id TEXT NOT NULL,
                card_id INTEGER NOT NULL REFERENCES cards(id),
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, card_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 500
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job (
                user_id TEXT PRIMARY KEY,
                job TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id TEXT NOT NULL,
                card_id INTEGER NOT NULL REFERENCES cards(id),
                quantity INTEGER NOT NULL,
                price INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unique_card_claim (
                card_id INTEGER NOT NULL REFERENCES cards(id),
                guild_id TEXT NOT NULL,
                holder_id TEXT NOT NULL,
                PRIMARY KEY (card_id, guild_id)
            )
            """
        )
