import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import (
    Asset,
    AuditLog,
    EpisodeOutlineVersion,
    EventLog,
    GenerationRecord,
    Project,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    StoryBibleVersion,
    StoryVersion,
    Take,
)
from app.db.session import get_engine
from app.seed import PROJECT_ID
from app.services.projects import canonical_json, content_hash

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
        preserved_shot = session.scalar(select(Shot).limit(1))
        preserved_asset = session.scalar(select(Asset).limit(1))
        assert preserved_shot is not None and preserved_asset is not None
        session.add(
            Take(
                id="93000000-0000-4000-8000-000000000007",
                shot_id=preserved_shot.id,
                kind="DIRECTOR_TEST",
                version=1,
                asset_id=preserved_asset.id,
                status="SUCCEEDED",
                approval="APPROVED",
                is_current=False,
                parent_take_id=None,
                generation_record_id=None,
                quality_status="PASSED",
                identity_status="NOT_APPLICABLE",
                identity_score=None,
                identity_message=None,
                identity_reference_asset_ids_json="[]",
                identity_review_decision=None,
                identity_review_issues_json="[]",
                identity_review_note=None,
                identity_review_actor=None,
                identity_reviewed_at=None,
                identity_review_look_version=None,
                created_at=now,
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


@pytest.mark.anyio
async def test_domain_command_revises_script_idempotently(client: AsyncClient) -> None:
    prepare_script()
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        source = session.get(ScriptVersion, SCRIPT_ID)
        assert source is not None
        source_hash = source.content_hash

    command = {
        "command_id": "94000000-0000-4000-8000-000000000001",
        "command_type": "REVISE_SCRIPT",
        "actor": {"type": "USER", "id": "test-writer"},
        "target_object_id": SCRIPT_ID,
        "target_version_id": SCRIPT_ID,
        "expected_version": {
            "project_lock_version": 8,
            "target_version_id": SCRIPT_ID,
            "target_hash": source_hash,
        },
        "payload": {
            "scope": "LINE",
            "entity_id": LINE_ID,
            "changes": {"text": "灯灭以后，我只等你十秒。"},
        },
        "idempotency_key": "revise-script-command-v1",
    }
    executed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/commands",
        json=command,
    )
    assert executed.status_code == 200, executed.text
    assert executed.headers["Idempotency-Replayed"] == "false"
    execution = executed.json()["data"]
    assert execution["command_id"] == command["command_id"]
    assert execution["status"] == "SUCCEEDED"
    assert execution["result"]["version"] == 2

    replayed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/commands",
        json=command,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json()["data"]["result"] == execution["result"]

    conflict = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/commands",
        json={
            **command,
            "command_id": "94000000-0000-4000-8000-000000000002",
            "payload": {
                **command["payload"],
                "changes": {"text": "这次提交使用了不同的正文。"},
            },
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == PROJECT_ID,
                    AuditLog.action == "REVISE_SCRIPT",
                )
            ).all()
        )
        assert len(audits) == 1
        assert audits[0].actor == "test-writer"
        assert audits[0].before_hash == source_hash
        assert audits[0].after_hash == execution["result"]["content_hash"]
        events = list(
            session.scalars(
                select(EventLog).where(
                    EventLog.project_id == PROJECT_ID,
                    EventLog.event_type == "domain_command.executed",
                )
            ).all()
        )
        assert len(events) == 1
        event_payload = json.loads(events[0].payload_json)
        assert event_payload["command_id"] == command["command_id"]


@pytest.mark.anyio
async def test_director_proposal_review_execute_compare_and_rollback(
    client: AsyncClient,
) -> None:
    prepare_script()
    create_payload = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "target_id": SCENE_ID,
        "issue_types": ["AI_DIALOGUE", "CHARACTER_MOTIVATION", "PACING"],
        "instruction": "检查人物是否在解释剧情，而不是采取行动。",
        "actor": "test-director",
    }
    proposed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=create_payload,
        headers={"Idempotency-Key": "director-proposal-create-v1"},
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.headers["Idempotency-Replayed"] == "false"
    proposal = proposed.json()["data"]
    assert proposal["status"] == "PROPOSED"
    assert proposal["issue_type"] == "AI_DIALOGUE"
    assert len(proposal["alternatives"]) == 3
    assert proposal["recommended_option"] in {
        item["option_id"] for item in proposal["alternatives"]
    }
    assert proposal["requires_confirmation"] is True
    assert proposal["estimated_cost_usd"] == 0
    assert proposal["provider"]["model"] == "deterministic-director-evaluator-v1"
    assert proposal["preserved_objects"]

    replayed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=create_payload,
        headers={"Idempotency-Key": "director-proposal-create-v1"},
    )
    assert replayed.status_code == 201
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json()["data"]["proposal_id"] == proposal["proposal_id"]

    replay_conflict = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={**create_payload, "instruction": "同一幂等键下的不同审查要求。"},
        headers={"Idempotency-Key": "director-proposal-create-v1"},
    )
    assert replay_conflict.status_code == 409
    assert replay_conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    unconfirmed = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": False,
        },
        headers={"Idempotency-Key": "director-proposal-apply-unconfirmed-v1"},
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["error"]["code"] == "USER_CONFIRMATION_REQUIRED"

    executed = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-proposal-apply-v1"},
    )
    assert executed.status_code == 200, executed.text
    assert executed.headers["Idempotency-Replayed"] == "false"
    result = executed.json()["data"]
    assert result["proposal"]["status"] == "APPLIED_PENDING_APPROVAL"
    assert result["script"]["version"] == 2
    assert result["proposal"]["comparison"]["media_generation"] is False
    assert result["proposal"]["comparison"]["base_script_version_id"] == SCRIPT_ID
    revised_script_id = result["script"]["id"]

    execute_replay = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-proposal-apply-v1"},
    )
    assert execute_replay.status_code == 200
    assert execute_replay.headers["Idempotency-Replayed"] == "true"
    assert execute_replay.json()["data"] == result

    rolled_back = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/decision",
        json={
            "expected_version": 9,
            "decision": "ROLLBACK",
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-proposal-rollback-v1"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    rollback = rolled_back.json()["data"]
    assert rollback["status"] == "ROLLED_BACK"
    assert rollback["result_script_version_id"] == revised_script_id
    assert rollback["rollback_script_version_id"]

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        rollback_script = session.get(ScriptVersion, rollback["rollback_script_version_id"])
        assert rollback_script is not None
        rollback_scene = session.scalar(
            select(ScriptScene).where(ScriptScene.script_version_id == rollback_script.id)
        )
        assert rollback_scene is not None
        rollback_line = session.scalar(
            select(ScriptLine).where(ScriptLine.script_scene_id == rollback_scene.id)
        )
        assert rollback_line is not None
        assert rollback_line.text == "其实我觉得，你现在必须离开这里。"
        generation_records = list(
            session.scalars(
                select(GenerationRecord).where(
                    GenerationRecord.project_id == PROJECT_ID,
                    GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                )
            )
        )
        assert len(generation_records) == 1
        assert generation_records[0].output_asset_id is None
        command_audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == PROJECT_ID,
                    AuditLog.action.in_(
                        {
                            "CREATE_DIRECTOR_PROPOSAL",
                            "APPLY_DIRECTOR_PROPOSAL",
                            "DECIDE_DIRECTOR_PROPOSAL",
                        }
                    ),
                )
            )
        )
        assert len(command_audits) == 3


