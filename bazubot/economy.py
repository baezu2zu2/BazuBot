import random
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

STARTING_BALANCE = 500
GACHA_COST = 300
COLORS = ["빨강", "파랑"]
COLOR_EMOJI = {"빨강": "🔴", "파랑": "🔵"}

JOBS = ["회사원", "프리랜서", "사업가", "카드 트레이더", "개미"]
JOB_EMOJI = {
    "회사원": "🏢",
    "프리랜서": "💻",
    "사업가": "💼",
    "카드 트레이더": "🃏",
    "개미": "🐜",
}
JOB_DESCRIPTION = {
    "회사원": "매일 1,000달러",
    "프리랜서": "매일 500~1,500달러",
    "사업가": "매일 0~2,000달러",
    "카드 트레이더": "매일 카드 5장 무료 뽑기",
    "개미": "매일 주식 3주 무료 구매",
}

# 급여 대신 매일 초기화되는 무료 혜택을 받는 직업.
PERK_JOBS = ("카드 트레이더", "개미")
FREE_CARD_DRAWS = 5
FREE_STOCK_BUYS = 3


def ensure_wallet(conn, user_id: str) -> int:
    conn.execute(
        "INSERT INTO wallet (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
        (user_id, STARTING_BALANCE),
    )
    row = conn.execute("SELECT balance FROM wallet WHERE user_id = ?", (user_id,)).fetchone()
    return row["balance"]


def set_balance(conn, user_id: str, balance: int) -> None:
    conn.execute("UPDATE wallet SET balance = ? WHERE user_id = ?", (balance, user_id))


def spin() -> str:
    return random.choice(COLORS)


def set_job(conn, user_id: str, job: str) -> None:
    conn.execute(
        "INSERT INTO job (user_id, job) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET job = excluded.job",
        (user_id, job),
    )


def get_job(conn, user_id: str) -> str | None:
    row = conn.execute("SELECT job FROM job WHERE user_id = ?", (user_id,)).fetchone()
    return row["job"] if row else None


def pay_for_job(job: str) -> int:
    if job == "회사원":
        return 1000
    if job == "프리랜서":
        return random.randint(500, 1500)
    if job == "사업가":
        return random.randint(0, 2000)
    if job in PERK_JOBS:
        return 0
    raise ValueError(f"알 수 없는 직업: {job}")


def job_benefit_note(job: str) -> str:
    if job in PERK_JOBS:
        return "혜택은 매일 한국시간 0시에 초기화돼요."
    return "매일 한국시간 오전 7시에 급여가 지급돼요."


def _today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def _get_perk_row(conn, user_id: str):
    """오늘 날짜(한국시간) 기준으로 사용량을 초기화한 뒤 돌려줍니다."""
    today = _today_kst()
    conn.execute(
        "INSERT INTO job_perk (user_id, perk_date) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO NOTHING",
        (user_id, today),
    )
    conn.execute(
        "UPDATE job_perk SET perk_date = ?, card_draws_used = 0, stock_buys_used = 0 "
        "WHERE user_id = ? AND perk_date <> ?",
        (today, user_id, today),
    )
    return conn.execute("SELECT * FROM job_perk WHERE user_id = ?", (user_id,)).fetchone()


def free_card_draws_left(conn, user_id: str) -> int:
    if get_job(conn, user_id) != "카드 트레이더":
        return 0
    row = _get_perk_row(conn, user_id)
    return max(0, FREE_CARD_DRAWS - row["card_draws_used"])


def use_free_card_draws(conn, user_id: str, count: int = 1) -> int:
    """실제로 사용한 무료 뽑기 횟수를 돌려줍니다."""
    used = min(count, free_card_draws_left(conn, user_id))
    if used > 0:
        conn.execute(
            "UPDATE job_perk SET card_draws_used = card_draws_used + ? WHERE user_id = ?",
            (used, user_id),
        )
    return used


def free_stock_buys_left(conn, user_id: str) -> int:
    if get_job(conn, user_id) != "개미":
        return 0
    row = _get_perk_row(conn, user_id)
    return max(0, FREE_STOCK_BUYS - row["stock_buys_used"])


def use_free_stock_buys(conn, user_id: str, count: int = 1) -> int:
    """실제로 사용한 무료 구매 주식 수를 돌려줍니다."""
    used = min(count, free_stock_buys_left(conn, user_id))
    if used > 0:
        conn.execute(
            "UPDATE job_perk SET stock_buys_used = stock_buys_used + ? WHERE user_id = ?",
            (used, user_id),
        )
    return used
