import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AuditLog,
    ChangeSet,
    Character,
    EpisodeOutlineVersion,
    Project,
    ScriptVersion,
    StoryBibleVersion,
    StoryVersion,
)
from app.db.session import get_engine
from app.domain.film_ir import FilmIRObject, FilmIRProjection, FilmIRSource
from app.seed import PROJECT_ID
from app.services.canvas_projection import get_canvas_projection

pytestmark = pytest.mark.anyio


async def test_canvas_projection_exposes_dialogue_speaker_key() -> None:
    projection = get_canvas_projection(
        FilmIRProjection(
            project_id=PROJECT_ID,
            project_lock_version=1,
            objects=[
                FilmIRObject(
                    type="DialogueLine",
                    id="line-1",
                    version_id="line-version-1",
                    canonical_kind="CANONICAL",
                    canonical_status="PENDING_REVIEW",
                    approval_status="DRAFT",
                    source=FilmIRSource(
                        table="script_lines",
                        row_id="line-version-1",
                        id_strategy="VERSION_SCOPED_DERIVED",
                    ),
                    attributes={
                        "line_type": "DIALOGUE",
                        "speaker_key": "wife",
                        "text": "等明天。",
                    },
                ),
            ],
            edges=[],
        ),
    )

    assert projection.nodes[0].operation_context == {
        "line_type": "DIALOGUE",
        "speaker_key": "wife",
    }


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


