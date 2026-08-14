import sqlite3

LISTING_QUERY = """
    SELECT m.id, m.seller_id, m.quantity, m.price,
           c.id AS card_id, c.name, c.category, c.rarity
    FROM market m
    JOIN cards c ON c.id = m.card_id
"""


def create_listing(conn, seller_id: str, card_id: int, quantity: int, price: int) -> int:
    cursor = conn.execute(
        "INSERT INTO market (seller_id, card_id, quantity, price) VALUES (?, ?, ?, ?)",
        (seller_id, card_id, quantity, price),
    )
    return cursor.lastrowid


def get_listings(conn) -> list[sqlite3.Row]:
    return conn.execute(LISTING_QUERY + " ORDER BY m.id").fetchall()


def get_listing(conn, listing_id: int) -> sqlite3.Row | None:
    return conn.execute(LISTING_QUERY + " WHERE m.id = ?", (listing_id,)).fetchone()


def update_quantity(conn, listing_id: int, quantity: int) -> None:
    conn.execute("UPDATE market SET quantity = ? WHERE id = ?", (quantity, listing_id))


def remove_listing(conn, listing_id: int) -> None:
    conn.execute("DELETE FROM market WHERE id = ?", (listing_id,))
