from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import sessionmaker

from app.config import SERVER_ROOT, get_settings
from app.db.session import get_engine
from app.main import app
from app.seed import seed_database


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    data_dir = tmp_path / "data"
    database_url = f"sqlite:///{data_dir / 'test.db'}"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JOB_WORKER_ENABLED", "0")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setenv("ARK_IDENTITY_QC_ENABLED", "0")
    monkeypatch.setenv("PROVIDER_MEDIA_STAGING_V1", "0")
    monkeypatch.delenv("TOS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOS_SECRET_KEY", raising=False)
    monkeypatch.delenv("TOS_SECURITY_TOKEN", raising=False)
    monkeypatch.delenv("TOS_BUCKET", raising=False)

    config = Config(str(SERVER_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        seed_database(session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
