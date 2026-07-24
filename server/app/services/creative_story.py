import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    BriefVersion,
    EpisodeOutlineVersion,
    Job,
    ProposalBatch,
    ProposalVersion,
    RelationshipGraphVersion,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    StoryBibleVersion,
    StoryVersion,
)
from app.domain.narrative_targeting import incomplete_targeting_fields
from app.schemas import JobRead, ProposalRead
from app.services.events import append_event
from app.services.jobs import enqueue_job, job_to_read, request_retry
from app.services.projects import canonical_json, content_hash, project_locked, version_conflict
from app.services.proposals import proposal_to_read
from app.services.relationship_graph_workflow import (
    graph_to_read,
    relationship_graph_script_context,
    replace_graph_payload,
)
from app.services.relationship_graphs import validate_relationship_graph
from app.services.text_provider import ScriptPackageOutput, StoryStructure, TextGenerationResult
from app.services.workspace import project_or_404

DIRECTION_CONFIG_VERSION = "story-directions-v5-resumable-routes"
STORY_PACKAGE_CONFIG_VERSION = "story-package-v5-independent-targeting"
SCRIPT_SCHEMA_VERSION = "script-v3-breakout-engine"
SCRIPT_CONFIG_VERSION = "script-v4-independent-targeting"
STORY_STRUCTURE_CONFIG_VERSION = "story-structure-v2-independent-targeting"
SCRIPT_PACKAGE_CONFIG_VERSION = "story-package-v5-independent-targeting"
RELATIONSHIP_SCRIPT_SCHEMA_VERSION = "script-v4-relationship-driven"


def _latest_brief(session: Session, project_id: str) -> BriefVersion:
    brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    if brief is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BRIEF_REQUIRED",
                "message": "生成故事方向前需要先保存项目简报",
                "retryable": False,
            },
        )
    questions = json.loads(brief.blocking_questions_json)
    if questions:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "BRIEF_QUESTIONS_REQUIRED",
                "message": "生成故事方向前需要先解决项目简报中的阻断问题",
                "user_action": "返回项目简报回答阻断问题并保存新版本",
                "retryable": False,
                "details": {"questions": questions},
            },
        )
    missing_targeting = incomplete_targeting_fields(
        {
            "narrative_protagonist": brief.narrative_protagonist,
            "emotional_rewards": json.loads(brief.emotional_rewards_json),
        }
    )
    if missing_targeting:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NARRATIVE_TARGETING_REQUIRED",
                "message": "生成故事方向前需要明确叙事主角和至少一种情绪回报",
                "user_action": "返回项目简报完成独立叙事定位后保存新版本",
                "retryable": False,
                "details": {"missing_fields": missing_targeting},
            },
        )
    return brief


def brief_provider_payload(brief: BriefVersion) -> dict[str, object]:
    return {
        "project_name": brief.project_name,
        "raw_input": brief.raw_input,
        "genre": brief.genre,
        "style": brief.style,
        "target_duration_sec": brief.target_duration_sec,
        "aspect_ratio": brief.aspect_ratio,
        "target_platform": brief.target_platform,
        "narrative_protagonist": brief.narrative_protagonist,
        "target_audience": brief.target_audience,
        "emotional_rewards": json.loads(brief.emotional_rewards_json),
        "audience_profile": brief.audience_profile,
        "production_format": brief.production_format,
        "primary_market": brief.primary_market,
        "secondary_markets": json.loads(brief.secondary_markets_json),
        "canonical_language": brief.canonical_language,
        "localization_targets": json.loads(brief.localization_targets_json),
        "platform_targets": json.loads(brief.platform_targets_json),
        "content_requirements": json.loads(brief.content_requirements_json),
        "content_avoidances": json.loads(brief.content_avoidances_json),
        "creative_defaults": json.loads(brief.creative_defaults_json),
        "brief_version": brief.version,
        "brief_content_hash": brief.content_hash,
        "payload_schema_version": brief.payload_schema_version,
    }


def request_story_directions(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[JobRead, bool]:
    project = project_or_404(session, project_id)
    brief = _latest_brief(session, project_id)
    settings = get_settings()
    business_key = (
        f"{project_id}:GENERATE_STORY_DIRECTIONS:{brief.version}:{DIRECTION_CONFIG_VERSION}"
    )
    existing = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if existing is not None:
        return job_to_read(existing), True
    if project.status not in {"DRAFT", "PROPOSAL_READY"}:
        raise project_locked(project)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="GENERATE_STORY_DIRECTIONS",
        entity_type="brief_version",
        entity_id=brief.id,
        idempotency_key=business_key,
        input_payload={
            "project_id": project_id,
            "brief_version_id": brief.id,
            "brief": brief_provider_payload(brief),
            "config_version": DIRECTION_CONFIG_VERSION,
            "request_idempotency_key": request_idempotency_key,
        },
        label=f"{project.name} · 3 个故事方向",
        stage="等待生成差异化故事方向",
        trace_id=trace_id,
        estimated_seconds=(
            max(5, round(settings.ark_request_timeout_seconds))
            if settings.ark_api_key
            else 5
        ),
        max_attempts=2,
        retryable=True,
    )
    now = datetime.now(UTC)
    project.status = "PROPOSAL_RUNNING"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="story_directions.started",
        payload={"brief_version": brief.version, "job_id": job.id},
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job), replayed


