import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.config import get_settings
from app.db.models import (
    Asset,
    Character,
    ExportArtifact,
    ExportRecord,
    Job,
    Project,
    ProposalVersion,
    Shot,
    Take,
)
from app.db.session import get_engine
from app.jobs.contracts import JobExecutionError
from app.jobs.handlers.proposal import (
    _await_with_progress,
    generate_story_directions,
    generate_story_structure,
)
from app.jobs.registry import registered_job_types
from app.jobs.worker import PersistentJobWorker
from app.seed import PROJECT_ID
from app.services.events import latest_event_sequence, list_events
from app.services.jobs import (
    claim_next_job,
    enqueue_job,
    finish_job_failure,
    reconcile_terminal_project_jobs,
    recover_expired_jobs,
    update_job_diagnostics,
    update_job_progress,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.anyio

CREATE_PAYLOAD = {
    "name": "雨停以后",
    "idea": "暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。",
    "genre": "urban_suspense",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "reference_asset_ids": [],
    "assumptions": [],
    "narrative_protagonist": "ensemble",
    "target_audience": "general",
    "emotional_rewards": ["identity"],
    "audience_profile": "",
    "production_format": "live_action",
}

EXPECTED_JOB_TYPES = {
    "GENERATE_PROPOSAL",
    "GENERATE_STORY_DIRECTIONS",
    "GENERATE_STORY_PACKAGE",
    "GENERATE_STORY_STRUCTURE",
    "GENERATE_SCRIPT_PACKAGE",
    "GENERATE_CHARACTER_CANDIDATES",
    "GENERATE_CHARACTER_CANDIDATE",
    "GENERATE_CHARACTER_VISUAL_CANDIDATE",
    "GENERATE_CHARACTER_IDENTITY_DOSSIER",
    "GENERATE_CHARACTER_LOOKS",
    "PREPARE_PREPRODUCTION_ASSETS",
    "GENERATE_STORYBOARD_V2",
    "GENERATE_STORYBOARD_TAKE",
    "GENERATE_ANIMATIC",
    "START_MEDIA_PRODUCTION",
    "GENERATE_KEYFRAME_TAKE",
    "GENERATE_VIDEO_TAKE_V2",
    "GENERATE_AUDIO_PIPELINE",
    "GENERATE_AUDIO_TAKE",
    "GENERATE_LIP_SYNC_BATCH",
    "ASSEMBLE_MULTITRACK_TIMELINE",
    "EXPORT_PACKAGE_V2",
    "GENERATE_STORYBOARDS",
    "GENERATE_HERO_FIXTURE",
    "ASSEMBLE_PREVIEW",
    "APPLY_REVISION",
    "EXPORT_PACKAGE",
    "DEMO_RENDER",
    "GENERATE_SHOT_IMAGE",
    "GENERATE_SHOT_VIDEO",
}


async def test_long_text_generation_emits_intermediate_progress() -> None:
    updates: list[tuple[float, str]] = []

    async def checkpoint(
        _session: object,
        _job: object,
        progress: float,
        stage: str,
    ) -> None:
        updates.append((progress, stage))

    async def operation() -> str:
        await asyncio.sleep(0.055)
        return "ready"

    result = await _await_with_progress(
        SimpleNamespace(checkpoint=checkpoint),
        SimpleNamespace(),
        SimpleNamespace(attempt=2, max_attempts=3),
        operation(),
        initial_progress=15,
        ceiling=30,
        stage="正在生成 3 个差异化故事方向",
        interval_seconds=0.01,
    )

    assert result == "ready"
    progresses = [progress for progress, _ in updates]
    assert progresses[:3] == [20, 25, 30]
    assert len(progresses) >= 4
    assert all(progress == 30 for progress in progresses[3:])
    assert {stage for _, stage in updates} == {
        "正在生成 3 个差异化故事方向 · 已等待 0 秒 · 任务尝试 2/3"
    }


async def test_story_direction_generation_has_a_hard_total_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def checkpoint(*_args: object) -> None:
        return None

    async def slow_generation(*_args: object) -> object:
        await asyncio.sleep(1)
        raise AssertionError("the total timeout should cancel the provider call")

    monkeypatch.setattr(
        "app.jobs.handlers.proposal.RoutedTextProvider.generate_directions",
        slow_generation,
    )
    context = SimpleNamespace(
        settings=replace(
            get_settings(),
            ark_api_key="test-key",
            ark_request_timeout_seconds=0.02,
        ),
        checkpoint=checkpoint,
    )
    job = SimpleNamespace(attempt=1, max_attempts=2)

    with pytest.raises(JobExecutionError) as caught:
        await generate_story_directions(
            context,
            SimpleNamespace(),
            job,
            {"brief": {}},
        )

    assert caught.value.code == "ARK_TEXT_TIMEOUT"
    assert caught.value.retryable is True
    assert caught.value.details == {"timeout_seconds": 0.02}


async def test_story_structure_progress_enters_relationship_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[float, str]] = []
    diagnostics: list[dict[str, object]] = []
    job = SimpleNamespace(attempt=1, max_attempts=3, progress=0)

    async def checkpoint(
        _session: object,
        current_job: object,
        progress: float,
        stage: str,
    ) -> None:
        current_job.progress = max(current_job.progress, progress)
        updates.append((progress, stage))

    async def fake_generate_story_structure(
        _provider: object,
        _settings: object,
        _brief: dict[str, object],
        _direction: dict[str, object],
        *,
        on_model_output=None,  # noqa: ANN001
        on_validation_failure=None,  # noqa: ANN001
    ) -> object:
        assert on_model_output is not None
        assert on_validation_failure is not None
        await on_validation_failure(
            1,
            {
                "attempt": 1,
                "error_type": "validation_error",
                "validation_error": "sequence 必须连续",
            },
        )
        await on_model_output(2)
        return SimpleNamespace(provider="test", model="test")

    async def record_diagnostics(
        _session: object,
        _job: object,
        details: dict[str, object],
    ) -> None:
        diagnostics.append(details)

    monkeypatch.setattr(
        "app.jobs.handlers.proposal.RoutedTextProvider.generate_story_structure",
        fake_generate_story_structure,
    )
    monkeypatch.setattr(
        "app.jobs.handlers.proposal.materialize_story_structure",
        lambda *_args: SimpleNamespace(id="graph-id", story_bible_version_id="bible-id"),
    )
    result = await generate_story_structure(
        SimpleNamespace(
            settings=get_settings(),
            checkpoint=checkpoint,
            record_diagnostics=record_diagnostics,
        ),
        SimpleNamespace(),
        job,
        {"brief": {}, "direction": {}},
    )

    assert (72, "第 1/3 次模型输出未通过校验，正在生成修复版") in updates
    assert (74, "模型修复输出完成，正在校验角色关系（1/2）") in updates
    assert (82, "角色关系校验通过") in updates
    assert diagnostics[0]["model_attempt"] == 1
    assert diagnostics[0]["attempts"] == [
        {
            "attempt": 1,
            "error_type": "validation_error",
            "validation_error": "sequence 必须连续",
        }
    ]
    assert result["relationship_graph_id"] == "graph-id"


async def test_worker_loop_survives_unexpected_run_once_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = PersistentJobWorker(replace(get_settings(), worker_poll_interval=0.001))
    calls = 0

    async def flaky_run_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated loop failure")
        worker._stop_event.set()
        return False

    def unavailable_factory():  # noqa: ANN202
        raise RuntimeError("heartbeat unavailable")

    monkeypatch.setattr(worker, "run_once", flaky_run_once)
    monkeypatch.setattr(worker, "_factory", unavailable_factory)

    await worker._run()

    assert calls == 2


