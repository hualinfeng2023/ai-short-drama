import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuditLog, Project
from app.db.session import get_engine
from app.seed import PROJECT_ID

pytestmark = pytest.mark.anyio


async def test_film_ir_is_read_only_projection_of_existing_rows(
    client: AsyncClient,
) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        before_lock_version = project.lock_version
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))

    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")

    assert response.status_code == 200, response.text
    projection = response.json()["data"]
    assert projection["schema_version"] == "film-ir-projection-v1"
    assert projection["project_id"] == PROJECT_ID
    assert projection["project_lock_version"] == before_lock_version

    objects = projection["objects"]
    object_keys = {(item["type"], item["id"]) for item in objects}
    assert ("Project", PROJECT_ID) in object_keys
    assert any(item["type"] == "Scene" for item in objects)
    assert any(item["type"] == "Shot" for item in objects)
    assert any(item["type"] == "Asset" for item in objects)
    assert any(item["type"] == "Character" for item in objects)

    for edge in projection["edges"]:
        assert (edge["source"]["type"], edge["source"]["id"]) in object_keys
        assert (edge["target"]["type"], edge["target"]["id"]) in object_keys
        assert edge["evidence"]

    with Session(get_engine(get_settings().database_url)) as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.lock_version == before_lock_version
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits


async def test_film_ir_unknown_project_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/projects/not-a-project/film-ir")
    assert response.status_code == 404
