import json
from datetime import UTC, datetime

import pytest
from app.config import get_settings
from app.db.models import (
    EpisodeOutlineVersion,
    Project,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    StoryBibleVersion,
    StoryVersion,
)
from app.db.session import get_engine
from app.seed import PROJECT_ID
from app.services.projects import canonical_json, content_hash
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

STORY_ID = "93000000-0000-4000-8000-000000000001"
BIBLE_ID = "93000000-0000-4000-8000-000000000002"
OUTLINE_ID = "93000000-0000-4000-8000-000000000003"
SCRIPT_ID = "93000000-0000-4000-8000-000000000004"
SCENE_ID = "93000000-0000-4000-8000-000000000005"
LINE_ID = "93000000-0000-4000-8000-000000000006"


def prepare_script() -> int:
    now = datetime.now(UTC)
    line_text = "其实我觉得，你现在必须离开这里。"
    script_payload = {
        "title": "第一集",
        "estimated_duration_ms": 8_000,
        "scenes": [
            {
                "heading": "走廊对峙",
                "location": "旧公寓",
                "time_of_day": "夜",
                "purpose": "让两人的分歧公开化",
                "emotion": "紧张",
                "duration_ms": 8_000,
                "bgm_intent": "低频压迫",
                "sfx_intents": ["雨声"],
                "lines": [
                    {
                        "speaker_key": "lead",
                        "text": line_text,
                        "line_type": "DIALOGUE",
                        "emotion": "克制",
                        "speech_rate": 1.0,
                        "pause_after_ms": 300,
                        "estimated_duration_ms": 3_000,
                        "pronunciation": {},
                        "localizations": {},
                    }
                ],
            }
        ],
    }
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.status = "SCRIPT_READY"
        project.lock_version = 8
        project.updated_at = now
        project.current_story_version_id = STORY_ID
        session.add(
            StoryVersion(
                id=STORY_ID,
                project_id=PROJECT_ID,
                version=1,
                proposal_version=1,
                source_proposal_ids_json="[]",
                parent_version_id=None,
                schema_version="story-dna-v1",
                provider="test",
                model="test",
                config_version="test",
                title="旧公寓",
                logline="两个人在雨夜摊牌。",
                payload_json="{}",
                content_hash=content_hash({}),
                status="APPROVED",
                approved_at=now,
                approved_by="test",
                created_at=now,
            )
        )
        session.flush()
        session.add(
            StoryBibleVersion(
                id=BIBLE_ID,
                project_id=PROJECT_ID,
                story_version_id=STORY_ID,
                version=1,
                status="APPROVED",
                payload_json="{}",
                critic_json="{}",
                content_hash=content_hash({}),
                parent_version_id=None,
                schema_version="story-bible-v1",
                provider="test",
                model="test",
                config_version="test",
                approved_at=now,
                approved_by="test",
                created_at=now,
            )
        )
        session.flush()
        session.add(
            EpisodeOutlineVersion(
                id=OUTLINE_ID,
                project_id=PROJECT_ID,
                story_bible_version_id=BIBLE_ID,
                relationship_graph_version_id=None,
                episode_ordinal=1,
                version=1,
                status="APPROVED",
                payload_json="{}",
                critic_json="{}",
                content_hash=content_hash({}),
                parent_version_id=None,
                schema_version="episode-outline-v1",
                provider="test",
                model="test",
                config_version="test",
                approved_at=now,
                approved_by="test",
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ScriptVersion(
                id=SCRIPT_ID,
                project_id=PROJECT_ID,
                outline_version_id=OUTLINE_ID,
                relationship_graph_version_id=None,
                episode_ordinal=1,
                version=1,
                status="READY_FOR_REVIEW",
                payload_json=canonical_json(script_payload),
                critic_json="{}",
                content_hash=content_hash(script_payload),
                parent_version_id=None,
                schema_version="script-v1",
                canonical_language="zh-CN",
                provider="test",
                model="test",
                config_version="test",
                estimated_duration_ms=8_000,
                approved_at=None,
                approved_by=None,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ScriptScene(
                id=SCENE_ID,
                script_version_id=SCRIPT_ID,
                ordinal=1,
                heading="走廊对峙",
                location="旧公寓",
                time_of_day="夜",
                purpose="让两人的分歧公开化",
                emotion="紧张",
                duration_ms=8_000,
                bgm_intent="低频压迫",
                sfx_intent_json='["雨声"]',
            )
        )
        session.flush()
        session.add(
            ScriptLine(
                id=LINE_ID,
                script_scene_id=SCENE_ID,
                ordinal=1,
                speaker_key="lead",
                text=line_text,
                line_type="DIALOGUE",
                emotion="克制",
                speech_rate=1.0,
                pause_after_ms=300,
                estimated_duration_ms=3_000,
                pronunciation_json="{}",
                localization_json="{}",
            )
        )
        session.commit()
    return line_text.index("你现在")


@pytest.mark.anyio
async def test_excerpt_rewrite_retry_history_and_apply(client: AsyncClient) -> None:
    selection_start = prepare_script()
    selection_end = selection_start + len("你现在必须离开这里")
    request = {
        "expected_version": 8,
        "selection_start": selection_start,
        "selection_end": selection_end,
        "action": "INTENSIFY_CONFLICT",
    }
    generated = await client.post(
        f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}/rewrites",
        json=request,
    )
    assert generated.status_code == 201, generated.text
    first = generated.json()["data"]
    assert first["version"] == 1
    assert first["original_text"] == "你现在必须离开这里"
    assert first["proposed_text"] != first["original_text"]
    assert first["status"] == "GENERATED"

    retried = await client.post(
        f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}/rewrites",
        json={**request, "parent_revision_id": first["id"]},
    )
    assert retried.status_code == 201, retried.text
    second = retried.json()["data"]
    assert second["version"] == 2
    assert second["parent_revision_id"] == first["id"]

    history = await client.get(f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}/rewrites")
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["data"]] == [2, 1]

    applied = await client.post(
        f"/api/v1/script-excerpt-rewrites/{first['id']}/apply",
        json={
            "expected_version": 8,
            "script_id": SCRIPT_ID,
            "line_id": LINE_ID,
        },
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()["data"]
    assert result["rewrite"]["status"] == "APPLIED"
    assert result["script"]["version"] == 2
    assert result["script"]["project_lock_version"] == 9

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        revised_script = session.get(ScriptVersion, result["script"]["id"])
        assert revised_script is not None
        assert revised_script.parent_version_id == SCRIPT_ID
        revised_scene = session.scalar(
            select(ScriptScene).where(ScriptScene.script_version_id == revised_script.id)
        )
        assert revised_scene is not None
        revised_line = session.scalar(
            select(ScriptLine).where(ScriptLine.script_scene_id == revised_scene.id)
        )
        assert revised_line is not None
        assert first["proposed_text"] in revised_line.text
        assert (
            json.loads(revised_script.payload_json)["scenes"][0]["lines"][0]["text"]
            == revised_line.text
        )

    new_history = await client.get(
        f"/api/v1/scripts/{revised_script.id}/lines/{revised_line.id}/rewrites"
    )
    assert new_history.status_code == 200
    assert len(new_history.json()["data"]) == 2

    stale = await client.post(
        f"/api/v1/script-excerpt-rewrites/{second['id']}/apply",
        json={
            "expected_version": 9,
            "script_id": revised_script.id,
            "line_id": revised_line.id,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCRIPT_REWRITE_SOURCE_CHANGED"
