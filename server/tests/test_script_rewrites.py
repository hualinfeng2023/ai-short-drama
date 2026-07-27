import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.v1 import director as director_api
from app.config import get_settings
from app.db.models import (
    Asset,
    AuditLog,
    ChangeSet,
    EpisodeOutlineVersion,
    EventLog,
    GenerationRecord,
    IdempotencyKey,
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
from app.domain.director import DirectorProposalRequest
from app.seed import PROJECT_ID
from app.services import director_proposals
from app.services.director_proposals import DirectorProposalDraft
from app.services.projects import canonical_json, content_hash
from app.services.text_provider import TextGenerationResult, TextProviderError

STORY_ID = "93000000-0000-4000-8000-000000000001"
BIBLE_ID = "93000000-0000-4000-8000-000000000002"
OUTLINE_ID = "93000000-0000-4000-8000-000000000003"
SCRIPT_ID = "93000000-0000-4000-8000-000000000004"
SCENE_ID = "93000000-0000-4000-8000-000000000005"
LINE_ID = "93000000-0000-4000-8000-000000000006"
SECOND_SCENE_ID = "93000000-0000-4000-8000-000000000008"
SECOND_LINE_ID = "93000000-0000-4000-8000-000000000009"


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


def add_second_script_scene() -> None:
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        script = session.get(ScriptVersion, SCRIPT_ID)
        assert script is not None
        payload = json.loads(script.payload_json)
        payload["estimated_duration_ms"] = 15_000
        payload["scenes"].append(
            {
                "heading": "门外告别",
                "location": "旧公寓门口",
                "time_of_day": "夜",
                "purpose": "让角色说出最后的选择",
                "emotion": "克制",
                "duration_ms": 7_000,
                "bgm_intent": "安静留白",
                "sfx_intents": ["关门声"],
                "lines": [
                    {
                        "speaker_key": "support",
                        "text": "我会在楼下等你。",
                        "line_type": "DIALOGUE",
                        "emotion": "平静",
                        "speech_rate": 1.0,
                        "pause_after_ms": 300,
                        "estimated_duration_ms": 2_000,
                        "pronunciation": {},
                        "localizations": {},
                    }
                ],
            }
        )
        script.payload_json = canonical_json(payload)
        script.content_hash = content_hash(payload)
        script.estimated_duration_ms = 15_000
        session.add(
            ScriptScene(
                id=SECOND_SCENE_ID,
                script_version_id=SCRIPT_ID,
                ordinal=2,
                heading="门外告别",
                location="旧公寓门口",
                time_of_day="夜",
                purpose="让角色说出最后的选择",
                emotion="克制",
                duration_ms=7_000,
                bgm_intent="安静留白",
                sfx_intent_json='["关门声"]',
            )
        )
        session.flush()
        session.add(
            ScriptLine(
                id=SECOND_LINE_ID,
                script_scene_id=SECOND_SCENE_ID,
                ordinal=1,
                speaker_key="support",
                text="我会在楼下等你。",
                line_type="DIALOGUE",
                emotion="平静",
                speech_rate=1.0,
                pause_after_ms=300,
                estimated_duration_ms=2_000,
                pronunciation_json="{}",
                localization_json="{}",
            )
        )
        session.commit()


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
    assert generated.headers["Idempotency-Replayed"] == "false"
    first = generated.json()["data"]
    assert first["version"] == 1
    assert first["original_text"] == "你现在必须离开这里"
    assert first["proposed_text"] != first["original_text"]
    assert first["status"] == "GENERATED"
    replayed_generated = await client.post(
        f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}/rewrites",
        json=request,
    )
    assert replayed_generated.status_code == 201
    assert replayed_generated.headers["Idempotency-Replayed"] == "true"
    assert replayed_generated.json()["data"] == first

    retried = await client.post(
        f"/api/v1/scripts/{SCRIPT_ID}/lines/{LINE_ID}/rewrites",
        json={**request, "parent_revision_id": first["id"]},
    )
    assert retried.status_code == 201, retried.text
    assert retried.headers["Idempotency-Replayed"] == "false"
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
    assert applied.headers["Idempotency-Replayed"] == "false"
    result = applied.json()["data"]
    assert result["rewrite"]["status"] == "APPLIED"
    assert result["script"]["version"] == 2
    assert result["script"]["project_lock_version"] == 9
    replayed_applied = await client.post(
        f"/api/v1/script-excerpt-rewrites/{first['id']}/apply",
        json={
            "expected_version": 8,
            "script_id": SCRIPT_ID,
            "line_id": LINE_ID,
        },
    )
    assert replayed_applied.status_code == 200
    assert replayed_applied.headers["Idempotency-Replayed"] == "true"
    assert replayed_applied.json()["data"] == result

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
        creation_audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == PROJECT_ID,
                    AuditLog.action == "CREATE_SCRIPT_EXCERPT_REWRITE",
                )
            ).all()
        )
        assert len(creation_audits) == 2
        assert {audit.entity_type for audit in creation_audits} == {"script_excerpt_revision"}
        apply_audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == PROJECT_ID,
                    AuditLog.action == "APPLY_SCRIPT_EXCERPT_REWRITE",
                    AuditLog.entity_id == revised_script.id,
                )
            ).all()
        )
        assert len(apply_audits) == 1
        assert apply_audits[0].entity_type == "script_version"
        assert apply_audits[0].before_hash != apply_audits[0].after_hash

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

    proposal_list = await client.get(f"/api/v1/projects/{PROJECT_ID}/director-review-proposals")
    assert proposal_list.status_code == 200
    assert [item["proposal_id"] for item in proposal_list.json()["data"]] == [
        proposal["proposal_id"]
    ]

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
    timeline_preview = result["proposal"]["comparison"]["timeline_preview"]
    assert timeline_preview["schema_version"] == "director-timeline-preview-v1"
    assert timeline_preview["projection_mode"] == "READ_ONLY"
    assert timeline_preview["canonical_source"] == "SCRIPT"
    assert timeline_preview["scene_logical_id"] == f"script-scene:{PROJECT_ID}:1:1"
    assert timeline_preview["formal_timeline_unchanged"] is True
    assert timeline_preview["media_generation"] is False
    assert timeline_preview["affected_tracks"] == ["DIALOGUE", "SUBTITLE"]
    assert timeline_preview["before"]["script_version_id"] == SCRIPT_ID
    assert timeline_preview["after"]["script_version_id"] == result["script"]["id"]
    assert timeline_preview["before"]["duration_budget_ms"] == 8_000
    assert timeline_preview["downstream_shift_ms"] == (
        timeline_preview["after"]["projected_scene_window_ms"]
        - timeline_preview["before"]["projected_scene_window_ms"]
    )
    revised_script_id = result["script"]["id"]
    film_ir = (await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")).json()["data"]
    proposal_node = next(
        item
        for item in film_ir["objects"]
        if item["type"] == "DirectorProposal" and item["id"] == proposal["proposal_id"]
    )
    assert proposal_node["canonical_kind"] == "DERIVED"
    assert proposal_node["canonical_status"] == "APPLIED_PENDING_APPROVAL"
    film_ir_edges = film_ir["edges"]
    assert ("PRESERVES", False) in {
        (edge["relation"], edge["inferred"]) for edge in film_ir_edges
    }
    logical_script_scene_id = f"script-scene:{PROJECT_ID}:1:1"
    assert any(
        edge["relation"] == "PROPOSES_CHANGE_TO"
        and edge["source"]["type"] == "DirectorProposal"
        and edge["source"]["id"] == proposal["proposal_id"]
        and edge["target"]["type"] == "ScriptScene"
        and edge["target"]["id"] == logical_script_scene_id
        for edge in film_ir_edges
    )
    canvas = (
        await client.get(f"/api/v1/projects/{PROJECT_ID}/canvas-projection")
    ).json()["data"]
    assert any(
        node["ref"]["type"] == "DirectorProposal"
        and node["ref"]["id"] == proposal["proposal_id"]
        for node in canvas["nodes"]
    )
    assert any(
        edge["relation"] == "PROPOSES_CHANGE_TO"
        and edge["source"]["id"] == proposal["proposal_id"]
        and edge["target"]["id"] == logical_script_scene_id
        for edge in canvas["edges"]
    )

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
        reservation = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == director_api._request_reservation_scope(PROJECT_ID),
                IdempotencyKey.key == "director-proposal-create-v1",
            )
        )
        assert reservation is not None
        assert reservation.status_code == 201
        reservation_payload = json.loads(reservation.response_json)
        assert reservation_payload["state"] == "SUCCEEDED"
        assert reservation_payload["result"]["proposal_id"] == proposal["proposal_id"]
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
async def test_director_proposal_follows_current_review_script_when_other_scene_changed(
    client: AsyncClient,
) -> None:
    prepare_script()
    add_second_script_scene()
    request = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "issue_types": ["AI_DIALOGUE"],
        "actor": "test-director",
    }
    first_proposal_response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={**request, "target_id": SCENE_ID},
        headers={"Idempotency-Key": "director-sequential-first-create-v1"},
    )
    assert first_proposal_response.status_code == 201, first_proposal_response.text
    first_proposal = first_proposal_response.json()["data"]
    second_proposal_response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={**request, "target_id": SECOND_SCENE_ID},
        headers={"Idempotency-Key": "director-sequential-second-create-v1"},
    )
    assert second_proposal_response.status_code == 201, second_proposal_response.text
    second_proposal = second_proposal_response.json()["data"]

    first_applied = await client.post(
        f"/api/v1/director-review-proposals/{first_proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": first_proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-sequential-first-apply-v1"},
    )
    assert first_applied.status_code == 200, first_applied.text
    version_two = first_applied.json()["data"]["script"]

    second_applied = await client.post(
        f"/api/v1/director-review-proposals/{second_proposal['proposal_id']}/execute",
        json={
            "expected_version": 9,
            "option_id": second_proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-sequential-second-apply-v1"},
    )
    assert second_applied.status_code == 200, second_applied.text
    second_result = second_applied.json()["data"]
    assert second_result["script"]["version"] == 3
    assert second_result["script"]["parent_version_id"] == version_two["id"]
    comparison = second_result["proposal"]["comparison"]
    assert comparison["base_script_version_id"] == version_two["id"]
    assert comparison["proposal_base_script_version_id"] == SCRIPT_ID

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        latest_scenes = list(
            session.scalars(
                select(ScriptScene)
                .where(ScriptScene.script_version_id == second_result["script"]["id"])
                .order_by(ScriptScene.ordinal)
            )
        )
        assert len(latest_scenes) == 2
        latest_lines = [
            session.scalar(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == scene.id)
                .order_by(ScriptLine.ordinal)
            )
            for scene in latest_scenes
        ]
        assert latest_lines[0] is not None
        assert latest_lines[1] is not None
        first_selected = next(
            item
            for item in first_proposal["alternatives"]
            if item["option_id"] == first_proposal["recommended_option"]
        )
        second_selected = next(
            item
            for item in second_proposal["alternatives"]
            if item["option_id"] == second_proposal["recommended_option"]
        )
        assert latest_lines[0].text == first_selected["proposed_change"]["changes"]["text"]
        assert latest_lines[1].text == second_selected["proposed_change"]["changes"]["text"]


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
async def test_director_approval_requires_reason_when_timeline_preview_needs_review(
    client: AsyncClient,
) -> None:
    prepare_script()
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        scene = session.get(ScriptScene, SCENE_ID)
        script = session.get(ScriptVersion, SCRIPT_ID)
        assert scene is not None
        assert script is not None
        scene.duration_ms = 2_000
        payload = json.loads(script.payload_json)
        payload["estimated_duration_ms"] = 2_000
        payload["scenes"][0]["duration_ms"] = 2_000
        script.payload_json = canonical_json(payload)
        script.content_hash = content_hash(payload)
        script.estimated_duration_ms = 2_000
        session.commit()

    proposal = (
        await client.post(
            f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
            json={
                "expected_version": 8,
                "target_type": "SCRIPT_SCENE",
                "target_id": SCENE_ID,
                "issue_types": ["AI_DIALOGUE"],
                "actor": "test-director",
            },
            headers={"Idempotency-Key": "director-risk-create-v1"},
        )
    ).json()["data"]
    applied = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/execute",
        json={
            "expected_version": 8,
            "option_id": proposal["recommended_option"],
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-risk-apply-v1"},
    )
    assert applied.status_code == 200, applied.text
    timeline_preview = applied.json()["data"]["proposal"]["comparison"]["timeline_preview"]
    assert timeline_preview["validation_status"] == "REVIEW_REQUIRED"
    assert timeline_preview["risk"] == "DURATION_BUDGET_EXCEEDED"

    blocked = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/decision",
        json={
            "expected_version": 9,
            "decision": "APPROVE",
            "actor": "test-director",
            "confirmed": True,
        },
        headers={"Idempotency-Key": "director-risk-approve-blocked-v1"},
    )
    assert blocked.status_code == 409, blocked.text
    assert (
        blocked.json()["error"]["code"]
        == "DIRECTOR_APPROVAL_OVERRIDE_REASON_REQUIRED"
    )

    override_reason = "对白超出预算，但已确认下一场可以顺延并会继续复核。"
    approved = await client.post(
        f"/api/v1/director-review-proposals/{proposal['proposal_id']}/decision",
        json={
            "expected_version": 9,
            "decision": "APPROVE",
            "actor": "test-director",
            "confirmed": True,
            "override_reason": override_reason,
        },
        headers={"Idempotency-Key": "director-risk-approve-override-v1"},
    )
    assert approved.status_code == 200, approved.text
    approved_data = approved.json()["data"]
    assert approved_data["status"] == "APPROVED"
    assert approved_data["approval_result"] == {
        "decision": "APPROVE",
        "actor": "test-director",
        "at": approved_data["approval_result"]["at"],
        "validation_status": "REVIEW_REQUIRED",
        "risk": "DURATION_BUDGET_EXCEEDED",
        "override_reason": override_reason,
    }