async def test_worker_heartbeat_continues_while_handler_is_awaiting(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del client
    with Session(get_engine(get_settings().database_url)) as session:
        enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:slow-worker-heartbeat",
            input_payload={"steps": 1},
            label="慢任务心跳测试",
            stage="等待测试",
            trace_id="slow-worker-heartbeat-trace",
        )
        session.commit()

    worker = PersistentJobWorker(
        replace(
            get_settings(),
            worker_heartbeat_stale_seconds=0.2,
            worker_lease_seconds=1,
        )
    )
    heartbeat_statuses: list[str] = []
    original_heartbeat = worker._heartbeat

    def record_heartbeat(
        session: Session,
        status: str,
        current_job_id: str | None,
    ) -> None:
        heartbeat_statuses.append(status)
        original_heartbeat(session, status, current_job_id)

    async def slow_execute(*_args: object) -> dict[str, object]:
        await asyncio.sleep(0.26)
        return {"ok": True}

    monkeypatch.setattr(worker, "_heartbeat", record_heartbeat)
    monkeypatch.setattr(worker, "_execute", slow_execute)

    assert await worker.run_once() is True
    assert heartbeat_statuses.count("RUNNING") >= 3
    assert heartbeat_statuses[-1] == "IDLE"


async def _create_draft(client: AsyncClient, key: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201
    return response.json()["data"]["project"]


async def _run_worker_until_project_status(
    client: AsyncClient,
    worker: PersistentJobWorker,
    project_id: str,
    target_status: str,
    *,
    max_runs: int,
) -> dict[str, object]:
    for _ in range(max_runs):
        project = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
        if project["status"] == target_status:
            return project
        if not await worker.run_once():
            jobs = (await client.get(f"/api/v1/projects/{project_id}/jobs")).json()["data"]
            summary = [
                {
                    "job_type": item["job_type"],
                    "status": item["status"],
                    "error_code": item["error_code"],
                }
                for item in jobs
            ]
            pytest.fail(
                f"任务队列已耗尽，但项目状态仍为 {project['status']}，"
                f"目标状态为 {target_status}；任务摘要：{summary}"
            )
    project = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    if project["status"] == target_status:
        return project
    pytest.fail(
        f"执行 {max_runs} 个任务后项目状态仍为 {project['status']}，目标状态为 {target_status}"
    )


async def test_global_jobs_aggregates_multiple_projects(client: AsyncClient) -> None:
    project = await _create_draft(client, "global-jobs-project-v1")
    with Session(get_engine(get_settings().database_url)) as session:
        seeded_job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:global-jobs-seeded",
            input_payload={"steps": 1},
            label="演示项目任务",
            stage="等待测试",
            trace_id="global-jobs-seeded-trace",
        )
        created_job, _ = enqueue_job(
            session,
            project_id=str(project["id"]),
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=str(project["id"]),
            idempotency_key="test:global-jobs-created",
            input_payload={"steps": 1},
            label="新项目任务",
            stage="等待测试",
            trace_id="global-jobs-created-trace",
        )
        session.commit()
        seeded_job_id = seeded_job.id
        created_job_id = created_job.id
        expected_ids = {seeded_job_id, created_job_id}

    global_jobs = (await client.get("/api/v1/jobs")).json()["data"]
    scoped_jobs = (await client.get(f"/api/v1/projects/{project['id']}/jobs")).json()["data"]

    assert expected_ids <= {job["id"] for job in global_jobs}
    assert {job["project_id"] for job in global_jobs} >= {PROJECT_ID, project["id"]}
    assert created_job_id in {job["id"] for job in scoped_jobs}
    assert seeded_job_id not in {job["id"] for job in scoped_jobs}


async def test_job_api_exposes_relationship_semantic_error_details(client: AsyncClient) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="GENERATE_STORY_STRUCTURE",
            entity_type="proposal_version",
            entity_id=PROJECT_ID,
            idempotency_key="test:relationship-semantic-error-details",
            input_payload={},
            label="关系语义错误详情测试",
            stage="等待测试",
            trace_id="relationship-semantic-error-details-trace",
        )
        session.commit()
        claimed = claim_next_job(session, "relationship-error-worker", 15)
        assert claimed is not None and claimed.id == job.id
        finish_job_failure(
            session,
            job_id=job.id,
            worker_id="relationship-error-worker",
            code="RELATIONSHIP_GRAPH_SEMANTIC_INVALID",
            message="角色关系语义校验失败：1 条隐藏关系缺少揭示计划",
            details={
                "issues": [
                    {
                        "severity": "BLOCKER",
                        "code": "HIDDEN_RELATIONSHIP_WITHOUT_REVEAL",
                        "message": "缺少揭示计划",
                        "relationship_key": "lead-rival",
                    }
                ],
                "hidden_relationship_count": 1,
            },
            retryable=False,
        )
        job_id = job.id

    response = await client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["error_code"] == "RELATIONSHIP_GRAPH_SEMANTIC_INVALID"
    assert payload["error_details"]["hidden_relationship_count"] == 1
    assert payload["error_details"]["issues"][0]["relationship_key"] == "lead-rival"


async def test_worker_registry_covers_every_persisted_job_type() -> None:
    assert registered_job_types() == EXPECTED_JOB_TYPES


