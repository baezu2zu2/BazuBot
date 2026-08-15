import json
import random
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "cards"

RARITY_ORDER = ["common", "uncommon", "rare", "legendary"]
RARITY_LABEL = {
    "common": "일반",
    "uncommon": "고급",
    "rare": "희귀",
    "legendary": "전설",
}
RARITY_EMOJI = {
    "common": "⚪",
    "uncommon": "🟢",
    "rare": "🔵",
    "legendary": "🟡",
}
RARITY_WEIGHTS = {
    "common": 60,
    "uncommon": 25,
    "rare": 12,
    "legendary": 3,
}
RARITY_PRICE = {
    "common": 60,
    "uncommon": 150,
    "rare": 450,
    "legendary": 1500,
}

UNIQUE_CARD_NAMES = {"드래곤 알", "명령 블록", "반복형 명령 블록", "연쇄형 명령 블록"}

# 카드 판매가는 1분마다 -50~51달러만큼 움직이고, 1시간마다 기본가로 돌아간다. (희귀도 단위)
CARD_TICK_MINUTES = 1
CARD_DELTA_MIN = -50
CARD_DELTA_MAX = 51
CARD_RESET_HOURS = 1


def sync_card_prices(conn) -> None:
    for rarity, price in RARITY_PRICE.items():
        conn.execute(
            "INSERT INTO card_price (rarity, price, prev_price, base_price) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(rarity) DO NOTHING",
            (rarity, price, price, price),
        )


def reset_card_prices(conn) -> None:
    """카드 가격을 전부 기본가로 되돌린다. (SET 오른쪽은 갱신 전 값으로 계산된다)"""
    conn.execute("UPDATE card_price SET prev_price = price, price = base_price")


def get_card_price_rows(conn) -> list[sqlite3.Row]:
    """희귀도 순서대로 카드 시세를 돌려줍니다."""
    rows = conn.execute("SELECT * FROM card_price").fetchall()
    return sorted(rows, key=lambda row: RARITY_ORDER.index(row["rarity"]))


def get_card_prices(conn) -> dict[str, int]:
    rows = conn.execute("SELECT rarity, price FROM card_price").fetchall()
    return {row["rarity"]: row["price"] for row in rows}


def get_card_price(conn, rarity: str) -> int:
    row = conn.execute("SELECT price FROM card_price WHERE rarity = ?", (rarity,)).fetchone()
    return row["price"] if row else RARITY_PRICE[rarity]


def tick_card_prices(conn) -> list[str]:
    """카드 가격을 한 번 변동시키고, 0 이하로 떨어져 초기화된 희귀도를 돌려줍니다."""
    reset = []
    for row in conn.execute("SELECT rarity, price, base_price FROM card_price").fetchall():
        new_price = row["price"] + random.randint(CARD_DELTA_MIN, CARD_DELTA_MAX)
        if new_price <= 0:
            # 주식과 같은 규칙: 0달러 이하로 떨어지면 기본가로 되돌린다.
            new_price = row["base_price"]
            reset.append(row["rarity"])
        conn.execute(
            "UPDATE card_price SET price = ?, prev_price = ? WHERE rarity = ?",
            (new_price, row["price"], row["rarity"]),
        )
    return reset


def load_card_files() -> list[dict]:
    parsed = []
    for path in sorted(DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for card in data["cards"]:
            parsed.append(
                {
                    "name": card["name"],
                    "category": card["category"],
                    "rarity": card["rarity"],
                }
            )
    return parsed


def sync_cards(conn) -> None:
    for card in load_card_files():
        conn.execute(
            """
            INSERT INTO cards (name, category, rarity)
            VALUES (:name, :category, :rarity)
            ON CONFLICT(name) DO UPDATE SET
                category = excluded.category,
                rarity = excluded.rarity
            """,
            card,
        )


def get_categories(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT category FROM cards ORDER BY category").fetchall()
    return [row["category"] for row in rows]


def get_all_cards(conn, category: str | None = None) -> list[sqlite3.Row]:
    if category:
        rows = conn.execute(
            "SELECT * FROM cards WHERE category = ? ORDER BY category, name", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards ORDER BY category, name").fetchall()
    return sorted(rows, key=lambda r: RARITY_ORDER.index(r["rarity"]))


def get_claimed_unique_card_ids(conn) -> set[int]:
    """DB가 서버별로 분리되어 있으므로 이 서버에서 이미 뽑힌 유니크 카드만 돌려준다."""
    rows = conn.execute("SELECT card_id FROM unique_card_claim").fetchall()
    return {row["card_id"] for row in rows}


def claim_unique_card(conn, card_id: int, holder_id: str) -> None:
    conn.execute(
        """
        INSERT INTO unique_card_claim (card_id, holder_id)
        VALUES (?, ?)
        ON CONFLICT(card_id) DO NOTHING
        """,
        (card_id, holder_id),
    )


def pick_random_card(
    conn, category: str | None = None, exclude_ids: set[int] | None = None
) -> sqlite3.Row | None:
    if category:
        rows = conn.execute("SELECT * FROM cards WHERE category = ?", (category,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards").fetchall()
    if exclude_ids:
        rows = [row for row in rows if row["id"] not in exclude_ids]
    if not rows:
        return None

    by_rarity: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_rarity.setdefault(row["rarity"], []).append(row)

    rarities = list(by_rarity.keys())
    weights = [RARITY_WEIGHTS[r] for r in rarities]
    rarity = random.choices(rarities, weights=weights, k=1)[0]
    return random.choice(by_rarity[rarity])


def add_card(conn, user_id: str, card_id: int, quantity: int = 1) -> None:
    conn.execute(
        """
        INSERT INTO inventory (user_id, card_id, quantity)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + excluded.quantity
        """,
        (user_id, card_id, quantity),
    )


def remove_card(conn, user_id: str, card_id: int, quantity: int = 1) -> None:
    conn.execute(
        "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?",
        (quantity, user_id, card_id),
    )


def get_quantity(conn, user_id: str, card_id: int) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, card_id)
    ).fetchone()
    return row["quantity"] if row else 0


def get_card_by_name(conn, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cards WHERE name = ?", (name,)).fetchone()