def materialize_story_directions(
    session: Session, job: Job, result: TextGenerationResult
) -> list[ProposalVersion]:
    payload = json.loads(job.input_json)
    brief = payload["brief"]
    brief_version = int(brief["brief_version"])
    existing_batch = session.scalar(
        select(ProposalBatch).where(
            ProposalBatch.project_id == job.project_id,
            ProposalBatch.brief_version == brief_version,
            ProposalBatch.config_version == DIRECTION_CONFIG_VERSION,
        )
    )
    if existing_batch is not None:
        return list(
            session.scalars(
                select(ProposalVersion)
                .where(ProposalVersion.batch_id == existing_batch.id)
                .order_by(ProposalVersion.version)
            ).all()
        )
    now = datetime.now(UTC)
    evidence = {
        "provider_request_id": result.request_id,
        "repair_attempts": result.repair_attempts,
        "brief_content_hash": brief["brief_content_hash"],
    }
    batch = ProposalBatch(
        id=str(uuid4()),
        project_id=job.project_id,
        brief_version=brief_version,
        config_version=DIRECTION_CONFIG_VERSION,
        provider=result.provider,
        model=result.model,
        status="READY",
        request_hash=content_hash(brief),
        evidence_json=canonical_json(evidence),
        created_at=now,
    )
    session.add(batch)
    next_version = (
        session.scalar(
            select(func.max(ProposalVersion.version)).where(
                ProposalVersion.project_id == job.project_id
            )
        )
        or 0
    )
    directions: list[ProposalVersion] = []
    for offset, direction in enumerate(result.payload["directions"], start=1):
        proposal = ProposalVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            version=next_version + offset,
            brief_version=brief_version,
            batch_id=batch.id,
            direction_key=str(direction["direction_key"]),
            source_proposal_ids_json="[]",
            parent_version_id=None,
            schema_version="story-direction-v3-independent-targeting",
            generation_evidence_json=canonical_json(evidence),
            payload_json=canonical_json(direction),
            provider=result.provider,
            model=result.model,
            config_version=DIRECTION_CONFIG_VERSION,
            status="READY",
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(proposal)
        directions.append(proposal)
    project = project_or_404(session, job.project_id)
    project.status = "PROPOSAL_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="story_directions.ready",
        payload={
            "batch_id": batch.id,
            "proposal_ids": [item.id for item in directions],
            "provider": result.provider,
        },
    )
    session.flush()
    return directions


def list_story_directions(session: Session, project_id: str) -> list[ProposalRead]:
    project_or_404(session, project_id)
    directions = session.scalars(
        select(ProposalVersion)
        .where(
            ProposalVersion.project_id == project_id,
            ProposalVersion.schema_version.in_(
                {
                    "story-direction-v1",
                    "story-direction-v2",
                    "story-direction-v3-independent-targeting",
                    "story-direction-merge-v1",
                }
            ),
            ProposalVersion.direction_key != "legacy",
        )
        .order_by(ProposalVersion.version.desc())
    ).all()
    return [proposal_to_read(item) for item in directions]