async def test_proposal_job_is_persisted_idempotent_and_processed(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "job-proposal-create-v1")
    endpoint = f"/api/v1/projects/{project['id']}/director-proposals"
    missing_header = await client.post(endpoint, json={"expected_version": 1})
    assert missing_header.status_code == 422

    created = await client.post(
        endpoint,
        json={"expected_version": 1},
        headers={"Idempotency-Key": "generate-proposal-v1"},
    )
    assert created.status_code == 202
    assert created.headers["Idempotency-Replayed"] == "false"
    job = created.json()["data"]
    assert job["status"] == "PENDING"
    assert job["job_type"] == "GENERATE_PROPOSAL"

    replay = await client.post(
        endpoint,
        json={"expected_version": 1},
        headers={"Idempotency-Key": "different-request-key-v1"},
    )
    assert replay.status_code == 202
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["data"]["id"] == job["id"]

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True

    completed = (await client.get(f"/api/v1/jobs/{job['id']}")).json()["data"]
    assert completed["status"] == "SUCCEEDED"
    assert completed["progress"] == 100
    proposals = (await client.get(endpoint)).json()["data"]
    assert len(proposals) == 1
    assert proposals[0]["provider"] == "mock"
    assert len(proposals[0]["payload"]["scenes"]) == 3
    assert sum(len(scene["shots"]) for scene in proposals[0]["payload"]["scenes"]) == 8
    current = (await client.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert current["status"] == "PROPOSAL_READY"
    assert current["lock_version"] == 3

    events = (await client.get(f"/api/v1/projects/{project['id']}/events")).json()["data"]
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert {event["event_type"] for event in events} >= {
        "job.created",
        "job.running",
        "job.succeeded",
        "proposal.ready",
    }
    resumed = await client.get(
        f"/api/v1/projects/{project['id']}/events",
        headers={"Last-Event-ID": str(events[-2]["sequence"])},
    )
    assert [event["sequence"] for event in resumed.json()["data"]] == [events[-1]["sequence"]]

    with Session(get_engine(get_settings().database_url)) as session:
        assert latest_event_sequence(session, project["id"]) == events[-1]["sequence"]
        assert session.scalar(
            select(ProposalVersion).where(ProposalVersion.project_id == project["id"])
        )


@pytest.mark.parametrize("target_duration", [45, 90])
async def test_target_duration_scales_all_shots_without_timeline_gap(
    client: AsyncClient,
    target_duration: int,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json={**CREATE_PAYLOAD, "target_duration_sec": target_duration},
        headers={"Idempotency-Key": f"job-proposal-{target_duration}-second-v1"},
    )
    project = created.json()["data"]["project"]
    endpoint = f"/api/v1/projects/{project['id']}/director-proposals"
    queued = await client.post(
        endpoint,
        json={"expected_version": 1},
        headers={"Idempotency-Key": f"generate-proposal-{target_duration}-second-v1"},
    )
    worker = PersistentJobWorker(get_settings())
    assert queued.status_code == 202 and await worker.run_once() is True
    proposal = (await client.get(endpoint)).json()["data"][0]["payload"]
    durations = [shot["duration_sec"] for scene in proposal["scenes"] for shot in scene["shots"]]
    assert len(durations) == 8
    assert sum(durations) == proposal["total_duration_sec"] == target_duration
    assert sum(scene["duration_sec"] for scene in proposal["scenes"]) == target_duration


async def test_blocking_brief_questions_prevent_proposal_generation(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json={**CREATE_PAYLOAD, "blocking_questions": ["主角是否必须保留婚姻关系？"]},
        headers={"Idempotency-Key": "brief-blocking-question-v1"},
    )
    project = created.json()["data"]["project"]
    response = await client.post(
        f"/api/v1/projects/{project['id']}/director-proposals",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "blocked-proposal-v1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BRIEF_QUESTIONS_REQUIRED"
    assert response.json()["error"]["details"]["questions"] == ["主角是否必须保留婚姻关系？"]


async def test_story_generation_requires_explicit_independent_targeting(
    client: AsyncClient,
) -> None:
    incomplete_payload = {
        key: value
        for key, value in CREATE_PAYLOAD.items()
        if key not in {"narrative_protagonist", "emotional_rewards"}
    }
    created = await client.post(
        "/api/v1/projects",
        json=incomplete_payload,
        headers={"Idempotency-Key": "incomplete-narrative-targeting-v1"},
    )
    project = created.json()["data"]["project"]

    response = await client.post(
        f"/api/v1/projects/{project['id']}/story-directions",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "blocked-targeting-generation-v1"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NARRATIVE_TARGETING_REQUIRED"
    assert response.json()["error"]["details"]["missing_fields"] == [
        "叙事主角",
        "情绪回报",
    ]


async def test_confirming_direction_retries_existing_failed_story_structure_job(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "story-retry-create-v1")
    project_id = str(project["id"])
    queued = await client.post(
        f"/api/v1/projects/{project_id}/story-directions",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "story-retry-directions-v1"},
    )
    assert queued.status_code == 202
    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    directions = (await client.get(f"/api/v1/projects/{project_id}/story-directions")).json()[
        "data"
    ]
    direction = directions[0]
    approved = await client.post(
        f"/api/v1/projects/{project_id}/story-dna/{direction['version']}/approve",
        json={"expected_version": 3, "actor": "test-writer"},
        headers={"Idempotency-Key": "story-retry-first-approval-v1"},
    )
    assert approved.status_code == 202
    job_id = approved.json()["data"]["id"]

    with Session(get_engine(get_settings().database_url)) as session:
        claimed = claim_next_job(session, "story-retry-worker", 15)
        assert claimed is not None and claimed.id == job_id
        finish_job_failure(
            session,
            job_id=job_id,
            worker_id="story-retry-worker",
            code="ARK_TEXT_SCHEMA_INVALID",
            message="模拟故事结构失败",
            details={"attempts": []},
            retryable=False,
        )

    retried = await client.post(
        f"/api/v1/projects/{project_id}/story-dna/{direction['version']}/approve",
        json={"expected_version": 3, "actor": "test-writer"},
        headers={"Idempotency-Key": "story-retry-second-approval-v1"},
    )

    assert retried.status_code == 202
    assert retried.headers["Idempotency-Replayed"] == "false"
    assert retried.json()["data"]["id"] == job_id
    assert retried.json()["data"]["status"] == "RETRY_WAIT"
    assert retried.json()["data"]["stage"] == "等待重试"
    assert retried.json()["data"]["error_code"] is None


async def test_story_directions_to_approved_script_flow(client: AsyncClient) -> None:
    project = await _create_draft(client, "story-v2-create-v1")
    project_id = str(project["id"])
    queued = await client.post(
        f"/api/v1/projects/{project_id}/story-directions",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "story-directions-v1"},
    )
    assert queued.status_code == 202
    assert queued.json()["data"]["max_attempts"] == 2
    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    directions = (await client.get(f"/api/v1/projects/{project_id}/story-directions")).json()[
        "data"
    ]
    assert len(directions) == 3
    assert {item["direction_key"] for item in directions} == {"emotion", "plot", "market"}
    assert all(item["provider"] == "mock" for item in directions)
    assert all(
        item["schema_version"] == "story-direction-v3-independent-targeting" for item in directions
    )
    assert all(len(item["payload"]["key_turns"]) >= 3 for item in directions)
    assert all(item["payload"]["sequel_setup"]["next_installment_objective"] for item in directions)
    assert all("？" not in item["payload"]["story_dna"]["ending_hook"] for item in directions)
    assert all(
        sum(scene["duration_sec"] for scene in item["payload"]["scenes"])
        == item["payload"]["total_duration_sec"]
        for item in directions
    )
    merged = await client.post(
        f"/api/v1/projects/{project_id}/story-directions/merge",
        json={
            "expected_version": 3,
            "source_proposal_ids": [directions[0]["id"], directions[1]["id"]],
            "title": "情绪与强情节融合版",
        },
        headers={"Idempotency-Key": "story-direction-merge-v1"},
    )
    assert merged.status_code == 200
    merged_direction = merged.json()["data"]
    assert merged_direction["direction_key"] == "merged"
    assert len(merged_direction["source_proposal_ids"]) == 2

    estimate = (await client.get(f"/api/v1/projects/{project_id}/story-package-estimate")).json()[
        "data"
    ]
    assert estimate == {
        "assets": ["故事设定", "角色文字设定", "角色关系网"],
        "estimated_seconds": 5,
        "estimated_points": 0,
        "direction_lock": "ON_SUCCESS",
        "version_strategy": "CREATE_NEW_VERSION",
    }

    structure_job = await client.post(
        f"/api/v1/projects/{project_id}/story-dna/{merged_direction['version']}/approve",
        json={"expected_version": 4, "actor": "test-writer"},
        headers={"Idempotency-Key": "story-package-v1"},
    )
    assert structure_job.status_code == 202
    assert structure_job.json()["data"]["job_type"] == "GENERATE_STORY_STRUCTURE"
    assert await worker.run_once() is True
    workspace = (await client.get(f"/api/v1/projects/{project_id}/story-workspace")).json()["data"]
    assert len(workspace["story_dna_versions"]) == 1
    assert len(workspace["story_bible_versions"]) == 1
    assert "relationships" not in workspace["story_bible_versions"][0]["payload"]
    assert len(workspace["relationship_graph_versions"]) == 1
    assert len(workspace["episode_outline_versions"]) == 0
    assert len(workspace["script_versions"]) == 0
    graph = workspace["relationship_graph_versions"][0]
    assert graph["status"] == "DRAFT"
    assert graph["editability"]["semantic_editable"] is True
    assert graph["editability"]["can_approve"] is True

    graph_approval = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={
            "expected_project_version": 6,
            "expected_graph_version": 1,
            "actor": "test-writer",
        },
    )
    assert graph_approval.status_code == 200, graph_approval.text
    approval_data = graph_approval.json()["data"]
    assert approval_data["status"] == "APPROVED"
    assert approval_data["character_visuals"]["character_count"] == 2
    assert approval_data["editability"]["reason_code"] == "GRAPH_APPROVED"

    visual_workspace = (
        await client.get(f"/api/v1/projects/{project_id}/character-visuals")
    ).json()["data"]
    assert all(not item["candidates"] for item in visual_workspace["characters"])
    script_job = None
    for character in visual_workspace["characters"]:
        profile = character["profile"]
        confirmed = await client.post(
            f"/api/v1/projects/{project_id}/characters/{character['id']}/visual-profile/confirm",
            json={
                "expected_version": character["lock_version"],
                "profile_version_id": profile["id"],
                "actor": "test-writer",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        current = (await client.get(f"/api/v1/projects/{project_id}/character-visuals")).json()[
            "data"
        ]
        current_character = next(
            item for item in current["characters"] if item["id"] == character["id"]
        )
        generated = await client.post(
            f"/api/v1/projects/{project_id}/characters/{character['id']}/visual-candidates",
            json={
                "expected_version": current_character["lock_version"],
                "profile_version_id": profile["id"],
                "count": 3,
                "actor": "test-writer",
            },
        )
        assert generated.status_code == 202, generated.text
        for _ in range(3):
            assert await worker.run_once() is True
        current = (await client.get(f"/api/v1/projects/{project_id}/character-visuals")).json()[
            "data"
        ]
        current_character = next(
            item for item in current["characters"] if item["id"] == character["id"]
        )
        assert len(current_character["candidates"]) == 3
        selected = await client.post(
            f"/api/v1/projects/{project_id}/characters/{character['id']}/visual-candidates/select",
            json={
                "expected_version": current_character["lock_version"],
                "candidate_id": current_character["candidates"][0]["id"],
                "actor": "test-writer",
            },
        )
        assert selected.status_code == 202, selected.text
        for _ in range(5):
            assert await worker.run_once() is True
        current = (await client.get(f"/api/v1/projects/{project_id}/character-visuals")).json()[
            "data"
        ]
        current_character = next(
            item for item in current["characters"] if item["id"] == character["id"]
        )
        identity = current_character["identities"][-1]
        assert len(identity["assets"]) == 5
        locked = await client.post(
            f"/api/v1/projects/{project_id}/characters/{character['id']}/identity/lock",
            json={
                "expected_version": current_character["lock_version"],
                "identity_version_id": identity["id"],
                "actor": "test-writer",
            },
        )
        assert locked.status_code == 200, locked.text
        script_job = locked.json()["data"]["script_job"] or script_job
    assert script_job is not None
    assert script_job["job_type"] == "GENERATE_SCRIPT_PACKAGE"
    assert await worker.run_once() is True

    workspace = (await client.get(f"/api/v1/projects/{project_id}/story-workspace")).json()["data"]
    assert len(workspace["episode_outline_versions"]) == 1
    assert len(workspace["script_versions"]) == 1
    script = workspace["script_versions"][0]
    assert script["status"] == "READY_FOR_REVIEW"
    assert script["config_version"] == "story-package-v5-independent-targeting"
    assert script["schema_version"] == "script-v4-relationship-driven"
    assert script["relationship_graph_version_id"] == graph["id"]
    assert workspace["current_script_relationship_graph_id"] == graph["id"]
    assert workspace["relationship_graph_stale"] is False
    assert script["estimated_duration_ms"] == 60_000
    assert len(script["scenes"]) == 3
    assert sum(len(scene["lines"]) for scene in script["scenes"]) == 6
    assert script["critic"]["status"] == "PASS_WITH_NOTES"
    engine = script["payload"]["short_drama_engine"]
    assert engine["formula_version"] == "short-drama-v1"
    assert engine["protagonist_desire"]
    assert len(engine["reversal_chain"]) >= 2
    assert [item["beat_type"] for item in engine["beats"]][-1] == "CONTINUATION_HOOK"
    breakout = script["payload"]["breakout_engine"]
    assert breakout["formula_version"] == "breakout-drama-v1"
    assert breakout["vulnerable_shell"]
    assert breakout["elite_core"]
    assert len(breakout["misjudgment_chain"]) >= 2
    assert len(breakout["authentication_ladder"]) >= 2
    assert breakout["relationship_reorders"]
    relationship_reorder = breakout["relationship_reorders"][0]
    assert relationship_reorder["source_character_key"] == "protagonist"
    assert relationship_reorder["target_character_key"] == "witness"
    assert relationship_reorder["relationship_beat_id"]
    assert relationship_reorder["before_state"]["trust_level"] == -2
    assert relationship_reorder["after_state"]["trust_level"] == 0
    assert breakout["sequel_unit"]["current_unit_closure"] == engine["stage_closure"]
    assert breakout["sequel_unit"]["next_unit_trigger"] == engine["continuation_hook"]

    line_revision = await client.patch(
        f"/api/v1/scripts/{script['id']}/lines/{script['scenes'][0]['lines'][0]['id']}",
        json={"expected_version": 9, "text": "灯灭时，每个人都收到同一张旧照片。"},
    )
    assert line_revision.status_code == 200
    workspace = (await client.get(f"/api/v1/projects/{project_id}/story-workspace")).json()["data"]
    script = workspace["script_versions"][0]
    assert script["version"] == 2
    assert script["scenes"][0]["lines"][0]["text"] == "灯灭时，每个人都收到同一张旧照片。"

    scene_revision = await client.patch(
        f"/api/v1/scripts/{script['id']}/scenes/{script['scenes'][0]['id']}",
        json={"expected_version": 10, "emotion": "高度警觉", "sfx_intents": ["雷声"]},
    )
    assert scene_revision.status_code == 200
    workspace = (await client.get(f"/api/v1/projects/{project_id}/story-workspace")).json()["data"]
    script = workspace["script_versions"][0]
    assert script["version"] == 3
    assert script["scenes"][0]["emotion"] == "高度警觉"

    episode_revision = await client.patch(
        f"/api/v1/scripts/{script['id']}",
        json={"expected_version": 11, "title": "暴雨旧照"},
    )
    assert episode_revision.status_code == 200
    workspace = (await client.get(f"/api/v1/projects/{project_id}/story-workspace")).json()["data"]
    script = workspace["script_versions"][0]
    assert script["version"] == 4
    assert script["payload"]["title"] == "暴雨旧照"

    approved = await client.post(
        f"/api/v1/scripts/{script['id']}/approve",
        json={"expected_version": 12, "actor": "test-writer"},
        headers={"Idempotency-Key": "approve-script-v1"},
    )
    assert approved.status_code == 202
    assert approved.json()["data"]["script"]["status"] == "APPROVED"
    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert project_after["status"] == "STORY_APPROVED"
    project_after = await _run_worker_until_project_status(
        client,
        worker,
        project_id,
        "PREPRODUCTION_READY",
        max_runs=10,
    )
    characters = (await client.get(f"/api/v1/projects/{project_id}/characters/candidates")).json()[
        "data"
    ]
    assert len(characters) == 2
    assert sorted(len(item["candidates"]) for item in characters) == [3, 3]
    assert project_after["status"] == "PREPRODUCTION_READY"

    current_version = project_after["lock_version"]

    preproduction = (await client.get(f"/api/v1/projects/{project_id}/preproduction")).json()[
        "data"
    ]
    assert len(preproduction["characters"]) == 2
    assert len(preproduction["looks"]) == 2
    assert len(preproduction["locations"]) >= 1
    assert len(preproduction["props"]) >= 1
    assert len(preproduction["voices"]) == 2
    assert all(item["cloning_enabled"] is False for item in preproduction["voices"])

    approved_preproduction = await client.post(
        f"/api/v1/projects/{project_id}/preproduction/approve",
        json={"expected_version": current_version, "actor": "test-director"},
        headers={"Idempotency-Key": "approve-preproduction-v1"},
    )
    assert approved_preproduction.status_code == 202
    assert await worker.run_once() is True
    for _ in range(6):
        assert await worker.run_once() is True
    assert await worker.run_once() is True

    storyboard_workspace = (
        await client.get(f"/api/v1/projects/{project_id}/storyboard-workspace")
    ).json()["data"]
    assert storyboard_workspace["storyboard"]["status"] == "READY_FOR_REVIEW"
    assert storyboard_workspace["storyboard"]["animatic_url"]
    assert len(storyboard_workspace["shots"]) == 6
    assert sum(item["duration_ms"] for item in storyboard_workspace["shots"]) == 60_000
    assert all(item["image_url"] for item in storyboard_workspace["shots"])
    assert storyboard_workspace["workflow"]["status"] == "WAITING_FOR_GATE"
    assert storyboard_workspace["workflow"]["current_gate"] == "G4_STORYBOARD"
    assert storyboard_workspace["gate"]["status"] == "PENDING_REVIEW"

    with Session(get_engine(get_settings().database_url)) as session:
        persisted_shots = list(
            session.scalars(
                select(Shot)
                .join(Shot.scene)
                .where(Shot.scene.has(episode_id=storyboard_workspace["storyboard"]["episode_id"]))
            ).all()
        )
        snapshot_before = {
            shot.id: (
                shot.character_identity_version_ids_json,
                shot.character_look_version_ids_json,
                shot.character_story_state_version_ids_json,
            )
            for shot in persisted_shots
        }
        assert snapshot_before
        assert all(json.loads(values[0]) for values in snapshot_before.values())
        character = session.scalar(
            select(Character).where(
                Character.project_id == project_id,
                Character.status == "LOCKED",
            )
        )
        assert character is not None
        character_id = character.id
        character_lock_version = character.lock_version

    changed_state = await client.post(
        f"/api/v1/projects/{project_id}/characters/{character_id}/changes",
        json={
            "expected_version": character_lock_version,
            "change_type": "STORY_STATE",
            "payload": {"label": "临时疲惫", "fatigue": "明显"},
            "actor": "test-writer",
        },
    )
    assert changed_state.status_code == 200
    assert changed_state.json()["data"]["existing_shots_preserved"] is True
    with Session(get_engine(get_settings().database_url)) as session:
        persisted_shots = list(session.scalars(select(Shot).where(Shot.id.in_(snapshot_before))))
        snapshot_after = {
            shot.id: (
                shot.character_identity_version_ids_json,
                shot.character_look_version_ids_json,
                shot.character_story_state_version_ids_json,
            )
            for shot in persisted_shots
        }
    assert snapshot_after == snapshot_before

    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    approved_storyboard = await client.post(
        f"/api/v1/storyboards/{storyboard_workspace['storyboard']['id']}/approve",
        json={"expected_version": project_after["lock_version"], "actor": "test-director"},
        headers={"Idempotency-Key": "approve-storyboard-v1"},
    )
    assert approved_storyboard.status_code == 202
    processed = 0
    for _ in range(60):
        if not await worker.run_once():
            break
        processed += 1
    assert processed >= 20

    audio_workspace = (await client.get(f"/api/v1/projects/{project_id}/audio-workspace")).json()[
        "data"
    ]
    assert audio_workspace["sound_brief"]["rights_status"] == "SYNTHETIC_OWNED"
    assert {item["type"] for item in audio_workspace["cues"]} >= {
        "DIALOGUE",
        "BGM",
        "AMBIENCE",
    }
    assert all(item["take"]["quality_status"] == "PASSED" for item in audio_workspace["cues"])
    assert all(item["source_video_preserved"] for item in audio_workspace["lip_sync"])

    timeline_workspace = (
        await client.get(f"/api/v1/projects/{project_id}/timeline-workspace")
    ).json()["data"]
    timeline = timeline_workspace["timeline"]
    assert timeline["status"] == "READY_FOR_G5"
    assert {item["type"] for item in timeline_workspace["tracks"]} == {
        "VIDEO",
        "DIALOGUE",
        "BGM",
        "AMBIENCE",
        "SFX",
        "SUBTITLE",
    }
    assert len(timeline_workspace["quality_checks"]) == 8
    assert all(item["status"] == "PASSED" for item in timeline_workspace["quality_checks"])
    assert timeline_workspace["gate"]["status"] == "PENDING"

    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    approved_g5 = await client.post(
        f"/api/v1/previews/{timeline['id']}/approve",
        json={"expected_version": project_after["lock_version"], "actor": "test-producer"},
        headers={"Idempotency-Key": "approve-g5-v1"},
    )
    assert approved_g5.status_code == 200
    assert approved_g5.json()["data"]["status"] == "APPROVED"

    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    first_profile = await client.post(
        f"/api/v1/projects/{project_id}/export-profiles",
        json={
            "expected_version": project_after["lock_version"],
            "name": "Douyin Vertical",
            "platform": "douyin",
            "aspect_ratio": "9:16",
            "width": 720,
            "height": 1280,
            "caption_mode": "BOTH",
            "languages": ["zh-CN", "en-US"],
            "actor": "test-producer",
        },
    )
    assert first_profile.status_code == 201
    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    second_profile = await client.post(
        f"/api/v1/projects/{project_id}/export-profiles",
        json={
            "expected_version": project_after["lock_version"],
            "name": "YouTube Shorts",
            "platform": "youtube_shorts",
            "aspect_ratio": "9:16",
            "width": 1080,
            "height": 1920,
            "caption_mode": "SIDECAR",
            "languages": ["zh-CN", "en-US"],
            "actor": "test-producer",
        },
    )
    assert second_profile.status_code == 201
    profile_ids = [first_profile.json()["data"]["id"], second_profile.json()["data"]["id"]]
    project_after = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    matrix = await client.post(
        f"/api/v1/projects/{project_id}/exports/matrix",
        json={
            "expected_version": project_after["lock_version"],
            "profile_ids": profile_ids,
            "languages": ["zh-CN", "en-US"],
            "actor": "test-producer",
        },
    )
    assert matrix.status_code == 202
    assert len(matrix.json()["data"]) == 4
    for _ in range(8):
        if not await worker.run_once():
            break

    exports = (await client.get(f"/api/v1/projects/{project_id}/exports")).json()["data"]
    matrix_exports = [item for item in exports if item["export_profile_id"]]
    assert len(matrix_exports) == 4
    assert all(item["status"] == "READY" for item in matrix_exports)
    assert {item["language"] for item in matrix_exports} == {"zh-CN", "en-US"}
    assert all(
        set(item["assets"])
        == {
            "mp4",
            "srt",
            "vtt",
            "manifest",
            "cover",
            "stems_manifest",
            "qc_report",
        }
        for item in matrix_exports
    )
    with Session(get_engine(get_settings().database_url)) as session:
        records = list(
            session.scalars(
                select(ExportRecord).where(ExportRecord.export_profile_id.in_(profile_ids))
            ).all()
        )
        assert len({item.picture_master_asset_id for item in records}) == 1
        assert len({(item.language, item.srt_asset_id) for item in records}) == 2
        assert (
            session.query(ExportArtifact)
            .filter(ExportArtifact.export_id.in_([item.id for item in records]))
            .count()
            == 28
        )


async def test_unchanged_progress_checkpoint_does_not_emit_duplicate_event(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "progress-dedup-create-v1")
    project_id = str(project["id"])
    queued = await client.post(
        f"/api/v1/projects/{project_id}/story-directions",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "progress-dedup-directions-v1"},
    )
    job_id = queued.json()["data"]["id"]

    with Session(get_engine(get_settings().database_url)) as session:
        job = claim_next_job(session, "progress-dedup-worker", 15)
        assert job is not None and job.id == job_id
        assert update_job_progress(
            session,
            job_id=job_id,
            worker_id="progress-dedup-worker",
            progress=65,
            stage="模型仍在生成 · 已等待 20 秒",
            lease_seconds=15,
        )
        assert update_job_progress(
            session,
            job_id=job_id,
            worker_id="progress-dedup-worker",
            progress=65,
            stage="模型仍在生成 · 已等待 20 秒",
            lease_seconds=15,
        )
        events = list_events(session, project_id)

    matching = [
        event
        for event in events
        if event.event_type == "job.progress"
        and event.payload.get("stage") == "模型仍在生成 · 已等待 20 秒"
    ]
    assert len(matching) == 1


async def test_running_job_diagnostics_are_persisted_before_terminal_failure(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "diagnostics-create-v1")
    project_id = str(project["id"])
    queued = await client.post(
        f"/api/v1/projects/{project_id}/story-directions",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "diagnostics-directions-v1"},
    )
    job_id = queued.json()["data"]["id"]
    details = {
        "phase": "story_structure_validation",
        "model_attempt": 1,
        "max_model_attempts": 3,
        "attempts": [{"attempt": 1, "validation_error": "sequence 必须连续"}],
    }

    with Session(get_engine(get_settings().database_url)) as session:
        job = claim_next_job(session, "diagnostics-worker", 15)
        assert job is not None and job.id == job_id
        assert update_job_diagnostics(
            session,
            job_id=job_id,
            worker_id="diagnostics-worker",
            details=details,
            lease_seconds=15,
        )
        session.refresh(job)
        assert job.error_details_json is not None
        events = list_events(session, project_id)

    assert any(event.event_type == "job.diagnostics" for event in events)


