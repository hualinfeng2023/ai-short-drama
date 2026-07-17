import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Asset, BriefVersion, Job, Project, ProposalVersion
from app.domain.narrative_targeting import incomplete_targeting_fields
from app.schemas import JobRead, ProposalRead
from app.services.events import append_event
from app.services.jobs import enqueue_job, job_to_read
from app.services.projects import canonical_json, project_locked, version_conflict
from app.services.workspace import project_or_404

PROPOSAL_CONFIG_VERSION = "proposal-v1"


def proposal_to_read(proposal: ProposalVersion) -> ProposalRead:
    return ProposalRead(
        id=proposal.id,
        project_id=proposal.project_id,
        version=proposal.version,
        brief_version=proposal.brief_version,
        batch_id=proposal.batch_id,
        direction_key=proposal.direction_key,
        source_proposal_ids=json.loads(proposal.source_proposal_ids_json),
        schema_version=proposal.schema_version,
        generation_evidence=json.loads(proposal.generation_evidence_json),
        payload=json.loads(proposal.payload_json),
        provider=proposal.provider,
        model=proposal.model,
        config_version=proposal.config_version,
        status=proposal.status,
        approved_at=proposal.approved_at,
        approved_by=proposal.approved_by,
        created_at=proposal.created_at,
    )


def list_proposals(session: Session, project_id: str) -> list[ProposalRead]:
    project_or_404(session, project_id)
    proposals = session.scalars(
        select(ProposalVersion)
        .where(ProposalVersion.project_id == project_id)
        .order_by(ProposalVersion.version.desc())
    ).all()
    return [proposal_to_read(item) for item in proposals]