def merge_story_directions(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    source_proposal_ids: list[str],
    title: str | None,
) -> ProposalRead:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    unique_ids = list(dict.fromkeys(source_proposal_ids))
    if len(unique_ids) < 2:
        raise HTTPException(
            status_code=422,
            detail={"code": "MERGE_REQUIRES_TWO_DIRECTIONS", "message": "至少选择两个不同方向"},
        )
    sources = list(
        session.scalars(
            select(ProposalVersion).where(
                ProposalVersion.project_id == project_id,
                ProposalVersion.id.in_(unique_ids),
                ProposalVersion.status == "READY",
            )
        ).all()
    )
    if {item.id for item in sources} != set(unique_ids):
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTION_NOT_FOUND", "message": "部分故事方向不存在或不可合并"},
        )
    source_key = canonical_json(sorted(unique_ids))
    existing = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == project_id,
            ProposalVersion.source_proposal_ids_json == source_key,
            ProposalVersion.schema_version == "story-direction-merge-v1",
        )
    )
    if existing is not None:
        return proposal_to_read(existing)
    sources.sort(key=lambda item: item.version)
    payloads = [json.loads(item.payload_json) for item in sources]
    merged = dict(payloads[0])
    merged["direction_key"] = "merged"
    merged["title"] = title or " × ".join(str(item["title"]) for item in payloads)
    merged["differentiator"] = "；".join(str(item["differentiator"]) for item in payloads)
    merged["assumptions"] = list(
        dict.fromkeys(item for payload in payloads for item in payload.get("assumptions", []))
    )
    if "ai_recommendation" in merged:
        merged["ai_recommendation"] = {
            **merged["ai_recommendation"],
            "recommended": False,
            "reason": "该融合方向为新建独立方向，需与原始方向重新比较后再决定。",
        }
    merged["story_dna"] = {
        **payloads[0]["story_dna"],
        "central_conflict": "；".join(
            str(item["story_dna"]["central_conflict"]) for item in payloads
        ),
        "tone_keywords": list(
            dict.fromkeys(
                keyword for item in payloads for keyword in item["story_dna"]["tone_keywords"]
            )
        )[:8],
    }
    next_version = (
        session.scalar(
            select(func.max(ProposalVersion.version)).where(
                ProposalVersion.project_id == project_id
            )
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    proposal = ProposalVersion(
        id=str(uuid4()),
        project_id=project_id,
        version=next_version,
        brief_version=sources[-1].brief_version,
        batch_id=sources[-1].batch_id,
        direction_key="merged",
        source_proposal_ids_json=source_key,
        parent_version_id=sources[-1].id,
        schema_version="story-direction-merge-v1",
        generation_evidence_json=canonical_json({"merge": unique_ids}),
        payload_json=canonical_json(merged),
        provider="system-merge",
        model="deterministic-merge-v1",
        config_version=DIRECTION_CONFIG_VERSION,
        status="READY",
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(proposal)
    project.lock_version += 1
    project.updated_at = now
    session.commit()
    return proposal_to_read(proposal)


def request_story_structure(
    session: Session,
    *,
    project_id: str,
    proposal_version: int,
    expected_version: int,
    actor: str,
    request_idempotency_key: str,
    trace_id: str,
) -> tuple[JobRead, bool]:
    project = project_or_404(session, project_id)
    proposal = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == project_id,
            ProposalVersion.version == proposal_version,
        )
    )
    if proposal is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "故事方向不存在"}
        )
    business_key = (
        f"{project_id}:GENERATE_STORY_STRUCTURE:{proposal.id}:{STORY_STRUCTURE_CONFIG_VERSION}"
    )
    existing = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if existing is not None:
        if existing.status in {"FAILED", "CANCELLED"} and existing.retryable:
            return request_retry(session, existing.id), False
        return job_to_read(existing), True
    if project.status != "PROPOSAL_READY" or proposal.status != "READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTION_NOT_READY",
                "message": "当前故事方向不能生成创作包",
                "details": {"project_status": project.status, "direction_status": proposal.status},
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    brief = _latest_brief(session, project_id)
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="GENERATE_STORY_STRUCTURE",
        entity_type="proposal_version",
        entity_id=proposal.id,
        idempotency_key=business_key,
        input_payload={
            "project_id": project_id,
            "proposal_id": proposal.id,
            "proposal_version": proposal.version,
            "direction": json.loads(proposal.payload_json),
            "brief": brief_provider_payload(brief),
            "actor": actor,
            "config_version": STORY_STRUCTURE_CONFIG_VERSION,
            "request_idempotency_key": request_idempotency_key,
        },
        label=f"{project.name} · 故事设定与角色关系网",
        stage="等待生成 Story Bible 与角色关系草案",
        trace_id=trace_id,
        estimated_seconds=150 if get_settings().ark_api_key else 5,
        retryable=True,
    )
    project.status = "STORY_STRUCTURE_RUNNING"
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="story_structure.started",
        payload={"proposal_id": proposal.id, "job_id": job.id},
    )
    session.commit()
    session.refresh(job)
    return job_to_read(job), replayed


def story_package_estimate(session: Session, project_id: str) -> dict[str, object]:
    project_or_404(session, project_id)
    return {
        "assets": ["故事设定", "角色文字设定", "角色关系网"],
        "estimated_seconds": 150 if get_settings().ark_api_key else 5,
        "estimated_points": 0,
        "direction_lock": "ON_SUCCESS",
        "version_strategy": "CREATE_NEW_VERSION",
    }


def materialize_story_structure(
    session: Session, job: Job, result: TextGenerationResult
) -> RelationshipGraphVersion:
    job_payload = json.loads(job.input_json)
    proposal = session.get(ProposalVersion, str(job_payload["proposal_id"]))
    if proposal is None:
        raise ValueError("故事方向不存在")
    existing_story = session.scalar(
        select(StoryVersion).where(
            StoryVersion.project_id == job.project_id,
            StoryVersion.proposal_version == proposal.version,
        )
    )
    if existing_story is not None:
        existing_graph = session.scalar(
            select(RelationshipGraphVersion)
            .join(
                StoryBibleVersion,
                StoryBibleVersion.id == RelationshipGraphVersion.story_bible_version_id,
            )
            .where(StoryBibleVersion.story_version_id == existing_story.id)
            .order_by(RelationshipGraphVersion.version.desc())
        )
        if existing_graph is None:
            raise ValueError("故事结构已存在但角色关系网缺失")
        return existing_graph

    structure = StoryStructure.model_validate(result.payload)
    now = datetime.now(UTC)
    direction = job_payload["direction"]
    story_version = (
        session.scalar(
            select(func.max(StoryVersion.version)).where(StoryVersion.project_id == job.project_id)
        )
        or 0
    ) + 1
    story = StoryVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        version=story_version,
        proposal_version=proposal.version,
        source_proposal_ids_json=canonical_json(
            [proposal.id, *json.loads(proposal.source_proposal_ids_json)]
        ),
        parent_version_id=None,
        schema_version="story-dna-v1",
        provider=result.provider,
        model=result.model,
        config_version=STORY_STRUCTURE_CONFIG_VERSION,
        title=str(direction["title"]),
        logline=str(direction["logline"]),
        payload_json=canonical_json(direction),
        content_hash=content_hash(direction),
        status="APPROVED",
        approved_at=now,
        approved_by=str(job_payload["actor"]),
        created_at=now,
    )
    session.add(story)
    session.flush()

    bible_payload = structure.story_bible.model_dump(mode="json")
    bible_version = (
        session.scalar(
            select(func.max(StoryBibleVersion.version)).where(
                StoryBibleVersion.project_id == job.project_id
            )
        )
        or 0
    ) + 1
    bible = StoryBibleVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        story_version_id=story.id,
        version=bible_version,
        status="DRAFT",
        payload_json=canonical_json(bible_payload),
        critic_json=canonical_json(structure.critic),
        content_hash=content_hash(bible_payload),
        parent_version_id=None,
        schema_version="story-bible-v3-independent-targeting",
        provider=result.provider,
        model=result.model,
        config_version=STORY_STRUCTURE_CONFIG_VERSION,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(bible)
    session.flush()

    graph_payload = structure.relationship_graph
    issues = validate_relationship_graph(graph_payload, bible_payload)
    graph_version = (
        session.scalar(
            select(func.max(RelationshipGraphVersion.version)).where(
                RelationshipGraphVersion.project_id == job.project_id
            )
        )
        or 0
    ) + 1
    graph = RelationshipGraphVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        story_bible_version_id=bible.id,
        version=graph_version,
        parent_version_id=None,
        status="DRAFT",
        schema_version=graph_payload.schema_version,
        config_version=STORY_STRUCTURE_CONFIG_VERSION,
        provider=result.provider,
        model=result.model,
        critic_json=canonical_json(
            {
                "generation_notes": graph_payload.generation_notes,
                "validation_issues": [item.model_dump(mode="json") for item in issues],
                "structure_critic": structure.critic,
            }
        ),
        content_hash=content_hash(graph_payload.model_dump(mode="json")),
        lock_version=1,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(graph)
    session.flush()
    replace_graph_payload(session, graph, graph_payload)

    proposal.status = "APPROVED"
    proposal.approved_at = now
    proposal.approved_by = str(job_payload["actor"])
    project = project_or_404(session, job.project_id)
    project.current_story_version_id = story.id
    project.status = "RELATIONSHIP_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="story_structure.ready",
        payload={
            "story_version_id": story.id,
            "story_bible_version_id": bible.id,
            "relationship_graph_id": graph.id,
            "provider": result.provider,
        },
    )
    session.flush()
    return graph