async def test_cancel_retry_and_worker_completion(client: AsyncClient) -> None:
    now = datetime.now(UTC)
    with Session(get_engine(get_settings().database_url)) as session:
        job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:cancel-retry-demo",
            input_payload={"steps": 2},
            label="取消与重试测试",
            stage="等待渲染",
            trace_id="cancel-retry-trace",
            retryable=True,
        )
        session.commit()
        job_id = job.id

    cancelled = await client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"Idempotency-Key": "cancel-job-v1"},
    )
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    retry = await client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers={"Idempotency-Key": "retry-job-v1"},
    )
    assert retry.json()["data"]["status"] == "RETRY_WAIT"
    assert (
        datetime.fromisoformat(retry.json()["data"]["available_at"].replace("Z", "+00:00")) >= now
    )

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    completed = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert completed["status"] == "SUCCEEDED"
    assert completed["attempt"] == 1


async def test_failed_job_exposes_and_executes_recovery_actions(client: AsyncClient) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:graceful-job-recovery",
            input_payload={"steps": 4},
            label="优雅恢复测试",
            stage="等待渲染",
            trace_id="graceful-job-recovery-trace",
            retryable=True,
        )
        session.commit()
        claimed = claim_next_job(session, "graceful-recovery-worker", 15)
        assert claimed is not None and claimed.id == job.id
        claimed.progress = 64
        claimed.stage = "生成主镜头"
        claimed.attempt = claimed.max_attempts
        claimed.output_json = json.dumps(
            {"completed_shot_ids": ["shot-01", "shot-02"]},
            ensure_ascii=False,
        )
        session.commit()
        finish_job_failure(
            session,
            job_id=job.id,
            worker_id="graceful-recovery-worker",
            code="MODEL_TEMPORARY_FAILURE",
            message="主镜头生成暂时失败",
            details={
                "completed_steps": ["解析脚本", "生成关键帧"],
                "failed_step": "生成主镜头",
                "failed_parts": ["shot-03"],
                "unreliable_outputs": ["shot-03 的视频和后续时间线"],
            },
            retryable=True,
        )
        job_id = job.id

    failed = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    recovery = failed["error_details"]["recovery"]
    assert recovery["completion_state"] == "PARTIAL"
    assert recovery["completed_percent"] == 64
    assert recovery["intermediate_result_saved"] is True
    assert recovery["failed_parts"] == ["shot-03"]
    assert "RETRY_FAILED_PARTS" in recovery["available_actions"]
    assert recovery["unreliable_outputs"] == ["shot-03 的视频和后续时间线"]

    saved = await client.post(
        f"/api/v1/jobs/{job_id}/recovery",
        json={"action": "SAVE_INTERMEDIATE"},
        headers={"Idempotency-Key": "save-intermediate-v1"},
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["status"] == "FAILED"
    assert saved.json()["data"]["error_details"]["recovery"]["intermediate_result_saved"] is True

    handoff = await client.post(
        f"/api/v1/jobs/{job_id}/recovery",
        json={"action": "ESCALATE_HUMAN", "note": "请检查主镜头连续性"},
        headers={"Idempotency-Key": "handoff-job-v1"},
    )
    assert handoff.status_code == 200
    assert handoff.json()["data"]["stage"] == "等待人工处理"
    assert handoff.json()["data"]["error_details"]["recovery"]["handoff_status"] == "REQUESTED"

    resumed = await client.post(
        f"/api/v1/jobs/{job_id}/recovery",
        json={"action": "RETRY_FAILED_PARTS"},
        headers={"Idempotency-Key": "retry-failed-parts-v1"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["data"]["status"] == "RETRY_WAIT"
    assert resumed.json()["data"]["progress"] == 64
    with Session(get_engine(get_settings().database_url)) as session:
        persisted = session.get(Job, job_id)
        assert persisted is not None
        directive = json.loads(persisted.input_json)["_recovery"]
        assert directive["action"] == "RETRY_FAILED_PARTS"
        assert directive["failed_part_ids"] == ["shot-03"]
        assert persisted.output_json is not None

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    recovered = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["stage"] == "恢复后已完成"
    assert recovered["error_details"]["recovery"]["completion_state"] == "RECOVERED"

    with Session(get_engine(get_settings().database_url)) as session:
        fallback_job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:graceful-fallback-recovery",
            input_payload={"steps": 1},
            label="降级执行测试",
            stage="等待渲染",
            trace_id="graceful-fallback-recovery-trace",
            retryable=True,
        )
        session.commit()
        fallback_job_id = fallback_job.id
    await client.post(
        f"/api/v1/jobs/{fallback_job_id}/cancel",
        headers={"Idempotency-Key": "cancel-fallback-job-v1"},
    )
    fallback = await client.post(
        f"/api/v1/jobs/{fallback_job_id}/recovery",
        json={"action": "FALLBACK_EXECUTION", "strategy": "stability-first"},
        headers={"Idempotency-Key": "fallback-job-v1"},
    )
    assert fallback.status_code == 200
    assert await worker.run_once() is True
    degraded = (await client.get(f"/api/v1/jobs/{fallback_job_id}")).json()["data"]
    assert degraded["stage"] == "已降级完成"
    assert degraded["error_details"]["recovery"]["completion_state"] == "DEGRADED_SUCCEEDED"
    assert degraded["error_details"]["recovery"]["unreliable_outputs"]


async def test_retry_backoff_and_expired_lease_recovery(client: AsyncClient) -> None:
    with Session(get_engine(get_settings().database_url)) as session:
        failing, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="GENERATE_PROPOSAL",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:retry-backoff",
            input_payload={"fail_until_attempt": 1},
            label="退避测试",
            stage="等待测试",
            trace_id="retry-backoff-trace",
            retryable=True,
        )
        session.commit()
        failing_id = failing.id

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    with Session(get_engine(get_settings().database_url)) as session:
        failed_once = session.get(Job, failing_id)
        assert failed_once is not None
        assert failed_once.status == "RETRY_WAIT"
        assert failed_once.attempt == 1
        available_at = failed_once.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        delay = (available_at - datetime.now(UTC)).total_seconds()
        assert 4 <= delay <= 5.5

        failed_once.status = "RUNNING"
        failed_once.attempt = 2
        failed_once.worker_id = "dead-worker"
        failed_once.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        assert recover_expired_jobs(session) == 1
        session.refresh(failed_once)
        assert failed_once.status == "RETRY_WAIT"
        events = list_events(session, PROJECT_ID)
        assert "job.recovered" in {event.event_type for event in events}


async def test_automatic_retry_claim_clears_previous_error(client: AsyncClient) -> None:
    del client
    with Session(get_engine(get_settings().database_url)) as session:
        job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="GENERATE_PROPOSAL",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:automatic-retry-clears-error",
            input_payload={},
            label="自动重试错误清理测试",
            stage="等待测试",
            trace_id="automatic-retry-clears-error-trace",
            max_attempts=2,
        )
        session.commit()
        first = claim_next_job(session, "retry-clear-worker-1", 15)
        assert first is not None and first.id == job.id
        finish_job_failure(
            session,
            job_id=job.id,
            worker_id="retry-clear-worker-1",
            code="ARK_TEXT_NETWORK_ERROR",
            message="上一轮错误",
            details={"exception_type": "ReadTimeout"},
            retryable=True,
        )
        queued = session.get(Job, job.id)
        assert queued is not None
        queued.available_at = datetime.now(UTC)
        session.commit()

        second = claim_next_job(session, "retry-clear-worker-2", 15)
        assert second is not None and second.id == job.id
        assert second.status == "RUNNING"
        assert second.attempt == 2
        assert second.error_code is None
        assert second.error_message is None
        assert second.error_details_json is None