@pytest.mark.anyio
async def test_director_command_rejects_non_executable_change_contract(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()

    async def fake_prepare(session, _settings, *, project_id, request):  # noqa: ANN001
        del project_id, request
        script = session.get(ScriptVersion, SCRIPT_ID)
        assert script is not None
        return DirectorProposalDraft(
            target_object_id=SCENE_ID,
            target_version_id=SCRIPT_ID,
            target_hash=script.content_hash,
            payload={
                "requested_by": "test-director",
                "review": {
                    "issue_type": "PACING",
                    "observation": "台词超过场景预算。",
                    "rationale": "模型试图直接写入派生时长。",
                    "options": [
                        {
                            "option_id": "invalid-duration",
                            "title": "直接修改估算时长",
                            "rationale": "该字段不属于可执行写入合同。",
                            "proposed_change": {
                                "scope": "LINE",
                                "entity_id": LINE_ID,
                                "changes": {"estimated_duration_ms": 1600},
                                "before": {"estimated_duration_ms": 3000},
                            },
                            "estimated_time_seconds": 1,
                            "estimated_cost_usd": 0,
                        },
                        {
                            "option_id": "valid-pause",
                            "title": "缩短停顿",
                            "rationale": "只修改允许字段。",
                            "proposed_change": {
                                "scope": "LINE",
                                "entity_id": LINE_ID,
                                "changes": {"pause_after_ms": 100},
                                "before": {"pause_after_ms": 300},
                            },
                            "estimated_time_seconds": 1,
                            "estimated_cost_usd": 0,
                        },
                    ],
                    "recommended_option_id": "invalid-duration",
                    "confidence": 0.8,
                    "validation_plan": ["比较时长"],
                },
                "context": {},
                "impact": {},
                "provider": {"provider": "test", "model": "invalid-director"},
            },
        )

    monkeypatch.setattr(
        "app.api.v1.director.prepare_director_proposal",
        fake_prepare,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-invalid-contract-v1"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "DIRECTOR_CHANGE_CONTRACT_INVALID"

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        assert session.scalars(select(ChangeSet)).all() == []
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        assert project.lock_version == 8
        failure = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                GenerationRecord.status == "FAILED",
            )
        )
        assert failure is not None
        assert failure.entity_type == "script_scene"
        assert failure.entity_id == SCENE_ID
        failure_metadata = json.loads(failure.metadata_json)
        assert failure_metadata["failure_stage"] == "COMMAND_VALIDATION"
        assert failure_metadata["error"]["code"] == "DIRECTOR_CHANGE_CONTRACT_INVALID"
        assert failure_metadata["media_generation"] is False
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.project_id == PROJECT_ID,
                AuditLog.action == "DIRECTOR_SCENE_REVIEW_FAILED",
            )
        )
        assert audit is not None
        assert audit.actor == "test-director"
        assert audit.entity_id == SCENE_ID
        assert audit.trace_id == failure.id