def materialize_story_package(
    session: Session, job: Job, result: TextGenerationResult
) -> ScriptVersion:
    job_payload = json.loads(job.input_json)
    proposal = session.get(ProposalVersion, str(job_payload["proposal_id"]))
    if proposal is None:
        raise ValueError("故事方向不存在")
    existing_story = session.scalar(
        select(StoryVersion).where(
            StoryVersion.project_id == job.project_id,
            StoryVersion.proposal_version == proposal.version,
        )
    )
    if existing_story is not None:
        existing_script = session.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == job.project_id)
            .order_by(ScriptVersion.created_at.desc())
        )
        if existing_script is None:
            raise ValueError("故事已存在但剧本缺失")
        return existing_script
    now = datetime.now(UTC)
    direction = job_payload["direction"]
    story_version = (
        session.scalar(
            select(func.max(StoryVersion.version)).where(StoryVersion.project_id == job.project_id)
        )
        or 0
    ) + 1
    story = StoryVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        version=story_version,
        proposal_version=proposal.version,
        source_proposal_ids_json=canonical_json(
            [proposal.id, *json.loads(proposal.source_proposal_ids_json)]
        ),
        parent_version_id=None,
        schema_version="story-dna-v1",
        provider=result.provider,
        model=result.model,
        config_version=STORY_PACKAGE_CONFIG_VERSION,
        title=str(direction["title"]),
        logline=str(direction["logline"]),
        payload_json=canonical_json(direction),
        content_hash=content_hash(direction),
        status="APPROVED",
        approved_at=now,
        approved_by=str(job_payload["actor"]),
        created_at=now,
    )
    session.add(story)
    session.flush()
    bible_payload = result.payload["story_bible"]
    bible_version = (
        session.scalar(
            select(func.max(StoryBibleVersion.version)).where(
                StoryBibleVersion.project_id == job.project_id
            )
        )
        or 0
    ) + 1
    bible = StoryBibleVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        story_version_id=story.id,
        version=bible_version,
        status="READY_FOR_REVIEW",
        payload_json=canonical_json(bible_payload),
        critic_json=canonical_json(result.payload["critic"]),
        content_hash=content_hash(bible_payload),
        parent_version_id=None,
        schema_version="story-bible-v2-independent-targeting",
        provider=result.provider,
        model=result.model,
        config_version=STORY_PACKAGE_CONFIG_VERSION,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(bible)
    session.flush()
    scripts_by_episode = {int(item["episode_ordinal"]): item for item in result.payload["scripts"]}
    created_scripts: list[ScriptVersion] = []
    for outline_payload in result.payload["outlines"]:
        episode_ordinal = int(outline_payload["episode_ordinal"])
        outline_version = (
            session.scalar(
                select(func.max(EpisodeOutlineVersion.version)).where(
                    EpisodeOutlineVersion.project_id == job.project_id,
                    EpisodeOutlineVersion.episode_ordinal == episode_ordinal,
                )
            )
            or 0
        ) + 1
        outline = EpisodeOutlineVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            story_bible_version_id=bible.id,
            episode_ordinal=episode_ordinal,
            version=outline_version,
            status="READY_FOR_REVIEW",
            payload_json=canonical_json(outline_payload),
            critic_json=canonical_json(result.payload["critic"]),
            content_hash=content_hash(outline_payload),
            parent_version_id=None,
            schema_version="episode-outline-v1",
            provider=result.provider,
            model=result.model,
            config_version=STORY_PACKAGE_CONFIG_VERSION,
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(outline)
        session.flush()
        script_payload = scripts_by_episode.get(episode_ordinal)
        if script_payload is None:
            continue
        script_version = (
            session.scalar(
                select(func.max(ScriptVersion.version)).where(
                    ScriptVersion.project_id == job.project_id,
                    ScriptVersion.episode_ordinal == episode_ordinal,
                )
            )
            or 0
        ) + 1
        script = ScriptVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            outline_version_id=outline.id,
            episode_ordinal=episode_ordinal,
            version=script_version,
            status="READY_FOR_REVIEW",
            payload_json=canonical_json(script_payload),
            critic_json=canonical_json(result.payload["critic"]),
            content_hash=content_hash(script_payload),
            parent_version_id=None,
            schema_version=SCRIPT_SCHEMA_VERSION,
            canonical_language=str(script_payload["canonical_language"]),
            provider=result.provider,
            model=result.model,
            config_version=SCRIPT_CONFIG_VERSION,
            estimated_duration_ms=int(script_payload["estimated_duration_ms"]),
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(script)
        session.flush()
        for scene_ordinal, scene_payload in enumerate(script_payload["scenes"], start=1):
            scene = ScriptScene(
                id=str(uuid4()),
                script_version_id=script.id,
                ordinal=scene_ordinal,
                heading=str(scene_payload["heading"]),
                location=str(scene_payload["location"]),
                time_of_day=str(scene_payload["time_of_day"]),
                purpose=str(scene_payload["purpose"]),
                emotion=str(scene_payload["emotion"]),
                duration_ms=int(scene_payload["duration_ms"]),
                bgm_intent=str(scene_payload["bgm_intent"]),
                sfx_intent_json=canonical_json(scene_payload["sfx_intents"]),
            )
            session.add(scene)
            session.flush()
            for line_ordinal, line_payload in enumerate(scene_payload["lines"], start=1):
                session.add(
                    ScriptLine(
                        id=str(uuid4()),
                        script_scene_id=scene.id,
                        ordinal=line_ordinal,
                        speaker_key=str(line_payload["speaker_key"]),
                        text=str(line_payload["text"]),
                        line_type=str(line_payload["line_type"]),
                        emotion=str(line_payload["emotion"]),
                        speech_rate=float(line_payload["speech_rate"]),
                        pause_after_ms=int(line_payload["pause_after_ms"]),
                        estimated_duration_ms=int(line_payload["estimated_duration_ms"]),
                        pronunciation_json=canonical_json(line_payload["pronunciation"]),
                        localization_json=canonical_json(line_payload["localizations"]),
                    )
                )
        created_scripts.append(script)
    if not created_scripts:
        raise ValueError("生成服务未返回任何剧本")
    proposal.status = "APPROVED"
    proposal.approved_at = now
    proposal.approved_by = str(job_payload["actor"])
    project = project_or_404(session, job.project_id)
    project.current_story_version_id = story.id
    project.status = "SCRIPT_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="story_package.ready",
        payload={
            "story_version_id": story.id,
            "story_bible_version_id": bible.id,
            "script_ids": [item.id for item in created_scripts],
            "provider": result.provider,
        },
    )
    session.flush()
    return created_scripts[0]