async def test_story_package_failure_releases_project_and_retry_restores_running_state(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "story-package-failure-release-v1")
    project_id = str(project["id"])
    worker_id = "story-package-failure-worker"
    with Session(get_engine(get_settings().database_url)) as session:
        record = session.get(Project, project_id)
        assert record is not None
        record.status = "STORY_PACKAGE_RUNNING"
        job, _ = enqueue_job(
            session,
            project_id=project_id,
            job_type="GENERATE_STORY_PACKAGE",
            entity_type="proposal_version",
            entity_id="missing-proposal",
            idempotency_key="test:story-package-failure-release",
            input_payload={"project_id": project_id},
            label="故事资料失败回退测试",
            stage="生成中",
            trace_id="story-package-failure-release-trace",
            retryable=True,
        )
        job.status = "RUNNING"
        job.worker_id = worker_id
        session.commit()
        job_id = job.id

        finish_job_failure(
            session,
            job_id=job_id,
            worker_id=worker_id,
            code="ARK_TEXT_SCHEMA_INVALID",
            message="结构化 JSON 不符合创作合同",
            details=None,
            retryable=False,
        )
        session.refresh(record)
        assert record.status == "PROPOSAL_READY"

    retried = await client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers={"Idempotency-Key": "story-package-manual-retry-v1"},
    )
    assert retried.status_code == 200
    assert retried.json()["data"]["status"] == "RETRY_WAIT"
    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert current["status"] == "STORY_PACKAGE_RUNNING"


