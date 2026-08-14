import random
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

BASE_PRICES = {
    "크리퍼": 300,
    "좀비": 100,
    "마녀": 500,
    "거미": 200,
    "스켈레톤": 400,
}
STOCK_EMOJI = {
    "크리퍼": "🟩",
    "좀비": "🧟",
    "마녀": "🧙",
    "거미": "🕷️",
    "스켈레톤": "💀",
}
STOCK_NAMES = list(BASE_PRICES)

# 10초마다 -10~11달러만큼 변동, 단 한국시간 12시~24시에만.
TICK_SECONDS = 10
DELTA_MIN = -10
DELTA_MAX = 11
TRADING_START_HOUR = 12

# 1초마다 보유 주식 평가액의 0.00001%가 배당금으로 쌓임.
DIVIDEND_RATE_PER_SECOND = 0.00001 / 100


def sync_stocks(conn) -> None:
    for name, price in BASE_PRICES.items():
        conn.execute(
            "INSERT INTO stock (name, price, prev_price) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (name, price, price),
        )


def is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    return now.hour >= TRADING_START_HOUR


def get_stocks(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM stock ORDER BY price DESC").fetchall()


def get_stock(conn, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM stock WHERE name = ?", (name,)).fetchone()


def tick_prices(conn) -> list[str]:
    """가격을 한 번 변동시키고, 상장폐지된 종목 이름을 돌려줍니다."""
    crashed = []
    for row in conn.execute("SELECT name, price FROM stock").fetchall():
        new_price = row["price"] + random.randint(DELTA_MIN, DELTA_MAX)
        if new_price <= 0:
            # 0달러 이하로 떨어지면 모두가 그 주식을 잃고 기본값으로 초기화된다.
            conn.execute("DELETE FROM stock_holding WHERE stock_name = ?", (row["name"],))
            new_price = BASE_PRICES[row["name"]]
            crashed.append(row["name"])
        conn.execute(
            "UPDATE stock SET price = ?, prev_price = ? WHERE name = ?",
            (new_price, row["price"], row["name"]),
        )
    return crashed


def accrue_dividends(conn, seconds: int) -> None:
    conn.execute(
        """
        INSERT INTO dividend (user_id, accrued)
        SELECT h.user_id, SUM(s.price * h.quantity) * ?
        FROM stock_holding h
        JOIN stock s ON s.name = h.stock_name
        WHERE h.quantity > 0
        GROUP BY h.user_id
        ON CONFLICT(user_id) DO UPDATE SET accrued = accrued + excluded.accrued
        """,
        (DIVIDEND_RATE_PER_SECOND * seconds,),
    )


def get_accrued_dividend(conn, user_id: str) -> float:
    row = conn.execute("SELECT accrued FROM dividend WHERE user_id = ?", (user_id,)).fetchone()
    return row["accrued"] if row else 0.0


def claim_dividend(conn, user_id: str) -> int:
    """정수 부분만 지급하고 소수점 아래는 누적해 둡니다."""
    accrued = get_accrued_dividend(conn, user_id)
    payout = int(accrued)
    if payout > 0:
        conn.execute(
            "UPDATE dividend SET accrued = accrued - ? WHERE user_id = ?", (payout, user_id)
        )
    return payout


def get_holdings(conn, user_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT h.stock_name, h.quantity, h.total_cost, s.price
        FROM stock_holding h
        JOIN stock s ON s.name = h.stock_name
        WHERE h.user_id = ? AND h.quantity > 0
        ORDER BY s.price DESC
        """,
        (user_id,),
    ).fetchall()


def profit_and_rate(value: float, total_cost: float) -> tuple[float, float | None]:
    """(손익 금액, 수익률 %). 산 적이 없는(전량 무료) 주식은 수익률을 낼 수 없어 None."""
    profit = value - total_cost
    rate = profit / total_cost * 100 if total_cost > 0 else None
    return profit, rate


def get_quantity(conn, user_id: str, name: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM stock_holding WHERE user_id = ? AND stock_name = ?",
        (user_id, name),
    ).fetchone()
    return row["quantity"] if row else 0


def add_holding(conn, user_id: str, name: str, quantity: int, cost: float = 0) -> None:
    """cost는 이번 매수에 실제로 쓴 금액. (개미 혜택으로 받은 무료 주식은 0)"""
    conn.execute(
        """
        INSERT INTO stock_holding (user_id, stock_name, quantity, total_cost)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, stock_name) DO UPDATE SET
            quantity = quantity + excluded.quantity,
            total_cost = total_cost + excluded.total_cost
        """,
        (user_id, name, quantity, cost),
    )


def remove_holding(conn, user_id: str, name: str, quantity: int) -> None:
    """평균 단가법: 판 수량만큼 매수 원가도 비례해서 덜어낸다."""
    conn.execute(
        """
        UPDATE stock_holding
        SET total_cost = CASE
                WHEN quantity - ? <= 0 THEN 0
                ELSE total_cost * (quantity - ?) / quantity
            END,
            quantity = quantity - ?
        WHERE user_id = ? AND stock_name = ?
        """,
        (quantity, quantity, quantity, user_id, name),
    )