@pytest.mark.anyio
async def test_director_proposal_reject_and_approve_state_paths(
    client: AsyncClient,
) -> None:
    prepare_script()
    request = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "target_id": SCENE_ID,
        "issue_types": ["STORY_LOGIC"],
        "actor": "test-director",
    }
    rejected_proposal = (
        await client.post(
            f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
            json=request,
            headers={"Idempotency-Key": "director-reject-create-v1"},
        )
    ).json()["data"]
    rejected = await client.post(
        f"/api/v1/director-review-proposals/{rejected_proposal['proposal_id']}/decision",
        json={
            "expected_version": 8,
            "decision": "REJECT",
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-reject-decision-v1"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "REJECTED"

    approved_proposal = (
        await client.post(
            f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
            json=request,
            headers={"Idempotency-Key": "director-approve-create-v1"},
        )
    ).json()["data"]
    applied = await client.post(
        f"/api/v1/director-review-proposals/{approved_proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": approved_proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-approve-apply-v1"},
    )
    assert applied.status_code == 200, applied.text
    applied_data = applied.json()["data"]
    approved = await client.post(
        f"/api/v1/director-review-proposals/{approved_proposal['proposal_id']}/decision",
        json={
            "expected_version": 9,
            "decision": "APPROVE",
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-approve-decision-v1"},
    )
    assert approved.status_code == 200, approved.text
    approved_data = approved.json()["data"]
    assert approved_data["status"] == "APPROVED"
    assert approved_data["result_script_version_id"] == applied_data["script"]["id"]
    assert approved_data["approval_result"]["decision"] == "APPROVE"


@pytest.mark.anyio
async def test_script_patch_adapter_replays_by_idempotency_key(client: AsyncClient) -> None:
    prepare_script()
    request = {
        "expected_version": 8,
        "text": "灯灭以后，我只等你十秒。",
    }
    headers = {"Idempotency-Key": "script-line-patch-v1"}
    endpoint = f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}"

    executed = await client.patch(endpoint, json=request, headers=headers)
    assert executed.status_code == 200, executed.text
    assert executed.headers["Idempotency-Replayed"] == "false"

    replayed = await client.patch(endpoint, json=request, headers=headers)
    assert replayed.status_code == 200, replayed.text
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json()["data"] == executed.json()["data"]

    conflict = await client.patch(
        endpoint,
        json={**request, "text": "相同幂等键不能提交不同内容。"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.anyio
async def test_approval_command_requires_explicit_user_actor(client: AsyncClient) -> None:
    prepare_script()
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        source = session.get(ScriptVersion, SCRIPT_ID)
        assert source is not None
        source_hash = source.content_hash

    rejected = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/commands",
        json={
            "command_id": "94000000-0000-4000-8000-000000000010",
            "command_type": "APPROVE_SCRIPT",
            "actor": {"type": "DIRECTOR", "id": "director-orchestrator"},
            "target_object_id": SCRIPT_ID,
            "target_version_id": SCRIPT_ID,
            "expected_version": {
                "project_lock_version": 8,
                "target_version_id": SCRIPT_ID,
                "target_hash": source_hash,
            },
            "payload": {"confirmed": True},
            "idempotency_key": "director-cannot-approve-v1",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "USER_CONFIRMATION_REQUIRED"

    with factory() as session:
        source = session.get(ScriptVersion, SCRIPT_ID)
        assert source is not None
        assert source.status == "READY_FOR_REVIEW"
        assert (
            session.scalar(
                select(AuditLog).where(
                    AuditLog.project_id == PROJECT_ID,
                    AuditLog.action == "APPROVE_SCRIPT",
                )
            )
            is None
        )