@pytest.mark.parametrize(
    ("job_type", "running_status", "fallback_status"),
    [
        ("GENERATE_STORY_STRUCTURE", "STORY_STRUCTURE_RUNNING", "PROPOSAL_READY"),
        ("GENERATE_SCRIPT_PACKAGE", "SCRIPT_PACKAGE_RUNNING", "CHARACTER_VISUAL_READY"),
    ],
)
async def test_two_stage_story_failure_and_retry_restore_correct_project_state(
    client: AsyncClient,
    job_type: str,
    running_status: str,
    fallback_status: str,
) -> None:
    project = await _create_draft(client, f"two-stage-failure-{job_type.lower()}")
    project_id = str(project["id"])
    worker_id = f"worker-{job_type.lower()}"
    with Session(get_engine(get_settings().database_url)) as session:
        record = session.get(Project, project_id)
        assert record is not None
        record.status = running_status
        job, _ = enqueue_job(
            session,
            project_id=project_id,
            job_type=job_type,
            entity_type="relationship_graph" if "SCRIPT" in job_type else "proposal_version",
            entity_id="missing-source",
            idempotency_key=f"test:{job_type.lower()}:failure-release",
            input_payload={"project_id": project_id},
            label="两阶段故事任务失败回退测试",
            stage="生成中",
            trace_id=f"{job_type.lower()}-failure-trace",
            retryable=True,
        )
        job.status = "RUNNING"
        job.worker_id = worker_id
        session.commit()
        job_id = job.id

        finish_job_failure(
            session,
            job_id=job_id,
            worker_id=worker_id,
            code="STORY_STAGE_FAILED",
            message="阶段生成失败",
            details=None,
            retryable=False,
        )
        session.refresh(record)
        assert record.status == fallback_status

    retried = await client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers={"Idempotency-Key": f"retry-{job_type.lower()}"},
    )
    assert retried.status_code == 200
    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert current["status"] == running_status


