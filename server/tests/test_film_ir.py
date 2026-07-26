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


async def test_canvas_projection_reuses_film_ir_and_exposes_only_view_state_contract(
    client: AsyncClient,
) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        before_lock_version = project.lock_version
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))

    film_ir = (await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")).json()["data"]
    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/canvas-projection")

    assert response.status_code == 200, response.text
    canvas = response.json()["data"]
    assert canvas["schema_version"] == "film-canvas-projection-v1"
    assert canvas["source_projection"] == "film-ir-projection-v1"
    assert canvas["project_lock_version"] == before_lock_version
    assert canvas["nodes"]
    assert all(node["read_only"] is True for node in canvas["nodes"])
    film_ir_refs = {(item["type"], item["id"]) for item in film_ir["objects"]}
    canvas_refs = {(node["ref"]["type"], node["ref"]["id"]) for node in canvas["nodes"]}
    assert canvas_refs <= film_ir_refs
    assert {"Project", "Scene", "Shot", "Character"} <= {
        node["ref"]["type"] for node in canvas["nodes"]
    }
    nodes_by_type = {}
    for node in canvas["nodes"]:
        nodes_by_type.setdefault(node["ref"]["type"], []).append(node)
    assert nodes_by_type["Project"][0]["detail_route"] == f"/projects/{PROJECT_ID}"
    assert nodes_by_type["Shot"][0]["detail_route"] == f"/projects/{PROJECT_ID}/storyboard"
    assert nodes_by_type["Scene"][0]["detail_route"].startswith(
        f"/projects/{PROJECT_ID}/episodes/"
    )
    for edge in canvas["edges"]:
        assert (edge["source"]["type"], edge["source"]["id"]) in canvas_refs
        assert (edge["target"]["type"], edge["target"]["id"]) in canvas_refs

    contract = canvas["view_state_contract"]
    assert contract["persistence"] == "CLIENT_LOCAL"
    assert {
        "x",
        "y",
        "width",
        "height",
        "group",
        "z_index",
        "collapsed",
        "selected",
        "viewport.x",
        "viewport.y",
        "viewport.zoom",
    } == set(contract["allowed_fields"])
    assert {
        "canonical_status",
        "approval_status",
        "version_id",
        "domain_payload",
    } <= set(contract["forbidden_business_fields"])

    with Session(get_engine(get_settings().database_url)) as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None and project.lock_version == before_lock_version
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits
