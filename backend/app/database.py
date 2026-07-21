from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import inspect, text
from app.config import settings


def _engine_options() -> dict:
    options = {
        "echo": False,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 10,
    }
    if settings.database_url.startswith("mysql+"):
        options["connect_args"] = {"connect_timeout": 5}
    return options


engine = create_async_engine(settings.database_url, **_engine_options())
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_user_account_columns)


def _ensure_user_account_columns(sync_conn):
    inspector = inspect(sync_conn)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    indexes = {index["name"] for index in inspector.get_indexes("users")}
    if "phone" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(32)"))
    if "ix_users_phone" not in indexes:
        sync_conn.execute(text("CREATE UNIQUE INDEX ix_users_phone ON users (phone)"))
    if columns.get("email", {}).get("nullable") is not False:
        return
    if sync_conn.dialect.name.startswith("mysql"):
        sync_conn.execute(text("ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NULL"))
    elif sync_conn.dialect.name == "sqlite":
        _rebuild_sqlite_users_table(sync_conn)


def _rebuild_sqlite_users_table(sync_conn):
    sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        sync_conn.execute(
            text(
                "CREATE TABLE users_new ("
                "id VARCHAR(64) NOT NULL PRIMARY KEY, "
                "email VARCHAR(255), "
                "phone VARCHAR(32), "
                "password_hash VARCHAR(255) NOT NULL, "
                "created_at DATETIME, "
                "sandbox_id VARCHAR(64)"
                ")"
            )
        )
        sync_conn.execute(
            text(
                "INSERT INTO users_new (id, email, phone, password_hash, created_at, sandbox_id) "
                "SELECT id, email, phone, password_hash, created_at, sandbox_id FROM users"
            )
        )
        sync_conn.execute(text("DROP TABLE users"))
        sync_conn.execute(text("ALTER TABLE users_new RENAME TO users"))
        sync_conn.execute(text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))
        sync_conn.execute(text("CREATE UNIQUE INDEX ix_users_phone ON users (phone)"))
    finally:
        sync_conn.execute(text("PRAGMA foreign_keys=ON"))