async def test_startup_reconciles_historical_terminal_story_package_job(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "story-package-startup-reconcile-v1")
    project_id = str(project["id"])
    with Session(get_engine(get_settings().database_url)) as session:
        record = session.get(Project, project_id)
        assert record is not None
        record.status = "STORY_PACKAGE_RUNNING"
        job, _ = enqueue_job(
            session,
            project_id=project_id,
            job_type="GENERATE_STORY_PACKAGE",
            entity_type="proposal_version",
            entity_id="historical-proposal",
            idempotency_key="test:story-package-startup-reconcile",
            input_payload={"project_id": project_id},
            label="历史故事资料任务纠正测试",
            stage="任务失败",
            trace_id="story-package-startup-reconcile-trace",
        )
        job.status = "FAILED"
        job.completed_at = datetime.now(UTC)
        session.commit()

        assert reconcile_terminal_project_jobs(session) == 1
        session.refresh(record)
        assert record.status == "PROPOSAL_READY"
        assert reconcile_terminal_project_jobs(session) == 0


async def test_atomic_claim_has_only_one_winner(client: AsyncClient) -> None:
    del client
    engine = get_engine(get_settings().database_url)
    with Session(engine) as session:
        enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="project",
            entity_id=PROJECT_ID,
            idempotency_key="test:atomic-claim",
            input_payload={"steps": 1},
            label="原子领取测试",
            stage="等待领取",
            trace_id="atomic-claim-trace",
        )
        session.commit()

    def claim(worker_id: str) -> str | None:
        with Session(engine) as session:
            result = claim_next_job(session, worker_id, 15)
            return result.id if result else None

    first, second = await asyncio.gather(
        asyncio.to_thread(claim, "worker-a"),
        asyncio.to_thread(claim, "worker-b"),
    )
    assert sum(value is not None for value in (first, second)) == 1