def create_proposal_job(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[JobRead, bool]:
    project = project_or_404(session, project_id)
    latest_brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    if latest_brief is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BRIEF_REQUIRED",
                "message": "生成导演方案前需要先保存项目简报",
                "user_action": "返回项目简报并保存",
                "retryable": False,
                "details": {"project_id": project_id},
            },
        )

    business_key = (
        f"{project_id}:GENERATE_PROPOSAL:{project_id}:"
        f"brief-{latest_brief.version}:{PROPOSAL_CONFIG_VERSION}"
    )
    existing = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if existing is not None:
        return job_to_read(existing), True

    if project.status not in {"DRAFT", "PROPOSAL_READY"}:
        raise project_locked(project)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)

    reference_ids = json.loads(latest_brief.reference_asset_ids_json)
    reference_assets = session.scalars(
        select(Asset).where(Asset.project_id == project_id, Asset.id.in_(reference_ids))
    ).all()
    reference_materials = []
    for asset in reference_assets:
        metadata = json.loads(asset.metadata_json or "{}")
        reference_materials.append(
            {
                "asset_id": asset.id,
                "kind": asset.kind,
                "sha256": asset.sha256,
                "parse_status": metadata.get("parse_status"),
                "parsed_text": str(metadata.get("parsed_text") or "")[:50_000],
            }
        )
    blocking_questions = json.loads(latest_brief.blocking_questions_json)
    if blocking_questions:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BRIEF_QUESTIONS_REQUIRED",
                "message": "生成导演方案前需要先解决项目简报中的阻断问题",
                "user_action": "返回项目简报回答阻断问题并保存新版本",
                "retryable": False,
                "details": {"questions": blocking_questions},
            },
        )
    missing_targeting = incomplete_targeting_fields(
        {
            "narrative_protagonist": latest_brief.narrative_protagonist,
            "emotional_rewards": json.loads(latest_brief.emotional_rewards_json),
        }
    )
    if missing_targeting:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NARRATIVE_TARGETING_REQUIRED",
                "message": "生成导演方案前需要明确叙事主角和至少一种情绪回报",
                "user_action": "返回项目简报完成独立叙事定位后保存新版本",
                "retryable": False,
                "details": {"missing_fields": missing_targeting},
            },
        )
    input_payload: dict[str, object] = {
        "project_id": project_id,
        "brief_version": latest_brief.version,
        "brief_content_hash": latest_brief.content_hash,
        "project_name": latest_brief.project_name,
        "raw_input": latest_brief.raw_input,
        "genre": latest_brief.genre,
        "style": latest_brief.style,
        "target_duration_sec": latest_brief.target_duration_sec,
        "aspect_ratio": latest_brief.aspect_ratio,
        "target_platform": latest_brief.target_platform,
        "narrative_protagonist": latest_brief.narrative_protagonist,
        "target_audience": latest_brief.target_audience,
        "emotional_rewards": json.loads(latest_brief.emotional_rewards_json),
        "audience_profile": latest_brief.audience_profile,
        "production_format": latest_brief.production_format,
        "primary_market": latest_brief.primary_market,
        "secondary_markets": json.loads(latest_brief.secondary_markets_json),
        "canonical_language": latest_brief.canonical_language,
        "localization_targets": json.loads(latest_brief.localization_targets_json),
        "platform_targets": json.loads(latest_brief.platform_targets_json),
        "content_requirements": json.loads(latest_brief.content_requirements_json),
        "content_avoidances": json.loads(latest_brief.content_avoidances_json),
        "creative_defaults": json.loads(latest_brief.creative_defaults_json),
        "payload_schema_version": latest_brief.payload_schema_version,
        "reference_materials": reference_materials,
        "config_version": PROPOSAL_CONFIG_VERSION,
        "request_idempotency_key": request_idempotency_key,
    }
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="GENERATE_PROPOSAL",
        entity_type="project",
        entity_id=project_id,
        idempotency_key=business_key,
        input_payload=input_payload,
        label=f"{project.name} · 导演方案",
        stage="等待生成导演方案",
        trace_id=trace_id,
        estimated_seconds=3,
        retryable=True,
    )
    now = datetime.now(UTC)
    result = session.execute(
        update(Project)
        .where(
            Project.id == project_id,
            Project.lock_version == expected_version,
            Project.status.in_({"DRAFT", "PROPOSAL_READY"}),
        )
        .values(
            status="PROPOSAL_RUNNING",
            lock_version=expected_version + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise version_conflict(project_or_404(session, project_id), expected_version)
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="project.proposal_started",
        payload={
            "project_id": project_id,
            "job_id": job.id,
            "brief_version": latest_brief.version,
        },
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job), replayed


def _mock_proposal_payload(job_input: dict[str, object]) -> dict[str, object]:
    project_name = str(job_input["project_name"])
    raw_input = str(job_input["raw_input"])
    style = str(job_input["style"])
    target_duration = int(job_input["target_duration_sec"])
    base_durations = [9, 9, 8, 7, 7, 7, 7, 6]
    scaled = [duration * target_duration / sum(base_durations) for duration in base_durations]
    shot_durations = [max(1, int(value)) for value in scaled]
    remainder = target_duration - sum(shot_durations)
    order = sorted(
        range(len(scaled)),
        key=lambda index: (scaled[index] - int(scaled[index]), -index),
        reverse=True,
    )
    for index in order[:remainder]:
        shot_durations[index] += 1
    first, second, third = shot_durations[:2], shot_durations[2:5], shot_durations[5:]
    references = job_input.get("reference_materials")
    reference_count = len(references) if isinstance(references, list) else 0
    emotional_rewards = job_input.get("emotional_rewards")
    if not isinstance(emotional_rewards, list):
        emotional_rewards = []
    return {
        "narrative_targeting": {
            "narrative_protagonist": job_input.get("narrative_protagonist", "unspecified"),
            "target_audience": job_input.get("target_audience", "general"),
            "emotional_rewards": emotional_rewards,
            "audience_profile": job_input.get("audience_profile", ""),
            "production_format": job_input.get("production_format", "live_action"),
        },
        "title": project_name,
        "logline": raw_input,
        "director_statement": f"以{style}为基调，用选择和行动完成主人公的情绪转折。",
        "total_duration_sec": target_duration,
        "scenes": [
            {
                "code": "01",
                "title": "失衡",
                "purpose": "在首屏建立人物困境与继续观看的理由",
                "duration_sec": sum(first),
                "shots": [
                    {
                        "code": "S01",
                        "duration_sec": first[0],
                        "shot_size": "WS",
                        "camera": "TRACK",
                    },
                    {
                        "code": "S02",
                        "duration_sec": first[1],
                        "shot_size": "CU",
                        "camera": "STATIC",
                    },
                ],
            },
            {
                "code": "02",
                "title": "选择",
                "purpose": "让转折来自可见的具体行动",
                "duration_sec": sum(second),
                "shots": [
                    {
                        "code": "S03",
                        "duration_sec": second[0],
                        "shot_size": "MCU",
                        "camera": "DOLLY_IN",
                    },
                    {
                        "code": "S04",
                        "duration_sec": second[1],
                        "shot_size": "MS",
                        "camera": "STATIC",
                    },
                    {
                        "code": "S05",
                        "duration_sec": second[2],
                        "shot_size": "WS",
                        "camera": "DOLLY_IN",
                    },
                ],
            },
            {
                "code": "03",
                "title": "新秩序",
                "purpose": "以结果和余韵收束人物成长",
                "duration_sec": sum(third),
                "shots": [
                    {
                        "code": "S06",
                        "duration_sec": third[0],
                        "shot_size": "MS",
                        "camera": "PAN",
                    },
                    {
                        "code": "S07",
                        "duration_sec": third[1],
                        "shot_size": "CU",
                        "camera": "STATIC",
                    },
                    {
                        "code": "S08",
                        "duration_sec": third[2],
                        "shot_size": "WS",
                        "camera": "TRACK",
                    },
                ],
            },
        ],
        "assumptions": [
            "Mock Provider 使用固定三幕八镜结构",
            f"总时长约束为 {target_duration} 秒",
            f"叙事主角：{job_input.get('narrative_protagonist', 'unspecified')}",
            f"目标受众：{job_input.get('target_audience', 'general')}",
            f"情绪回报：{'、'.join(str(item) for item in emotional_rewards)}",
            (
                f"主市场与规范语言：{job_input.get('primary_market', 'CN')} / "
                f"{job_input.get('canonical_language', 'zh-CN')}"
            ),
            f"已读取 {reference_count} 份参考素材" if reference_count else "未提供参考素材",
        ],
    }


def materialize_mock_proposal(session: Session, job: Job) -> ProposalVersion:
    job_input = json.loads(job.input_json)
    brief_version = int(job_input["brief_version"])
    existing = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == job.project_id,
            ProposalVersion.brief_version == brief_version,
            ProposalVersion.config_version == PROPOSAL_CONFIG_VERSION,
        )
    )
    if existing is not None:
        return existing

    next_version = (
        session.scalar(
            select(func.max(ProposalVersion.version)).where(
                ProposalVersion.project_id == job.project_id
            )
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    proposal = ProposalVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        version=next_version,
        brief_version=brief_version,
        payload_json=canonical_json(_mock_proposal_payload(job_input)),
        provider="mock",
        model="deterministic-v1",
        config_version=PROPOSAL_CONFIG_VERSION,
        status="READY",
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(proposal)
    project = project_or_404(session, job.project_id)
    project.status = "PROPOSAL_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="proposal.ready",
        payload={
            "proposal_id": proposal.id,
            "version": proposal.version,
            "brief_version": brief_version,
        },
    )
    session.flush()
    return proposal
