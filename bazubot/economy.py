import random

STARTING_BALANCE = 500
GACHA_COST = 300
COLORS = ["빨강", "파랑"]
COLOR_EMOJI = {"빨강": "🔴", "파랑": "🔵"}

JOBS = ["회사원", "프리랜서", "사업가"]
JOB_EMOJI = {"회사원": "🏢", "프리랜서": "💻", "사업가": "💼"}
JOB_DESCRIPTION = {
    "회사원": "매일 1,000달러",
    "프리랜서": "매일 500~1,500달러",
    "사업가": "매일 0~2,000달러",
}


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
    raise ValueError(f"알 수 없는 직업: {job}")