async def test_film_ir_links_a_character_to_a_beat_that_explicitly_mentions_them(
    client: AsyncClient,
) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        character = session.scalar(
            select(Character)
            .where(Character.project_id == PROJECT_ID)
            .order_by(Character.character_key)
            .limit(1)
        )
        assert character is not None
        now = datetime.now(UTC)
        story = StoryVersion(
            id="a1000000-0000-4000-8000-000000000002",
            project_id=PROJECT_ID,
            version=1,
            proposal_version=1,
            source_proposal_ids_json="[]",
            parent_version_id=None,
            schema_version="story-dna-v1",
            provider="test",
            model="test",
            config_version="test",
            title="角色节拍联动",
            logline="角色在叙事节拍中推进冲突。",
            payload_json="{}",
            content_hash="test-character-beat-story",
            status="APPROVED",
            approved_at=now,
            approved_by="test",
            created_at=now,
        )
        session.add(story)
        session.flush()
        bible = StoryBibleVersion(
            id="a1000000-0000-4000-8000-000000000003",
            project_id=PROJECT_ID,
            story_version_id=story.id,
            version=1,
            status="APPROVED",
            payload_json="{}",
            critic_json="{}",
            content_hash="test-character-beat-bible",
            parent_version_id=None,
            schema_version="story-bible-v1",
            provider="test",
            model="test",
            config_version="test",
            approved_at=now,
            approved_by="test",
            created_at=now,
        )
        session.add(bible)
        session.flush()
        outline = EpisodeOutlineVersion(
            id="a1000000-0000-4000-8000-000000000004",
            project_id=PROJECT_ID,
            story_bible_version_id=bible.id,
            relationship_graph_version_id=None,
            episode_ordinal=1,
            version=1,
            status="APPROVED",
            payload_json="{}",
            critic_json="{}",
            content_hash="test-character-beat-outline",
            parent_version_id=None,
            schema_version="episode-outline-v1",
            provider="test",
            model="test",
            config_version="test",
            approved_at=now,
            approved_by="test",
            created_at=now,
        )
        session.add(outline)
        session.flush()
        script = ScriptVersion(
            id="a1000000-0000-4000-8000-000000000001",
            project_id=PROJECT_ID,
            outline_version_id=outline.id,
            relationship_graph_version_id=None,
            episode_ordinal=1,
            version=1,
            status="READY_FOR_REVIEW",
            payload_json=json.dumps(
                {
                    "title": "角色节拍联动剧本",
                    "short_drama_engine": {
                        "protagonist_desire": "角色必须在关键事件中推动冲突并作出选择。",
                        "beats": [
                            {
                                "sequence": 1,
                                "scene_ordinal": 1,
                                "description": f"{character.name} 在此节拍中推进冲突",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            critic_json="{}",
            content_hash="test-character-beat-link",
            parent_version_id=None,
            schema_version="script-v1",
            canonical_language="zh-CN",
            provider="test",
            model="test",
            config_version="test",
            estimated_duration_ms=1_000,
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(script)
        session.commit()
        character_id = character.id

    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")

    assert response.status_code == 200, response.text
    edges = response.json()["data"]["edges"]
    assert any(
        edge["relation"] == "APPEARS_IN_BEAT"
        and edge["source"]["type"] == "Character"
        and edge["source"]["id"] == character_id
        and edge["target"]["type"] == "Beat"
        for edge in edges
    )

    canvas_response = await client.get(f"/api/v1/projects/{PROJECT_ID}/canvas-projection")
    assert canvas_response.status_code == 200, canvas_response.text
    script_node = next(
        node
        for node in canvas_response.json()["data"]["nodes"]
        if node["ref"]["id"] == f"script:{PROJECT_ID}:1"
    )
    assert script_node["label"] == "角色节拍联动剧本"
    assert script_node["content_summary"] == "角色必须在关键事件中推动冲突并作出选择。"

    beat_node = next(
        node
        for node in canvas_response.json()["data"]["nodes"]
        if node["ref"]["type"] == "Beat"
    )
    assert 0 < len(beat_node["label"]) <= 18
    assert beat_node["content_summary"] == f"{character.name} 在此节拍中推进冲突"


async def test_film_ir_unknown_project_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/projects/not-a-project/film-ir")
    assert response.status_code == 404


async def test_canvas_projection_shows_director_proposal_observation_and_recommendation(
    client: AsyncClient,
) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        now = datetime.now(UTC)
        change_set_id = "b1000000-0000-4000-8000-000000000001"
        change_set = ChangeSet(
            id=change_set_id,
            project_id=PROJECT_ID,
            base_timeline_id=None,
            base_relationship_graph_id=None,
            scope_json="{}",
            instruction="审查第 2 场的节奏",
            impact_json=json.dumps(
                {
                    "proposal": {
                        "issue_type": "PACING",
                        "observation": "规则说明集中出现，紧张感被削弱。",
                        "target_objects": [],
                        "recommended_option": "option-b",
                        "alternatives": [
                            {"option_id": "option-a", "title": "缩短说明"},
                            {"option_id": "option-b", "title": "先演示规则，再揭示代价"},
                        ],
                        "scene_ordinal": 2,
                    }
                },
                ensure_ascii=False,
            ),
            estimate_json="{}",
            status="PROPOSED",
            result_timeline_id=None,
            result_relationship_graph_id=None,
            created_at=now,
        )
        session.add(change_set)
        session.commit()

    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/canvas-projection")

    assert response.status_code == 200, response.text
    director_node = next(
        node
        for node in response.json()["data"]["nodes"]
        if node["ref"] == {"type": "DirectorProposal", "id": change_set_id, "version_id": None}
    )
    assert director_node["label"] == "规则说明集中出现，紧张感被削弱。"
    assert director_node["content_summary"] == "第 2 场 · 推荐：先演示规则，再揭示代价"
    assert director_node["operation_context"] == {}


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
    character_nodes = [node for node in canvas["nodes"] if node["ref"]["type"] == "Character"]
    assert any(node["thumbnail_url"] for node in character_nodes)
    assert all(node["operation_context"]["character_key"] for node in character_nodes)
    assert all(
        node["thumbnail_url"] is None or node["thumbnail_url"].startswith("/api/v1/assets/")
        for node in canvas["nodes"]
    )
    nodes_by_type = {}
    for node in canvas["nodes"]:
        nodes_by_type.setdefault(node["ref"]["type"], []).append(node)
    assert nodes_by_type["Project"][0]["detail_route"] == f"/projects/{PROJECT_ID}"
    assert nodes_by_type["Shot"][0]["detail_route"] == f"/projects/{PROJECT_ID}/storyboard"
    assert {
        "description",
        "dialogue",
        "shot_size",
        "camera_movement",
        "shot_lock_version",
    } <= set(nodes_by_type["Shot"][0]["operation_context"])
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