def script_package_generation_context(
    session: Session, job: Job
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    job_payload = json.loads(job.input_json)
    graph = session.get(RelationshipGraphVersion, str(job_payload["relationship_graph_id"]))
    if graph is None or graph.status != "APPROVED":
        raise ValueError("生成剧本前必须存在已批准的角色关系网")
    if graph.content_hash != job_payload.get("relationship_graph_content_hash"):
        raise ValueError("角色关系网内容哈希与任务快照不一致")
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None or bible.status != "APPROVED":
        raise ValueError("生成剧本前必须存在已批准的 Story Bible")
    story = session.get(StoryVersion, bible.story_version_id)
    if story is None or story.status != "APPROVED":
        raise ValueError("生成剧本前必须存在已批准的 Story DNA")
    brief = _latest_brief(session, job.project_id)
    bible_payload = json.loads(bible.payload_json)
    if not isinstance(bible_payload, dict):
        raise ValueError("Story Bible 不是有效对象")
    return (
        brief_provider_payload(brief),
        json.loads(story.payload_json),
        bible_payload,
        relationship_graph_script_context(session, graph),
    )


def materialize_script_package(
    session: Session, job: Job, result: TextGenerationResult
) -> ScriptVersion:
    job_payload = json.loads(job.input_json)
    graph = session.get(RelationshipGraphVersion, str(job_payload["relationship_graph_id"]))
    if graph is None or graph.status != "APPROVED":
        raise ValueError("已批准角色关系网不存在")
    existing_script = session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.relationship_graph_version_id == graph.id)
        .order_by(ScriptVersion.episode_ordinal, ScriptVersion.version.desc())
    )
    if existing_script is not None:
        return existing_script
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None or bible.status != "APPROVED":
        raise ValueError("已批准 Story Bible 不存在")

    package = ScriptPackageOutput.model_validate(result.payload)
    now = datetime.now(UTC)
    scripts_by_episode = {item.episode_ordinal: item for item in package.scripts}
    created_scripts: list[ScriptVersion] = []
    for outline_payload in package.outlines:
        episode_ordinal = outline_payload.episode_ordinal
        outline_version = (
            session.scalar(
                select(func.max(EpisodeOutlineVersion.version)).where(
                    EpisodeOutlineVersion.project_id == job.project_id,
                    EpisodeOutlineVersion.episode_ordinal == episode_ordinal,
                )
            )
            or 0
        ) + 1
        outline = EpisodeOutlineVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            story_bible_version_id=bible.id,
            relationship_graph_version_id=graph.id,
            episode_ordinal=episode_ordinal,
            version=outline_version,
            status="READY_FOR_REVIEW",
            payload_json=canonical_json(outline_payload.model_dump(mode="json")),
            critic_json=canonical_json(package.critic),
            content_hash=content_hash(outline_payload.model_dump(mode="json")),
            parent_version_id=None,
            schema_version="episode-outline-v2-relationship-driven",
            provider=result.provider,
            model=result.model,
            config_version=SCRIPT_PACKAGE_CONFIG_VERSION,
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(outline)
        session.flush()
        script_payload = scripts_by_episode.get(episode_ordinal)
        if script_payload is None:
            continue
        script_version = (
            session.scalar(
                select(func.max(ScriptVersion.version)).where(
                    ScriptVersion.project_id == job.project_id,
                    ScriptVersion.episode_ordinal == episode_ordinal,
                )
            )
            or 0
        ) + 1
        script_payload_json = script_payload.model_dump(mode="json")
        script = ScriptVersion(
            id=str(uuid4()),
            project_id=job.project_id,
            outline_version_id=outline.id,
            relationship_graph_version_id=graph.id,
            episode_ordinal=episode_ordinal,
            version=script_version,
            status="READY_FOR_REVIEW",
            payload_json=canonical_json(script_payload_json),
            critic_json=canonical_json(package.critic),
            content_hash=content_hash(script_payload_json),
            parent_version_id=None,
            schema_version=RELATIONSHIP_SCRIPT_SCHEMA_VERSION,
            canonical_language=script_payload.canonical_language,
            provider=result.provider,
            model=result.model,
            config_version=SCRIPT_PACKAGE_CONFIG_VERSION,
            estimated_duration_ms=script_payload.estimated_duration_ms,
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(script)
        session.flush()
        for scene_ordinal, scene_payload in enumerate(script_payload.scenes, start=1):
            scene = ScriptScene(
                id=str(uuid4()),
                script_version_id=script.id,
                ordinal=scene_ordinal,
                heading=scene_payload.heading,
                location=scene_payload.location,
                time_of_day=scene_payload.time_of_day,
                purpose=scene_payload.purpose,
                emotion=scene_payload.emotion,
                duration_ms=scene_payload.duration_ms,
                bgm_intent=scene_payload.bgm_intent,
                sfx_intent_json=canonical_json(scene_payload.sfx_intents),
            )
            session.add(scene)
            session.flush()
            for line_ordinal, line_payload in enumerate(scene_payload.lines, start=1):
                session.add(
                    ScriptLine(
                        id=str(uuid4()),
                        script_scene_id=scene.id,
                        ordinal=line_ordinal,
                        speaker_key=line_payload.speaker_key,
                        text=line_payload.text,
                        line_type=line_payload.line_type,
                        emotion=line_payload.emotion,
                        speech_rate=line_payload.speech_rate,
                        pause_after_ms=line_payload.pause_after_ms,
                        estimated_duration_ms=line_payload.estimated_duration_ms,
                        pronunciation_json=canonical_json(line_payload.pronunciation),
                        localization_json=canonical_json(line_payload.localizations),
                    )
                )
        created_scripts.append(script)
    if not created_scripts:
        raise ValueError("生成服务未返回任何剧本")
    project = project_or_404(session, job.project_id)
    project.status = "SCRIPT_READY"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="script_package.ready",
        payload={
            "relationship_graph_id": graph.id,
            "story_bible_version_id": bible.id,
            "script_ids": [item.id for item in created_scripts],
            "provider": result.provider,
        },
    )
    session.flush()
    return created_scripts[0]


