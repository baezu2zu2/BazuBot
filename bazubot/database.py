import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_DIR = ROOT_DIR / "data" / "db"

# 서버별로 나누기 전에 쓰던 단일 DB. 아래 환경변수의 서버로 한 번만 옮겨진다.
LEGACY_DB_PATH = ROOT_DIR / "bazubot.db"


def _legacy_db_guild_id() -> str:
    # load_dotenv()보다 임포트가 먼저 일어날 수 있어 매번 환경변수를 읽는다.
    return os.getenv("LEGACY_DB_GUILD_ID") or os.getenv("GUILD_ID") or ""


# 리셋할 때 비우는 유저 데이터 테이블. cards처럼 매번 다시 채워지는 기준 데이터는 제외한다.
USER_DATA_TABLES = (
    "inventory",
    "wallet",
    "job",
    "market",
    "unique_card_claim",
    "stock_holding",
    "dividend",
    "job_perk",
)

# 서버별 DB는 첫 접근 때 한 번만 초기화한다.
_initialized: set[str] = set()


def db_path(guild_id: str | int) -> Path:
    return DB_DIR / f"{guild_id}.db"


def existing_guild_ids() -> list[str]:
    """DB 파일이 이미 만들어진 서버 ID 목록. (한 번이라도 봇을 쓴 서버)"""
    if not DB_DIR.exists():
        return []
    return sorted(path.stem for path in DB_DIR.glob("*.db"))


def _connect(guild_id: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(guild_id))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_connection(guild_id: str | int) -> sqlite3.Connection:
    guild_id = str(guild_id)
    _ensure_initialized(guild_id)
    return _connect(guild_id)


@contextmanager
def get_db(guild_id: str | int):
    """해당 서버의 DB 커넥션을 열어준다. 서버마다 파일이 완전히 분리되어 있다."""
    conn = get_connection(guild_id)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_legacy_db(guild_id: str) -> None:
    """서버별 분리 이전의 단일 DB를 지정된 서버의 DB로 한 번 옮긴다."""
    if guild_id != _legacy_db_guild_id():
        return
    if not LEGACY_DB_PATH.exists() or db_path(guild_id).exists():
        return
    shutil.copy2(LEGACY_DB_PATH, db_path(guild_id))
    backup = LEGACY_DB_PATH.with_suffix(".db.migrated")
    LEGACY_DB_PATH.replace(backup)
    print(f"기존 DB를 서버 {guild_id}의 DB로 옮겼습니다. (백업: {backup.name})")


def _ensure_initialized(guild_id: str) -> None:
    if guild_id in _initialized:
        return
    DB_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_db(guild_id)

    # 순환 임포트를 피하려고 여기서 임포트한다.
    from . import cards as cards_module
    from . import stocks as stocks_module

    conn = _connect(guild_id)
    try:
        _migrate_schema(conn, guild_id)
        _create_tables(conn)
        cards_module.sync_cards(conn)
        stocks_module.sync_stocks(conn)
        conn.commit()
    finally:
        conn.close()
    _initialized.add(guild_id)


