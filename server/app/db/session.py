from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


@lru_cache(maxsize=8)
def get_engine(database_url: str) -> Engine:
    connect_args = (
        {"check_same_thread": False, "timeout": 5} if database_url.startswith("sqlite") else {}
    )
    if database_url.startswith("sqlite"):
        engine = create_engine(database_url, connect_args=connect_args, poolclass=NullPool)
    else:
        engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def get_session() -> Generator[Session, None, None]:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    with factory() as session:
        yield session