def _version_payload(
    record: StoryBibleVersion | EpisodeOutlineVersion | ScriptVersion,
) -> dict[str, object]:
    value = {
        "id": record.id,
        "version": record.version,
        "status": record.status,
        "payload": json.loads(record.payload_json),
        "critic": json.loads(record.critic_json),
        "content_hash": record.content_hash,
        "schema_version": record.schema_version,
        "provider": record.provider,
        "model": record.model,
        "config_version": record.config_version,
        "approved_at": record.approved_at,
        "approved_by": record.approved_by,
        "created_at": record.created_at,
    }
    relationship_graph_version_id = getattr(record, "relationship_graph_version_id", None)
    if relationship_graph_version_id is not None:
        value["relationship_graph_version_id"] = relationship_graph_version_id
    return value


def story_workspace(session: Session, project_id: str) -> dict[str, object]:
    project = project_or_404(session, project_id)
    stories = session.scalars(
        select(StoryVersion)
        .where(StoryVersion.project_id == project_id)
        .order_by(StoryVersion.version.desc())
    ).all()
    bibles = session.scalars(
        select(StoryBibleVersion)
        .where(StoryBibleVersion.project_id == project_id)
        .order_by(StoryBibleVersion.version.desc())
    ).all()
    outlines = session.scalars(
        select(EpisodeOutlineVersion)
        .where(EpisodeOutlineVersion.project_id == project_id)
        .order_by(EpisodeOutlineVersion.episode_ordinal, EpisodeOutlineVersion.version.desc())
    ).all()
    scripts = session.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.episode_ordinal, ScriptVersion.version.desc())
    ).all()
    relationship_graphs = session.scalars(
        select(RelationshipGraphVersion)
        .where(RelationshipGraphVersion.project_id == project_id)
        .order_by(RelationshipGraphVersion.version.desc())
    ).all()
    script_payloads = []
    for script in scripts:
        scenes = session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script.id)
            .order_by(ScriptScene.ordinal)
        ).all()
        value = _version_payload(script)
        value["episode_ordinal"] = script.episode_ordinal
        value["estimated_duration_ms"] = script.estimated_duration_ms
        value["scenes"] = []
        for scene in scenes:
            lines = session.scalars(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == scene.id)
                .order_by(ScriptLine.ordinal)
            ).all()
            value["scenes"].append(
                {
                    "id": scene.id,
                    "ordinal": scene.ordinal,
                    "heading": scene.heading,
                    "location": scene.location,
                    "time_of_day": scene.time_of_day,
                    "purpose": scene.purpose,
                    "emotion": scene.emotion,
                    "duration_ms": scene.duration_ms,
                    "bgm_intent": scene.bgm_intent,
                    "sfx_intents": json.loads(scene.sfx_intent_json),
                    "lines": [
                        {
                            "id": line.id,
                            "ordinal": line.ordinal,
                            "speaker_key": line.speaker_key,
                            "text": line.text,
                            "line_type": line.line_type,
                            "emotion": line.emotion,
                            "speech_rate": line.speech_rate,
                            "pause_after_ms": line.pause_after_ms,
                            "estimated_duration_ms": line.estimated_duration_ms,
                            "pronunciation": json.loads(line.pronunciation_json),
                            "localizations": json.loads(line.localization_json),
                        }
                        for line in lines
                    ],
                }
            )
        script_payloads.append(value)
    approved_graph = next((item for item in relationship_graphs if item.status == "APPROVED"), None)
    current_script_graph_id = next(
        (
            item.relationship_graph_version_id
            for item in scripts
            if item.relationship_graph_version_id is not None
        ),
        None,
    )
    return {
        "directions": list_story_directions(session, project_id),
        "story_dna_versions": [
            {
                "id": item.id,
                "version": item.version,
                "proposal_version": item.proposal_version,
                "title": item.title,
                "logline": item.logline,
                "payload": json.loads(item.payload_json),
                "status": item.status,
                "content_hash": item.content_hash,
                "provider": item.provider,
                "model": item.model,
                "approved_at": item.approved_at,
                "approved_by": item.approved_by,
            }
            for item in stories
        ],
        "story_bible_versions": [_version_payload(item) for item in bibles],
        "relationship_graph_versions": [
            graph_to_read(session, item, project=project) for item in relationship_graphs
        ],
        "current_approved_relationship_graph_id": approved_graph.id if approved_graph else None,
        "has_unapproved_relationship_revision": any(
            item.status in {"DRAFT", "READY_FOR_REVIEW"} for item in relationship_graphs
        ),
        "current_script_relationship_graph_id": current_script_graph_id,
        "relationship_graph_stale": bool(
            current_script_graph_id
            and (
                (approved_graph and approved_graph.id != current_script_graph_id)
                or any(item.status in {"DRAFT", "READY_FOR_REVIEW"} for item in relationship_graphs)
            )
        ),
        "episode_outline_versions": [
            {**_version_payload(item), "episode_ordinal": item.episode_ordinal} for item in outlines
        ],
        "script_versions": script_payloads,
    }