def _column_names(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_schema(conn, guild_id: str) -> None:
    card_columns = _column_names(conn, "cards")
    if "tab" in card_columns:
        # cards를 참조하는 테이블부터 지워야 외래키 제약에 걸리지 않는다.
        conn.execute("DROP TABLE IF EXISTS market")
        conn.execute("DROP TABLE IF EXISTS inventory")
        conn.execute("DROP TABLE IF EXISTS unique_card_claim")
        conn.execute("DROP TABLE IF EXISTS cards")
        return

    # DB 자체가 서버별로 분리됐으므로 unique_card_claim의 guild_id 컬럼은 필요 없다.
    claim_columns = _column_names(conn, "unique_card_claim")
    if "guild_id" in claim_columns:
        conn.execute(
            """
            CREATE TABLE unique_card_claim_new (
                card_id INTEGER PRIMARY KEY REFERENCES cards(id),
                holder_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO unique_card_claim_new (card_id, holder_id) "
            "SELECT card_id, holder_id FROM unique_card_claim WHERE guild_id = ?",
            (guild_id,),
        )
        conn.execute("DROP TABLE unique_card_claim")
        conn.execute("ALTER TABLE unique_card_claim_new RENAME TO unique_card_claim")

    # 수익률 표시를 위해 나중에 추가된 컬럼. 기존 보유분은 원가를 모르므로 0으로 시작한다.
    holding_columns = _column_names(conn, "stock_holding")
    if holding_columns and "total_cost" not in holding_columns:
        conn.execute(
            "ALTER TABLE stock_holding ADD COLUMN total_cost REAL NOT NULL DEFAULT 0"
        )

    # 하루 한 번 직업 변경 제한을 위해 나중에 추가된 컬럼.
    # 기존 취업자는 빈 값이라 오늘 한 번은 바로 바꿀 수 있다.
    job_columns = _column_names(conn, "job")
    if job_columns and "last_changed" not in job_columns:
        conn.execute("ALTER TABLE job ADD COLUMN last_changed TEXT NOT NULL DEFAULT ''")

    # 사업가 명의 주식을 지원하면서 추가된 컬럼.
    stock_columns = _column_names(conn, "stock")
    if stock_columns and "base_price" not in stock_columns:
        conn.execute("ALTER TABLE stock ADD COLUMN base_price INTEGER NOT NULL DEFAULT 300")
        # 기존 몹 주식의 기본가를 실제 값으로 채운다.
        from . import stocks as stocks_module

        for name, price in stocks_module.BASE_PRICES.items():
            conn.execute("UPDATE stock SET base_price = ? WHERE name = ?", (price, name))
    if stock_columns and "owner_id" not in stock_columns:
        conn.execute("ALTER TABLE stock ADD COLUMN owner_id TEXT")


def _create_tables(conn) -> None:
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
            job TEXT NOT NULL,
            -- 직업을 마지막으로 바꾼 날. 하루(오전 7시 기준) 한 번 제한에 쓴다.
            last_changed TEXT NOT NULL DEFAULT ''
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
            card_id INTEGER PRIMARY KEY REFERENCES cards(id),
            holder_id TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock (
            name TEXT PRIMARY KEY,
            price INTEGER NOT NULL,
            prev_price INTEGER NOT NULL,
            -- 폭락 후 되돌아갈 가격. 몹 주식은 종목마다 다르고 사업가 주식은 300이다.
            base_price INTEGER NOT NULL DEFAULT 300,
            -- 사업가 명의 주식이면 그 사람의 user_id. 기본 몹 주식은 NULL.
            owner_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_holding (
            user_id TEXT NOT NULL,
            stock_name TEXT NOT NULL REFERENCES stock(name),
            quantity INTEGER NOT NULL DEFAULT 0,
            -- 수익률 계산용. 지금 들고 있는 수량을 사기 위해 실제로 쓴 금액.
            total_cost REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, stock_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dividend (
            user_id TEXT PRIMARY KEY,
            accrued REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_perk (
            user_id TEXT PRIMARY KEY,
            perk_date TEXT NOT NULL,
            card_draws_used INTEGER NOT NULL DEFAULT 0,
            stock_buys_used INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def init_db(guild_id: str | int) -> None:
    """해당 서버의 DB를 만들고 카드/주식 기준 데이터를 채운다."""
    _ensure_initialized(str(guild_id))


def reset_guild_db(guild_id: str | int) -> None:
    """해당 서버의 유저 데이터를 전부 지우고 주식 시세를 기본값으로 되돌린다."""
    guild_id = str(guild_id)
    _ensure_initialized(guild_id)

    from . import stocks as stocks_module

    conn = _connect(guild_id)
    try:
        for table in USER_DATA_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM stock")
        # 시장 거래 번호도 1번부터 다시 시작하게 한다.
        has_sequence = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone()
        if has_sequence:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'market'")
        stocks_module.sync_stocks(conn)
        conn.commit()
    finally:
        conn.close()