async def test_approved_story_builds_character_assets_and_real_preview(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "production-flow-create-v1")
    project_id = str(project["id"])
    proposal_response = await client.post(
        f"/api/v1/projects/{project_id}/director-proposals",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "production-proposal-v1"},
    )
    proposal_job_id = proposal_response.json()["data"]["id"]
    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    assert (await client.get(f"/api/v1/jobs/{proposal_job_id}")).json()["data"][
        "status"
    ] == "SUCCEEDED"

    approved = await client.post(
        f"/api/v1/projects/{project_id}/director-proposals/1/approve",
        json={
            "expected_version": 3,
            "assumptions_confirmed": True,
            "actor": "integration-test",
        },
        headers={"Idempotency-Key": "approve-story-v1"},
    )
    assert approved.status_code == 202
    assert approved.json()["data"]["story"]["status"] == "APPROVED"
    assert approved.json()["data"]["job"]["job_type"] == "GENERATE_CHARACTER_CANDIDATES"
    assert await worker.run_once() is True

    characters = (await client.get(f"/api/v1/projects/{project_id}/characters/candidates")).json()[
        "data"
    ]
    assert len(characters) == 1
    assert len(characters[0]["candidates"]) == 2
    first_candidate = characters[0]["candidates"][0]
    image = await client.get(first_candidate["asset_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    image_range = await client.get(first_candidate["asset_url"], headers={"Range": "bytes=0-15"})
    assert image_range.status_code == 206
    assert len(image_range.content) == 16
    assert image_range.content.startswith(b"\x89PNG")

    locked = await client.post(
        f"/api/v1/projects/{project_id}/characters/{characters[0]['id']}/lock",
        json={"expected_version": 4, "candidate_id": first_candidate["id"]},
        headers={"Idempotency-Key": "lock-character-v1"},
    )
    assert locked.status_code == 202
    assert locked.json()["data"]["character"]["status"] == "LOCKED"
    assert await worker.run_once() is True  # Storyboards and current Takes.
    assert await worker.run_once() is True  # Injected Hero failure and reversible fallback.
    assert await worker.run_once() is True  # FFmpeg + ffprobe Preview.

    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert current["status"] == "PREVIEW_READY"
    workspace = (await client.get(f"/api/v1/projects/{project_id}/workspace")).json()["data"]
    assert len(workspace["scenes"]) == 3
    assert len(workspace["shots"]) == 8
    assert sum(shot["duration_sec"] for shot in workspace["shots"]) == 60
    assert all(shot["status"] == "APPROVED" for shot in workspace["shots"])
    hero_job = next(job for job in workspace["jobs"] if job["job_type"] == "GENERATE_HERO_FIXTURE")
    assert hero_job["status"] == "SUCCEEDED"
    previews = (await client.get(f"/api/v1/projects/{project_id}/previews")).json()["data"]
    assert len(previews) == 1
    assert previews[0]["status"] == "READY"
    assert abs(previews[0]["duration_ms"] - 60_000) < 500
    video_range = await client.get(previews[0]["assets"]["mp4"], headers={"Range": "bytes=0-1023"})
    assert video_range.status_code == 206
    assert len(video_range.content) == 1024
    invalid_range = await client.get(
        previews[0]["assets"]["mp4"], headers={"Range": "bytes=invalid"}
    )
    assert invalid_range.status_code == 416
    vtt = await client.get(previews[0]["assets"]["vtt"])
    assert vtt.text.startswith("WEBVTT")
    assert "演示字幕" in vtt.text
    manifest = (await client.get(previews[0]["assets"]["manifest"])).json()
    assert manifest["hero_shot"] == {
        "requested": 1,
        "rendered": 0,
        "shot_code": "S05",
        "failure_plan": "HERO_VIDEO:S05:attempt1",
        "fallback": "KEN_BURNS",
        "timeline_gap": False,
    }


async def test_revision_compare_approve_export_and_rollback_closed_loop(
    client: AsyncClient,
) -> None:
    project = await _create_draft(client, "revision-export-create-v1")
    project_id = str(project["id"])
    worker = PersistentJobWorker(get_settings())
    await client.post(
        f"/api/v1/projects/{project_id}/director-proposals",
        json={"expected_version": 1},
        headers={"Idempotency-Key": "revision-proposal-v1"},
    )
    assert await worker.run_once() is True
    await client.post(
        f"/api/v1/projects/{project_id}/director-proposals/1/approve",
        json={"expected_version": 3, "assumptions_confirmed": True, "actor": "test"},
        headers={"Idempotency-Key": "revision-approve-story-v1"},
    )
    assert await worker.run_once() is True
    characters = (await client.get(f"/api/v1/projects/{project_id}/characters/candidates")).json()[
        "data"
    ]
    await client.post(
        f"/api/v1/projects/{project_id}/characters/{characters[0]['id']}/lock",
        json={"expected_version": 4, "candidate_id": characters[0]["candidates"][0]["id"]},
        headers={"Idempotency-Key": "revision-lock-character-v1"},
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is True

    workspace = (await client.get(f"/api/v1/projects/{project_id}/workspace")).json()["data"]
    shot_id = workspace["shots"][2]["id"]
    timelines = (await client.get(f"/api/v1/projects/{project_id}/previews")).json()["data"]
    baseline = timelines[0]
    with Session(get_engine(get_settings().database_url)) as session:
        shot_ids = [shot["id"] for shot in workspace["shots"]]
        before = {
            take.id: session.get(Asset, take.asset_id).sha256
            for take in session.scalars(select(Take).where(Take.shot_id.in_(shot_ids)))
        }

    impact_payload = {
        "expected_version": 7,
        "scope": {"type": "SHOT", "ids": [shot_id]},
        "instruction": "妹妹只说半句，把威胁放在动作里",
    }
    impact = await client.post(
        f"/api/v1/projects/{project_id}/revision-impact", json=impact_payload
    )
    assert impact.status_code == 200
    assert impact.json()["data"]["intent"]["type"] == "DIALOGUE"
    assert impact.json()["data"]["requires_confirmation"] is True

    revision = await client.post(
        f"/api/v1/projects/{project_id}/revisions",
        json={**impact_payload, "confirmed": True},
        headers={"Idempotency-Key": "revision-change-set-v1"},
    )
    assert revision.status_code == 202
    assert revision.json()["data"]["revision"]["status"] == "PENDING"
    revision_job_id = revision.json()["data"]["job"]["id"]
    cancelled_revision = await client.post(
        f"/api/v1/jobs/{revision_job_id}/cancel",
        headers={"Idempotency-Key": "revision-cancel-v1"},
    )
    assert cancelled_revision.json()["data"]["status"] == "CANCELLED"
    cancelled_change = await client.get(
        f"/api/v1/revisions/{revision.json()['data']['revision']['id']}"
    )
    assert cancelled_change.json()["data"]["status"] == "CANCELLED"
    after_cancel = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert after_cancel["status"] == "PREVIEW_READY"
    retried_revision = await client.post(
        f"/api/v1/jobs/{revision_job_id}/retry",
        headers={"Idempotency-Key": "revision-retry-v1"},
    )
    assert retried_revision.json()["data"]["status"] == "RETRY_WAIT"
    assert await worker.run_once() is True
    revision_read = await client.get(
        f"/api/v1/revisions/{revision.json()['data']['revision']['id']}"
    )
    result_timeline_id = revision_read.json()["data"]["result_timeline_id"]
    assert revision_read.json()["data"]["status"] == "SUCCEEDED"
    assert result_timeline_id

    comparison = await client.get(f"/api/v1/previews/{baseline['id']}/compare/{result_timeline_id}")
    assert comparison.status_code == 200
    assert set(comparison.json()["data"]["changed_assets"]) >= {"srt", "vtt"}
    assert comparison.json()["data"]["changed_shot_ids"] == []
    with Session(get_engine(get_settings().database_url)) as session:
        after = {
            take.id: session.get(Asset, take.asset_id).sha256
            for take in session.scalars(select(Take).where(Take.id.in_(before)))
        }
    assert after == before

    project_before_approval = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    approved = await client.post(
        f"/api/v1/previews/{result_timeline_id}/approve",
        json={"expected_version": project_before_approval["lock_version"], "actor": "test"},
        headers={"Idempotency-Key": "revision-approve-preview-v2"},
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"
    estimate = await client.post(
        f"/api/v1/projects/{project_id}/exports/estimate",
        json={"profile": "hybrid_720p"},
    )
    assert estimate.json()["data"]["outputs"] == ["MP4", "SRT", "VTT", "JSON_MANIFEST"]
    project_before_export = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    blocked = await client.post(
        f"/api/v1/projects/{project_id}/exports",
        json={
            "expected_version": project_before_export["lock_version"],
            "profile": "hybrid_720p",
            "rights_confirmed": False,
            "actor": "test",
        },
        headers={"Idempotency-Key": "revision-export-blocked-v1"},
    )
    assert blocked.status_code == 423
    created_export = await client.post(
        f"/api/v1/projects/{project_id}/exports",
        json={
            "expected_version": project_before_export["lock_version"],
            "profile": "hybrid_720p",
            "rights_confirmed": True,
            "actor": "test",
        },
        headers={"Idempotency-Key": "revision-export-v1"},
    )
    assert created_export.status_code == 202
    assert await worker.run_once() is True
    export_id = created_export.json()["data"]["export"]["id"]
    ready_export = (await client.get(f"/api/v1/exports/{export_id}")).json()["data"]
    assert ready_export["status"] == "READY"
    assert set(ready_export["assets"]) == {"mp4", "srt", "vtt", "manifest"}
    for url in ready_export["assets"].values():
        assert (await client.get(url)).status_code == 200
    manifest = (await client.get(ready_export["assets"]["manifest"])).json()
    assert manifest["timeline"]["id"] == result_timeline_id
    assert manifest["rights"]["status"] == "RESTRICTED_DEMO"

    project_before_rollback = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    rollback = await client.post(
        f"/api/v1/previews/{baseline['id']}/rollback",
        json={"expected_version": project_before_rollback["lock_version"], "actor": "test"},
        headers={"Idempotency-Key": "revision-rollback-v1"},
    )
    assert rollback.status_code == 200
    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert current["timeline_version"] == 1
    assert len((await client.get(f"/api/v1/projects/{project_id}/previews")).json()["data"]) == 2
    approved_baseline = await client.post(
        f"/api/v1/previews/{baseline['id']}/approve",
        json={"expected_version": current["lock_version"], "actor": "test"},
        headers={"Idempotency-Key": "revision-approve-baseline-v1"},
    )
    assert approved_baseline.status_code == 200
    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]

    retryable_export = await client.post(
        f"/api/v1/projects/{project_id}/exports",
        json={
            "expected_version": current["lock_version"],
            "profile": "hybrid_720p",
            "rights_confirmed": True,
            "actor": "test",
        },
        headers={"Idempotency-Key": "revision-export-retry-v1"},
    )
    assert retryable_export.status_code == 202
    retryable_job_id = retryable_export.json()["data"]["job"]["id"]
    reserved = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert reserved["available_points"] == 49_980
    cancelled = await client.post(
        f"/api/v1/jobs/{retryable_job_id}/cancel",
        headers={"Idempotency-Key": "revision-export-cancel-v1"},
    )
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    released = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert released["available_points"] == 49_990
    assert released["status"] == "APPROVED"

    retried = await client.post(
        f"/api/v1/jobs/{retryable_job_id}/retry",
        headers={"Idempotency-Key": "revision-export-retry-job-v1"},
    )
    assert retried.json()["data"]["status"] == "RETRY_WAIT"
    re_reserved = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert re_reserved["available_points"] == 49_980
    assert await worker.run_once() is True
    completed_retry = (await client.get(f"/api/v1/jobs/{retryable_job_id}")).json()["data"]
    assert completed_retry["status"] == "SUCCEEDED"
