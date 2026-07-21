from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _engine_options() -> dict:
    options = {
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


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
