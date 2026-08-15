from typing import NamedTuple

from . import cards as cards_module


class Assets(NamedTuple):
    user_id: str
    balance: int
    stocks: int
    cards: int

    @property
    def total(self) -> int:
        return self.balance + self.stocks + self.cards


def _rarity_price_case(column: str) -> str:
    """희귀도를 판매가로 바꾸는 SQL CASE 식.

    희귀도 값은 코드 안의 상수라서 문자열로 끼워 넣어도 안전하다.
    """
    whens = " ".join(
        f"WHEN '{rarity}' THEN {price}" for rarity, price in cards_module.RARITY_PRICE.items()
    )
    return f"CASE {column} {whens} ELSE 0 END"


def card_values(conn) -> dict[str, int]:
    """유저별 카드 가치. 시장에 올려둔 카드도 아직 본인 자산으로 친다."""
    case = _rarity_price_case("c.rarity")
    rows = conn.execute(
        f"""
        SELECT user_id, SUM(quantity * unit_price) AS total
        FROM (
            SELECT i.user_id AS user_id, i.quantity AS quantity, {case} AS unit_price
            FROM inventory i
            JOIN cards c ON c.id = i.card_id
            WHERE i.quantity > 0
            UNION ALL
            SELECT m.seller_id AS user_id, m.quantity AS quantity, {case} AS unit_price
            FROM market m
            JOIN cards c ON c.id = m.card_id
        )
        GROUP BY user_id
        """
    ).fetchall()
    return {row["user_id"]: row["total"] for row in rows}


def stock_values(conn) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT h.user_id, SUM(h.quantity * s.price) AS total
        FROM stock_holding h
        JOIN stock s ON s.name = h.stock_name
        WHERE h.quantity > 0
        GROUP BY h.user_id
        """
    ).fetchall()
    return {row["user_id"]: row["total"] for row in rows}


def balances(conn) -> dict[str, int]:
    rows = conn.execute("SELECT user_id, balance FROM wallet").fetchall()
    return {row["user_id"]: row["balance"] for row in rows}


def all_assets(conn) -> list[Assets]:
    """모든 유저의 자산을 많은 순으로 돌려줍니다."""
    balance_map = balances(conn)
    stock_map = stock_values(conn)
    card_map = card_values(conn)

    user_ids = set(balance_map) | set(stock_map) | set(card_map)
    rows = [
        Assets(
            user_id=user_id,
            balance=balance_map.get(user_id, 0),
            stocks=stock_map.get(user_id, 0),
            cards=card_map.get(user_id, 0),
        )
        for user_id in user_ids
    ]
    # 동점이면 순서가 매번 흔들리지 않게 user_id로 한 번 더 정렬한다.
    return sorted(rows, key=lambda a: (-a.total, a.user_id))


def get_assets(conn, user_id: str) -> Assets:
    balance_row = conn.execute(
        "SELECT balance FROM wallet WHERE user_id = ?", (user_id,)
    ).fetchone()
    stock_row = conn.execute(
        """
        SELECT COALESCE(SUM(h.quantity * s.price), 0) AS total
        FROM stock_holding h
        JOIN stock s ON s.name = h.stock_name
        WHERE h.user_id = ? AND h.quantity > 0
        """,
        (user_id,),
    ).fetchone()

    case = _rarity_price_case("c.rarity")
    card_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(quantity * unit_price), 0) AS total
        FROM (
            SELECT i.quantity AS quantity, {case} AS unit_price
            FROM inventory i
            JOIN cards c ON c.id = i.card_id
            WHERE i.user_id = ? AND i.quantity > 0
            UNION ALL
            SELECT m.quantity AS quantity, {case} AS unit_price
            FROM market m
            JOIN cards c ON c.id = m.card_id
            WHERE m.seller_id = ?
        )
        """,
        (user_id, user_id),
    ).fetchone()

    return Assets(
        user_id=user_id,
        balance=balance_row["balance"] if balance_row else 0,
        stocks=stock_row["total"],
        cards=card_row["total"],
    )