@pytest.mark.anyio
async def test_director_command_revalidates_retry_source_lineage(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    original_prepare = director_proposals.prepare_director_proposal

    async def fake_prepare(*args, **kwargs) -> DirectorProposalDraft:  # noqa: ANN002, ANN003
        draft = await original_prepare(*args, **kwargs)
        return DirectorProposalDraft(
            target_object_id=draft.target_object_id,
            target_version_id=draft.target_version_id,
            target_hash=draft.target_hash,
            payload={
                **draft.payload,
                "retry_of_generation_record_id": SCRIPT_ID,
            },
        )

    monkeypatch.setattr(
        "app.api.v1.director.prepare_director_proposal",
        fake_prepare,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-forged-retry-lineage-v1"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "DIRECTOR_RETRY_SOURCE_NOT_FOUND"

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        assert session.scalars(select(ChangeSet)).all() == []
        assert (
            session.scalar(
                select(GenerationRecord).where(
                    GenerationRecord.project_id == PROJECT_ID,
                    GenerationRecord.status == "SUCCEEDED",
                    GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                )
            )
            is None
        )


@pytest.mark.anyio
async def test_director_provider_failure_is_audited_without_changeset(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_PROMPT_MODEL", "test-director-model")
    provider_calls = 0

    async def fake_generate(*_args, **_kwargs) -> TextGenerationResult:
        nonlocal provider_calls
        provider_calls += 1
        raise TextProviderError(
            "ARK_TEXT_SCHEMA_INVALID",
            "连续三次未返回符合合同的 JSON",
            retryable=True,
            details={
                "validator": "DirectorReviewOutput",
                "last_request_id": "request-3",
                "attempts": [
                    {
                        "attempt": attempt,
                        "request_id": f"request-{attempt}",
                        "error_type": "validation_error",
                        "validation_error": "estimated_duration_ms: Extra inputs are not permitted",
                    }
                    for attempt in range(1, 4)
                ],
            },
        )

    monkeypatch.setattr(
        director_proposals,
        "generate_director_scene_review",
        fake_generate,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-provider-failure-v1"},
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "ARK_TEXT_SCHEMA_INVALID"
    replayed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-provider-failure-v1"},
    )
    assert replayed.status_code == 503, replayed.text
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json()["error"]["code"] == "ARK_TEXT_SCHEMA_INVALID"
    assert provider_calls == 1

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        assert session.scalars(select(ChangeSet)).all() == []
        failure = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
            )
        )
        assert failure is not None
        assert failure.status == "FAILED"
        assert failure.provider == "volcengine-ark"
        assert failure.model == "test-director-model"
        assert failure.provider_request_id == "request-3"
        assert failure.latency_ms is not None
        metadata = json.loads(failure.metadata_json)
        assert metadata["failure_stage"] == "PROVIDER_VALIDATION"
        assert metadata["attempt_count"] == 3
        assert metadata["repair_attempts"] == 2
        assert metadata["error"]["retryable"] is True
        assert metadata["error"]["details"]["validator"] == "DirectorReviewOutput"
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.project_id == PROJECT_ID,
                AuditLog.action == "DIRECTOR_SCENE_REVIEW_FAILED",
            )
        )
        assert audit is not None
        assert audit.trace_id == failure.id
        failure_id = failure.id
        reservation = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == director_api._request_reservation_scope(PROJECT_ID),
                IdempotencyKey.key == "director-provider-failure-v1",
            )
        )
        assert reservation is not None
        assert reservation.status_code == 503
        reservation_payload = json.loads(reservation.response_json)
        assert reservation_payload["state"] == "FAILED"
        assert reservation_payload["error"]["code"] == "ARK_TEXT_SCHEMA_INVALID"

    failed_retry = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "retry_of_generation_record_id": failure_id,
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-provider-failure-retry-v1"},
    )
    assert failed_retry.status_code == 503, failed_retry.text
    assert provider_calls == 2
    with factory() as session:
        retry_failure = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                GenerationRecord.status == "FAILED",
                GenerationRecord.id != failure_id,
            )
        )
        assert retry_failure is not None
        retry_failure_metadata = json.loads(retry_failure.metadata_json)
        assert (
            retry_failure_metadata["retry_of_generation_record_id"]
            == failure_id
        )
        retry_failure_id = retry_failure.id

    film_ir_response = await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")
    assert film_ir_response.status_code == 200, film_ir_response.text
    film_ir = film_ir_response.json()["data"]
    failure_node = next(
        item
        for item in film_ir["objects"]
        if item["type"] == "GenerationRecord" and item["id"] == failure_id
    )
    assert failure_node["canonical_status"] == "FAILED"
    assert failure_node["attributes"]["repair_attempts"] == 2
    assert failure_node["attributes"]["error_code"] == "ARK_TEXT_SCHEMA_INVALID"
    assert failure_node["attributes"]["failure_stage"] == "PROVIDER_VALIDATION"
    assert any(
        edge["source"]["type"] == "GenerationRecord"
        and edge["source"]["id"] == failure_id
        and edge["target"]["type"] == "ScriptScene"
        and edge["target"]["id"] == f"script-scene:{PROJECT_ID}:1:1"
        and edge["relation"] == "EVALUATED_SCRIPT_SCENE"
        for edge in film_ir["edges"]
    )
    assert not any(
        edge["target"]["type"] == "Asset" and edge["target"]["id"] == ""
        for edge in film_ir["edges"]
    )
    assert any(
        edge["source"]["type"] == "GenerationRecord"
        and edge["source"]["id"] == retry_failure_id
        and edge["target"]["type"] == "GenerationRecord"
        and edge["target"]["id"] == failure_id
        and edge["relation"] == "RETRY_OF"
        for edge in film_ir["edges"]
    )
    failures_response = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/director-generation-failures",
        params={"script_scene_id": SCENE_ID},
    )
    assert failures_response.status_code == 200, failures_response.text
    failures = failures_response.json()["data"]
    assert [item["generation_record_id"] for item in failures] == [
        retry_failure_id,
        failure_id,
    ]
    assert failures[0]["retry_of_generation_record_id"] == failure_id
    assert failures[0]["error_code"] == "ARK_TEXT_SCHEMA_INVALID"
    assert failures[0]["provider_request_id"] == "request-3"
    assert failures[0]["attempt_count"] == 3
    assert failures[0]["repair_attempts"] == 2