def revise_script(
    session: Session,
    *,
    script_id: str,
    expected_version: int,
    scope: str,
    entity_id: str,
    changes: dict[str, object],
    commit: bool = True,
) -> dict[str, object]:
    source = session.get(ScriptVersion, script_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "剧本不存在"},
        )
    project = project_or_404(session, source.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "SCRIPT_READY" or source.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_EDIT_LOCKED",
                "message": "只能修改当前待审核剧本",
                "details": {
                    "project_status": project.status,
                    "script_status": source.status,
                },
            },
        )
    payload = json.loads(source.payload_json)
    source_scenes = list(
        session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == source.id)
            .order_by(ScriptScene.ordinal)
        ).all()
    )
    target_found = scope == "EPISODE"
    if scope == "EPISODE":
        payload["title"] = str(changes["title"])
    for scene_index, source_scene in enumerate(source_scenes):
        scene_payload = payload["scenes"][scene_index]
        if scope == "SCENE" and source_scene.id == entity_id:
            target_found = True
            for key, value in changes.items():
                scene_payload[key] = value
        source_lines = list(
            session.scalars(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == source_scene.id)
                .order_by(ScriptLine.ordinal)
            ).all()
        )
        for line_index, source_line in enumerate(source_lines):
            if scope != "LINE" or source_line.id != entity_id:
                continue
            target_found = True
            line_payload = scene_payload["lines"][line_index]
            for key, value in changes.items():
                line_payload[key] = value
            if "text" in changes or "speech_rate" in changes:
                text_value = str(line_payload["text"])
                rate = float(line_payload["speech_rate"])
                line_payload["estimated_duration_ms"] = max(
                    600,
                    min(20_000, round(len(text_value) * 280 / rate)),
                )
    if not target_found:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCRIPT_ENTITY_NOT_FOUND", "message": "待修改的剧本实体不存在"},
        )
    next_version = (
        session.scalar(
            select(func.max(ScriptVersion.version)).where(
                ScriptVersion.project_id == source.project_id,
                ScriptVersion.episode_ordinal == source.episode_ordinal,
            )
        )
        or 0
    ) + 1
    now = datetime.now(UTC)
    revised = ScriptVersion(
        id=str(uuid4()),
        project_id=source.project_id,
        outline_version_id=source.outline_version_id,
        relationship_graph_version_id=source.relationship_graph_version_id,
        episode_ordinal=source.episode_ordinal,
        version=next_version,
        status="READY_FOR_REVIEW",
        payload_json=canonical_json(payload),
        critic_json=canonical_json(
            {
                "status": "REVIEW_REQUIRED",
                "checks": {"human_edit": "PENDING"},
                "notes": [f"{scope} 修改后需重新审核"],
            }
        ),
        content_hash=content_hash(payload),
        parent_version_id=source.id,
        schema_version=source.schema_version,
        canonical_language=source.canonical_language,
        provider="human-edit",
        model="structured-script-editor-v1",
        config_version="script-edit-v1",
        estimated_duration_ms=int(payload["estimated_duration_ms"]),
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(revised)
    session.flush()
    for scene_ordinal, scene_payload in enumerate(payload["scenes"], start=1):
        scene = ScriptScene(
            id=str(uuid4()),
            script_version_id=revised.id,
            ordinal=scene_ordinal,
            heading=str(scene_payload["heading"]),
            location=str(scene_payload["location"]),
            time_of_day=str(scene_payload["time_of_day"]),
            purpose=str(scene_payload["purpose"]),
            emotion=str(scene_payload["emotion"]),
            duration_ms=int(scene_payload["duration_ms"]),
            bgm_intent=str(scene_payload["bgm_intent"]),
            sfx_intent_json=canonical_json(scene_payload["sfx_intents"]),
        )
        session.add(scene)
        session.flush()
        for line_ordinal, line_payload in enumerate(scene_payload["lines"], start=1):
            session.add(
                ScriptLine(
                    id=str(uuid4()),
                    script_scene_id=scene.id,
                    ordinal=line_ordinal,
                    speaker_key=str(line_payload["speaker_key"]),
                    text=str(line_payload["text"]),
                    line_type=str(line_payload["line_type"]),
                    emotion=str(line_payload["emotion"]),
                    speech_rate=float(line_payload["speech_rate"]),
                    pause_after_ms=int(line_payload["pause_after_ms"]),
                    estimated_duration_ms=int(line_payload["estimated_duration_ms"]),
                    pronunciation_json=canonical_json(line_payload["pronunciation"]),
                    localization_json=canonical_json(line_payload["localizations"]),
                )
            )
    source.status = "SUPERSEDED"
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="script.revised",
        payload={
            "source_script_id": source.id,
            "script_id": revised.id,
            "version": revised.version,
            "scope": scope,
            "entity_id": entity_id,
        },
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return {
        "id": revised.id,
        "version": revised.version,
        "status": revised.status,
        "parent_version_id": source.id,
        "content_hash": revised.content_hash,
        "project_lock_version": project.lock_version,
    }


def approve_script(
    session: Session,
    *,
    script_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[dict[str, object], JobRead, bool]:
    script = session.get(ScriptVersion, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "剧本不存在"})
    project = project_or_404(session, script.project_id)
    business_key = (
        f"{project.id}:PREPARE_PREPRODUCTION_ASSETS:"
        f"{project.current_story_version_id}:script-{script.id}"
    )
    existing_job = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if script.status == "APPROVED" and existing_job is not None:
        return _version_payload(script), job_to_read(existing_job), True
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "SCRIPT_READY" or script.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_NOT_READY",
                "message": "当前剧本不能批准",
                "details": {"project_status": project.status, "script_status": script.status},
            },
        )
    pending_relationship_revision = session.scalar(
        select(RelationshipGraphVersion.id).where(
            RelationshipGraphVersion.project_id == project.id,
            RelationshipGraphVersion.status.in_({"DRAFT", "READY_FOR_REVIEW"}),
        )
    )
    if pending_relationship_revision is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_RELATIONSHIP_GRAPH_OUTDATED",
                "message": "角色关系已有待确认修改版，当前剧本不能批准。",
                "user_action": "先批准关系修改版并重新生成剧本",
                "retryable": False,
                "details": {
                    "script_relationship_graph_id": script.relationship_graph_version_id,
                    "pending_relationship_graph_id": pending_relationship_revision,
                },
            },
        )
    now = datetime.now(UTC)
    script.status = "APPROVED"
    script.approved_at = now
    script.approved_by = actor
    project.status = "STORY_APPROVED"
    project.lock_version += 1
    project.updated_at = now
    story = session.get(StoryVersion, project.current_story_version_id)
    if story is None:
        raise ValueError("当前故事版本不存在")
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="PREPARE_PREPRODUCTION_ASSETS",
        entity_type="script_version",
        entity_id=script.id,
        idempotency_key=business_key,
        input_payload={
            "project_id": project.id,
            "story_version_id": story.id,
            "script_version_id": script.id,
            "story_version": story.version,
            "project_name": project.name,
            "config_version": "character-candidates-v2",
        },
        label=f"{project.name} · 剧本下游前期资产",
        stage="等待从批准剧本提取场景、道具与声音资产",
        trace_id=trace_id,
        estimated_seconds=4,
        retryable=True,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="script.approved",
        payload={"script_id": script.id, "episode_ordinal": script.episode_ordinal},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(job)
    return _version_payload(script), job_to_read(job), replayed