@pytest.mark.anyio
async def test_director_in_progress_reservation_blocks_duplicate_provider_call(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    request_payload = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "target_id": SCENE_ID,
        "issue_types": ["PACING"],
        "actor": "test-director",
    }
    request_fingerprint = content_hash(
        DirectorProposalRequest.model_validate(request_payload).model_dump(mode="json")
    )
    idempotency_key = "director-in-progress-v1"
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        reservation, replay = director_api._reserve_director_request(
            session,
            project_id=PROJECT_ID,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        assert reservation is not None
        assert replay is None
    with factory() as session:
        with pytest.raises(HTTPException) as caught:
            director_api._reserve_director_request(
                session,
                project_id=PROJECT_ID,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "DIRECTOR_REQUEST_IN_PROGRESS"

    async def fail_if_called(*_args, **_kwargs) -> TextGenerationResult:
        raise AssertionError("已有幂等占位时不得再次调用 Provider")

    monkeypatch.setattr(
        director_proposals,
        "generate_director_scene_review",
        fail_if_called,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=request_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 409, response.text
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["error"]["code"] == "DIRECTOR_REQUEST_IN_PROGRESS"
    assert response.json()["error"]["retryable"] is True


@pytest.mark.anyio
async def test_director_expired_reservation_can_safely_start_again(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    request_payload = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "target_id": SCENE_ID,
        "issue_types": ["PACING"],
        "actor": "test-director",
    }
    request_fingerprint = content_hash(
        DirectorProposalRequest.model_validate(request_payload).model_dump(mode="json")
    )
    idempotency_key = "director-expired-reservation-v1"
    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        reservation, _ = director_api._reserve_director_request(
            session,
            project_id=PROJECT_ID,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        assert reservation is not None
        reservation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    original_generate = director_proposals.generate_director_scene_review
    provider_calls = 0

    async def counted_generate(*args, **kwargs) -> TextGenerationResult:  # noqa: ANN002, ANN003
        nonlocal provider_calls
        provider_calls += 1
        return await original_generate(*args, **kwargs)

    monkeypatch.setattr(
        director_proposals,
        "generate_director_scene_review",
        counted_generate,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=request_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 201, response.text
    assert response.headers["Idempotency-Replayed"] == "false"
    assert provider_calls == 1


@pytest.mark.anyio
async def test_director_success_records_repair_attempts_and_latency(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    original_generate = director_proposals.generate_director_scene_review

    async def fake_generate(*args, **kwargs) -> TextGenerationResult:  # noqa: ANN002, ANN003
        generated = await original_generate(*args, **kwargs)
        return TextGenerationResult(
            payload=generated.payload,
            provider="volcengine-ark",
            model="test-director-model",
            request_id="request-after-repair",
            repair_attempts=2,
        )

    monkeypatch.setattr(
        director_proposals,
        "generate_director_scene_review",
        fake_generate,
    )
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            "expected_version": 8,
            "target_type": "SCRIPT_SCENE",
            "target_id": SCENE_ID,
            "issue_types": ["PACING"],
            "actor": "test-director",
        },
        headers={"Idempotency-Key": "director-repaired-success-v1"},
    )
    assert response.status_code == 201, response.text

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        record = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
            )
        )
        assert record is not None
        assert record.status == "SUCCEEDED"
        assert record.provider == "volcengine-ark"
        assert record.model == "test-director-model"
        assert record.provider_request_id == "request-after-repair"
        assert record.latency_ms is not None
        metadata = json.loads(record.metadata_json)
        assert metadata["repair_attempts"] == 2
        assert metadata["media_generation"] is False


@pytest.mark.anyio
async def test_director_explicit_retry_requires_new_key_and_records_lineage(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_script()
    original_generate = director_proposals.generate_director_scene_review
    provider_calls = 0

    async def fail_once_then_succeed(
        *args,
        **kwargs,
    ) -> TextGenerationResult:  # noqa: ANN002, ANN003
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            raise TextProviderError(
                "ARK_TEXT_SCHEMA_INVALID",
                "首次 Director 审查结构无效",
                retryable=True,
                details={
                    "last_request_id": "retry-source-request",
                    "attempts": [
                        {
                            "attempt": 1,
                            "request_id": "retry-source-request",
                            "error_type": "validation_error",
                            "validation_error": "invalid output",
                        }
                    ],
                },
            )
        return await original_generate(*args, **kwargs)

    monkeypatch.setattr(
        director_proposals,
        "generate_director_scene_review",
        fail_once_then_succeed,
    )
    request_payload = {
        "expected_version": 8,
        "target_type": "SCRIPT_SCENE",
        "target_id": SCENE_ID,
        "issue_types": ["PACING"],
        "actor": "test-director",
    }
    initial_key = "director-retry-source-v1"
    failed_response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=request_payload,
        headers={"Idempotency-Key": initial_key},
    )
    assert failed_response.status_code == 503, failed_response.text
    assert provider_calls == 1

    factory = sessionmaker(
        bind=get_engine(get_settings().database_url),
        expire_on_commit=False,
    )
    with factory() as session:
        failed_record = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                GenerationRecord.status == "FAILED",
            )
        )
        assert failed_record is not None
        failed_record_id = failed_record.id

    retry_payload = {
        **request_payload,
        "retry_of_generation_record_id": failed_record_id,
    }
    reused_key = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=retry_payload,
        headers={"Idempotency-Key": initial_key},
    )
    assert reused_key.status_code == 409, reused_key.text
    assert reused_key.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert provider_calls == 1

    retried = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json=retry_payload,
        headers={"Idempotency-Key": "director-explicit-retry-v1"},
    )
    assert retried.status_code == 201, retried.text
    proposal = retried.json()["data"]
    assert proposal["retry_of_generation_record_id"] == failed_record_id
    assert provider_calls == 2

    with factory() as session:
        succeeded_record = session.scalar(
            select(GenerationRecord).where(
                GenerationRecord.project_id == PROJECT_ID,
                GenerationRecord.capability == "DIRECTOR_SCENE_REVIEW",
                GenerationRecord.status == "SUCCEEDED",
            )
        )
        assert succeeded_record is not None
        succeeded_metadata = json.loads(succeeded_record.metadata_json)
        assert succeeded_metadata["retry_of_generation_record_id"] == failed_record_id
        succeeded_record_id = succeeded_record.id

    history_response = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/director-generation-history",
        params={"script_scene_id": SCENE_ID},
    )
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()["data"]
    assert [item["generation_record_id"] for item in history] == [
        succeeded_record_id,
        failed_record_id,
    ]
    assert history[0]["status"] == "SUCCEEDED"
    assert history[0]["script_scene_id"] == SCENE_ID
    assert history[0]["script_version_id"] == SCRIPT_ID
    assert history[0]["proposal_id"] == proposal["proposal_id"]
    assert history[0]["proposal_status"] == "PROPOSED"
    assert history[0]["retry_of_generation_record_id"] == failed_record_id
    assert history[0]["attempt_count"] == history[0]["repair_attempts"] + 1
    assert history[1]["status"] == "FAILED"
    assert history[1]["proposal_id"] is None

    invalid_source = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/director-review-proposals",
        json={
            **request_payload,
            "retry_of_generation_record_id": succeeded_record_id,
        },
        headers={"Idempotency-Key": "director-invalid-retry-source-v1"},
    )
    assert invalid_source.status_code == 409, invalid_source.text
    assert invalid_source.json()["error"]["code"] == "DIRECTOR_RETRY_SOURCE_INVALID"
    assert provider_calls == 2

    film_ir_response = await client.get(f"/api/v1/projects/{PROJECT_ID}/film-ir")
    assert film_ir_response.status_code == 200, film_ir_response.text
    film_ir = film_ir_response.json()["data"]
    succeeded_node = next(
        item
        for item in film_ir["objects"]
        if item["type"] == "GenerationRecord" and item["id"] == succeeded_record_id
    )
    assert (
        succeeded_node["attributes"]["retry_of_generation_record_id"]
        == failed_record_id
    )
    assert any(
        edge["source"]["type"] == "GenerationRecord"
        and edge["source"]["id"] == succeeded_record_id
        and edge["target"]["type"] == "GenerationRecord"
        and edge["target"]["id"] == failed_record_id
        and edge["relation"] == "RETRY_OF"
        for edge in film_ir["edges"]
    )


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
