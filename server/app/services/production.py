import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    AuditLog,
    Character,
    CharacterCandidate,
    Episode,
    Job,
    Project,
    ProposalVersion,
    Scene,
    Shot,
    StoryVersion,
    Take,
    TimelineItem,
    TimelineVersion,
)
from app.schemas import (
    CharacterCandidateRead,
    CharacterRead,
    JobRead,
    StoryRead,
    TimelineRead,
)
from app.services.assets import register_file
from app.services.events import append_event
from app.services.jobs import enqueue_job, job_to_read
from app.services.media import (
    PreviewFiles,
    PreviewShot,
    build_candidate_images,
    write_deterministic_png,
)
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def story_to_read(story: StoryVersion) -> StoryRead:
    return StoryRead(
        id=story.id,
        project_id=story.project_id,
        version=story.version,
        proposal_version=story.proposal_version,
        title=story.title,
        logline=story.logline,
        status=story.status,
        content_hash=story.content_hash,
        approved_at=story.approved_at,
        approved_by=story.approved_by,
    )


def _audit(
    session: Session,
    *,
    project_id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before_hash: str | None,
    after_hash: str | None,
    trace_id: str,
) -> None:
    session.add(
        AuditLog(
            id=str(uuid4()),
            project_id=project_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_hash=before_hash,
            after_hash=after_hash,
            trace_id=trace_id,
            created_at=datetime.now(UTC),
        )
    )


def _candidate_job(
    session: Session, *, project: Project, story: StoryVersion, trace_id: str
) -> tuple[Job, bool]:
    return enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_CHARACTER_CANDIDATES",
        entity_type="story_version",
        entity_id=story.id,
        idempotency_key=(
            f"{project.id}:GENERATE_CHARACTER_CANDIDATES:{story.id}:v{story.version}:mock-v1"
        ),
        input_payload={
            "project_id": project.id,
            "story_version_id": story.id,
            "story_version": story.version,
            "project_name": project.name,
            "config_version": "character-candidates-v1",
        },
        label=f"{project.name} · 主角候选",
        stage="等待生成角色候选",
        trace_id=trace_id,
        estimated_seconds=2,
        retryable=True,
    )


def approve_proposal(
    session: Session,
    *,
    project_id: str,
    proposal_version: int,
    expected_version: int,
    assumptions_confirmed: bool,
    actor: str,
    trace_id: str,
) -> tuple[StoryRead, JobRead, bool]:
    project = project_or_404(session, project_id)
    proposal = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == project_id,
            ProposalVersion.version == proposal_version,
        )
    )
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "导演方案版本不存在",
                "user_action": "刷新方案列表",
                "retryable": False,
                "details": {"version": proposal_version},
            },
        )
    existing_story = session.scalar(
        select(StoryVersion).where(
            StoryVersion.project_id == project_id,
            StoryVersion.proposal_version == proposal_version,
        )
    )
    if existing_story is not None:
        job, _ = _candidate_job(session, project=project, story=existing_story, trace_id=trace_id)
        session.commit()
        session.refresh(job)
        return story_to_read(existing_story), job_to_read(job), True
    if not assumptions_confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ASSUMPTIONS_CONFIRMATION_REQUIRED",
                "message": "批准故事前必须确认重要的 AI 补充假设",
                "user_action": "查看并确认方案中的补充假设",
                "retryable": False,
                "details": None,
            },
        )
    if project.status != "PROPOSAL_READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "message": "当前项目状态不能批准导演方案",
                "user_action": "等待方案生成完成或刷新项目",
                "retryable": False,
                "details": {"status": project.status},
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if proposal.status != "READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROPOSAL_NOT_READY",
                "message": "只有 READY 的方案可以批准",
                "user_action": "选择已完成的方案版本",
                "retryable": False,
                "details": {"status": proposal.status},
            },
        )

    payload = json.loads(proposal.payload_json)
    now = datetime.now(UTC)
    next_version = (
        session.scalar(
            select(func.max(StoryVersion.version)).where(StoryVersion.project_id == project_id)
        )
        or 0
    ) + 1
    story = StoryVersion(
        id=str(uuid4()),
        project_id=project_id,
        version=next_version,
        proposal_version=proposal_version,
        title=str(payload["title"]),
        logline=str(payload["logline"]),
        payload_json=canonical_json(payload),
        content_hash=content_hash(payload),
        status="APPROVED",
        approved_at=now,
        approved_by=actor,
        created_at=now,
    )
    session.add(story)
    session.flush()
    proposal.status = "APPROVED"
    proposal.approved_at = now
    proposal.approved_by = actor
    project.status = "STORY_APPROVED"
    project.current_story_version_id = story.id
    project.lock_version += 1
    project.updated_at = now
    job, replayed = _candidate_job(session, project=project, story=story, trace_id=trace_id)
    _audit(
        session,
        project_id=project_id,
        actor=actor,
        action="APPROVE_STORY",
        entity_type="story_version",
        entity_id=story.id,
        before_hash=None,
        after_hash=story.content_hash,
        trace_id=trace_id,
    )
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="story.approved",
        payload={"story_version_id": story.id, "version": story.version},
    )
    session.commit()
    session.refresh(job)
    return story_to_read(story), job_to_read(job), replayed


def list_characters(session: Session, project_id: str) -> list[CharacterRead]:
    project_or_404(session, project_id)
    characters = session.scalars(
        select(Character)
        .where(Character.project_id == project_id, Character.status != "SUPERSEDED")
        .order_by(Character.created_at)
    ).all()
    result: list[CharacterRead] = []
    for character in characters:
        candidates = session.scalars(
            select(CharacterCandidate)
            .where(CharacterCandidate.character_id == character.id)
            .order_by(CharacterCandidate.ordinal)
        ).all()
        result.append(
            CharacterRead(
                id=character.id,
                project_id=character.project_id,
                character_key=character.character_key,
                name=character.name,
                role=character.role,
                visual_brief=character.visual_brief,
                status=character.status,
                locked_candidate_id=character.locked_candidate_id,
                current_profile_version_id=character.current_profile_version_id,
                locked_identity_version_id=character.locked_identity_version_id,
                active_look_version_id=character.active_look_version_id,
                active_story_state_version_id=character.active_story_state_version_id,
                lock_version=character.lock_version,
                candidates=[
                    CharacterCandidateRead(
                        id=item.id,
                        character_id=item.character_id,
                        ordinal=item.ordinal,
                        asset_id=item.asset_id,
                        asset_url=f"/api/v1/assets/{item.asset_id}/content",
                        seed=item.seed,
                        status=item.status,
                        selected=item.selected,
                        batch_id=item.batch_id,
                        profile_version_id=item.profile_version_id,
                        review_status=item.review_status,
                    )
                    for item in candidates
                ],
            )
        )
    return result


def request_character_candidates(
    session: Session, *, project_id: str, trace_id: str
) -> tuple[JobRead, bool]:
    project = project_or_404(session, project_id)
    if project.current_story_version_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APPROVED_STORY_REQUIRED",
                "message": "生成角色候选前必须先批准故事",
                "user_action": "返回导演方案并批准",
                "retryable": False,
                "details": {"status": project.status},
            },
        )
    story = session.get(StoryVersion, project.current_story_version_id)
    if story is None:
        raise ValueError("项目当前故事版本不存在")
    job, replayed = _candidate_job(session, project=project, story=story, trace_id=trace_id)
    session.commit()
    session.refresh(job)
    return job_to_read(job), replayed


def materialize_character_candidates(session: Session, settings: Settings, job: Job) -> list[str]:
    existing = session.scalar(
        select(Character).where(
            Character.project_id == job.project_id,
            Character.character_key == "protagonist",
        )
    )
    if existing is not None:
        existing_ids = list(
            session.scalars(
                select(CharacterCandidate.id)
                .where(CharacterCandidate.character_id == existing.id)
                .order_by(CharacterCandidate.ordinal)
            ).all()
        )
        if len(existing_ids) >= 2:
            return existing_ids
    payload = json.loads(job.input_json)
    story = session.get(StoryVersion, str(payload["story_version_id"]))
    if story is None:
        raise ValueError("故事版本不存在")
    tmp_dir = settings.data_dir / "tmp" / job.id / "characters"
    image_paths = build_candidate_images(tmp_dir, story.content_hash)
    now = datetime.now(UTC)
    character = existing
    if character is None:
        character = Character(
            id=str(uuid4()),
            project_id=job.project_id,
            character_key="protagonist",
            name="主角",
            role="PROTAGONIST",
            visual_brief="现实电影感的当代都市女性；身份由锁定的候选编号保持稳定。",
            status="CANDIDATES_READY",
            locked_candidate_id=None,
            lock_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(character)
        session.flush()
    candidate_ids: list[str] = []
    for ordinal, image_path in enumerate(image_paths, start=1):
        existing_candidate = session.scalar(
            select(CharacterCandidate).where(
                CharacterCandidate.character_id == character.id,
                CharacterCandidate.ordinal == ordinal,
            )
        )
        if existing_candidate is not None:
            candidate_ids.append(existing_candidate.id)
            if image_path.exists():
                image_path.unlink()
            continue
        candidate_id = str(uuid4())
        asset = register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="character_candidate",
            source=image_path,
            source_entity_type="character_candidate",
            source_entity_id=candidate_id,
            mime="image/png",
            width=480,
            height=640,
        )
        session.add(
            CharacterCandidate(
                id=candidate_id,
                project_id=job.project_id,
                character_id=character.id,
                ordinal=ordinal,
                asset_id=asset.id,
                seed=f"{story.content_hash[:12]}-{ordinal}",
                status="READY",
                selected=False,
                created_at=now,
            )
        )
        candidate_ids.append(candidate_id)
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="characters.candidates_ready",
        payload={"character_id": character.id, "candidate_ids": candidate_ids},
    )
    session.flush()
    return candidate_ids


def lock_character(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    candidate_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
) -> tuple[CharacterRead, JobRead, bool]:
    project = project_or_404(session, project_id)
    character = session.get(Character, character_id)
    candidate = session.get(CharacterCandidate, candidate_id)
    if (
        character is None
        or character.project_id != project_id
        or candidate is None
        or candidate.character_id != character_id
    ):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "角色或候选不存在",
                "user_action": "刷新候选列表",
                "retryable": False,
                "details": {"character_id": character_id, "candidate_id": candidate_id},
            },
        )
    from app.services.preproduction import (
        is_script_preproduction,
        lock_character_for_preproduction,
    )

    if is_script_preproduction(session, project_id):
        return lock_character_for_preproduction(
            session,
            project=project,
            character=character,
            candidate=candidate,
            expected_version=expected_version,
            actor=actor,
            trace_id=trace_id,
        )
    business_key = (
        f"{project_id}:GENERATE_STORYBOARDS:{character_id}:candidate-{candidate_id}:story-v1"
    )
    existing_job = session.scalar(select(Job).where(Job.idempotency_key == business_key))
    if existing_job is not None:
        return list_characters(session, project_id)[0], job_to_read(existing_job), True
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "STORY_APPROVED" or candidate.status != "READY":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "message": "当前状态不能锁定角色候选",
                "user_action": "等待候选生成完成并刷新项目",
                "retryable": False,
                "details": {"project_status": project.status, "candidate_status": candidate.status},
            },
        )
    now = datetime.now(UTC)
    character.locked_candidate_id = candidate.id
    character.status = "LOCKED"
    character.lock_version += 1
    character.updated_at = now
    candidate.selected = True
    existing_shots = session.scalars(
        select(Shot)
        .join(Scene, Shot.scene_id == Scene.id)
        .join(Episode, Scene.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
    ).all()
    for existing_shot in existing_shots:
        try:
            bound_ids = json.loads(existing_shot.character_ids_json or "[]")
        except json.JSONDecodeError:
            bound_ids = []
        if not isinstance(bound_ids, list):
            bound_ids = []
        if character.id not in bound_ids:
            existing_shot.character_ids_json = json.dumps([*bound_ids, character.id])
            existing_shot.lock_version += 1
    project.status = "CHARACTER_LOCKED"
    project.lock_version += 1
    project.updated_at = now
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="GENERATE_STORYBOARDS",
        entity_type="character",
        entity_id=character_id,
        idempotency_key=business_key,
        input_payload={
            "project_id": project_id,
            "story_version_id": project.current_story_version_id,
            "character_id": character_id,
            "candidate_id": candidate_id,
            "config_version": "storyboards-v1",
        },
        label=f"{project.name} · 分镜",
        stage="等待生成动态分镜",
        trace_id=trace_id,
        estimated_seconds=8,
        retryable=True,
    )
    _audit(
        session,
        project_id=project_id,
        actor=actor,
        action="LOCK_CHARACTER",
        entity_type="character_candidate",
        entity_id=candidate_id,
        before_hash=None,
        after_hash=sha256(candidate_id.encode()).hexdigest(),
        trace_id=trace_id,
    )
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="character.locked",
        payload={"character_id": character_id, "candidate_id": candidate_id},
    )
    session.commit()
    session.refresh(job)
    return list_characters(session, project_id)[0], job_to_read(job), replayed


def materialize_storyboards(session: Session, settings: Settings, job: Job) -> Job:
    payload = json.loads(job.input_json)
    story = session.get(StoryVersion, str(payload["story_version_id"]))
    if story is None:
        raise ValueError("故事版本不存在")
    project = project_or_404(session, job.project_id)
    story_payload = json.loads(story.payload_json)
    episode = session.scalar(select(Episode).where(Episode.project_id == project.id))
    now = datetime.now(UTC)
    if episode is None:
        episode = Episode(
            id=str(uuid4()),
            project_id=project.id,
            code="S01E01",
            ordinal=1,
            title=story.title,
            target_duration_sec=project.target_duration_sec,
            status="IN_PROGRESS",
        )
        session.add(episode)
        session.flush()
        shot_ordinal = 1
        for scene_data in story_payload["scenes"]:
            scene = Scene(
                id=str(uuid4()),
                episode_id=episode.id,
                code=str(scene_data["code"]),
                ordinal=int(scene_data["code"]),
                title=str(scene_data["title"]),
                purpose=str(scene_data["purpose"]),
                duration_sec=int(scene_data["duration_sec"]),
                status="IN_PROGRESS",
            )
            session.add(scene)
            session.flush()
            for shot_data in scene_data["shots"]:
                code = str(shot_data["code"])
                session.add(
                    Shot(
                        id=str(uuid4()),
                        scene_id=scene.id,
                        code=code,
                        ordinal=shot_ordinal,
                        title=f"{scene.title} · {code}",
                        description=f"{scene.purpose}（模拟动态分镜）",
                        dialogue="",
                        duration_sec=int(shot_data["duration_sec"]),
                        status="READY",
                        shot_size=str(shot_data["shot_size"]),
                        camera_movement=str(shot_data["camera"]),
                        current_take=1,
                        candidate_take=None,
                        continuity="CLEAR",
                        location="演示场景",
                        time_of_day="日",
                        current_take_id=None,
                        character_ids_json=json.dumps([str(payload["character_id"])]),
                        character_look_version="Look V1",
                        lock_version=1,
                    )
                )
                shot_ordinal += 1
        session.commit()

    scenes = session.scalars(
        select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.ordinal)
    ).all()
    scene_ids = [scene.id for scene in scenes]
    shots = session.scalars(
        select(Shot).where(Shot.scene_id.in_(scene_ids)).order_by(Shot.ordinal)
    ).all()
    tmp_dir = settings.data_dir / "tmp" / job.id / "storyboards"
    width, height = (360, 640) if project.aspect_ratio == "9:16" else (640, 360)
    candidate = session.get(CharacterCandidate, str(payload["candidate_id"]))
    if candidate is None:
        raise ValueError("锁定的角色候选不存在")
    for shot in shots:
        existing_take = session.scalar(
            select(Take).where(Take.shot_id == shot.id, Take.is_current.is_(True))
        )
        if existing_take is not None:
            continue
        image_path = tmp_dir / f"{shot.code}-{shot.id}.png"
        write_deterministic_png(
            image_path,
            width,
            height,
            f"{story.content_hash}:{candidate.seed}:{shot.code}",
        )
        take_id = str(uuid4())
        asset = register_file(
            session,
            settings,
            project_id=project.id,
            kind="storyboard",
            source=image_path,
            source_entity_type="take",
            source_entity_id=take_id,
            mime="image/png",
            width=width,
            height=height,
            duration_ms=shot.duration_sec * 1000,
        )
        take = Take(
            id=take_id,
            shot_id=shot.id,
            kind="STORYBOARD",
            version=1,
            asset_id=asset.id,
            status="QC_PASSED",
            approval="APPROVED",
            is_current=True,
            parent_take_id=None,
            identity_status="PASSED",
            identity_score=1.0,
            identity_message="模拟分镜继承锁定角色候选与稳定种子",
            identity_reference_asset_ids_json=json.dumps([candidate.asset_id]),
            created_at=now,
        )
        session.add(take)
        session.flush()
        shot.current_take_id = take.id
        shot.status = "APPROVED"
    if project.status == "CHARACTER_LOCKED":
        project.status = "PRODUCING"
        project.lock_version += 1
        project.updated_at = datetime.now(UTC)
    hero_key = f"{project.id}:HERO_VIDEO:{episode.id}:S05:fixture-v1"
    next_job, _ = enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_HERO_FIXTURE",
        entity_type="episode",
        entity_id=episode.id,
        idempotency_key=hero_key,
        input_payload={
            "project_id": project.id,
            "episode_id": episode.id,
            "story_version_id": story.id,
            "requested_shot_code": "S05",
            "failure_plan": "HERO_VIDEO:S05:attempt1",
            "fallback": "KEN_BURNS",
            "config_version": "hero-fixture-v1",
        },
        label=f"{project.name} · 主镜头样例",
        stage="等待主镜头生成或可逆降级",
        trace_id=job.trace_id,
        estimated_seconds=2,
        retryable=True,
        priority=1,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="storyboards.ready",
        payload={"episode_id": episode.id, "shot_count": len(shots), "next_job_id": next_job.id},
    )
    session.commit()
    session.refresh(next_job)
    return next_job


def enqueue_preview_after_hero_fallback(session: Session, job: Job) -> Job:
    payload = json.loads(job.input_json)
    project = project_or_404(session, job.project_id)
    episode_id = str(payload["episode_id"])
    assemble_key = f"{project.id}:ASSEMBLE_PREVIEW:{episode_id}:timeline-v1:media-v1"
    next_job, _ = enqueue_job(
        session,
        project_id=project.id,
        job_type="ASSEMBLE_PREVIEW",
        entity_type="episode",
        entity_id=episode_id,
        idempotency_key=assemble_key,
        input_payload={
            "project_id": project.id,
            "episode_id": episode_id,
            "story_version_id": payload["story_version_id"],
            "timeline_version": 1,
            "config_version": "media-v1",
            "hero_evidence": {
                "requested": 1,
                "rendered": 0,
                "shot_code": payload["requested_shot_code"],
                "failure_plan": payload["failure_plan"],
                "fallback": payload["fallback"],
                "timeline_gap": False,
            },
        },
        label=f"{project.name} · 混合小样",
        stage="主镜头已降级，等待 FFmpeg 组装小样",
        trace_id=job.trace_id,
        estimated_seconds=15,
        retryable=True,
        priority=1,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="hero.fallback",
        payload={
            "shot_code": payload["requested_shot_code"],
            "failure_plan": payload["failure_plan"],
            "fallback": payload["fallback"],
            "next_job_id": next_job.id,
        },
    )
    session.commit()
    session.refresh(next_job)
    return next_job


def preview_inputs(
    session: Session, settings: Settings, job: Job
) -> tuple[Project, Episode, list[PreviewShot]]:
    payload = json.loads(job.input_json)
    project = project_or_404(session, job.project_id)
    episode = session.get(Episode, str(payload["episode_id"]))
    if episode is None:
        raise ValueError("Episode 不存在")
    scenes = session.scalars(
        select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.ordinal)
    ).all()
    shots = session.scalars(
        select(Shot).where(Shot.scene_id.in_([scene.id for scene in scenes])).order_by(Shot.ordinal)
    ).all()
    preview_shots: list[PreviewShot] = []
    for shot in shots:
        take = session.get(Take, shot.current_take_id) if shot.current_take_id else None
        asset = session.get(Asset, take.asset_id) if take else None
        if take is None or asset is None or take.status != "QC_PASSED":
            raise ValueError(f"镜头 {shot.code} 缺少可用的当前素材版本")
        preview_shots.append(
            PreviewShot(
                id=shot.id,
                code=shot.code,
                title=shot.title,
                dialogue=shot.dialogue,
                duration_sec=shot.duration_sec,
                image_path=settings.data_dir / asset.storage_key,
            )
        )
    session.commit()
    return project, episode, preview_shots


def register_preview(
    session: Session,
    settings: Settings,
    *,
    job: Job,
    episode: Episode,
    preview_shots: list[PreviewShot],
    files: PreviewFiles,
) -> TimelineVersion:
    existing = session.scalar(
        select(TimelineVersion).where(
            TimelineVersion.project_id == job.project_id,
            TimelineVersion.version == 1,
        )
    )
    if existing is not None:
        return existing
    assets = {
        "mp4": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="preview_mp4",
            source=files.mp4,
            source_entity_type="timeline",
            source_entity_id=episode.id,
            mime="video/mp4",
            width=files.width,
            height=files.height,
            duration_ms=files.duration_ms,
        ),
        "srt": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="subtitle_srt",
            source=files.srt,
            source_entity_type="timeline",
            source_entity_id=episode.id,
            mime="application/x-subrip",
            duration_ms=files.duration_ms,
        ),
        "vtt": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="subtitle_vtt",
            source=files.vtt,
            source_entity_type="timeline",
            source_entity_id=episode.id,
            mime="text/vtt",
            duration_ms=files.duration_ms,
        ),
        "manifest": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="preview_manifest",
            source=files.manifest,
            source_entity_type="timeline",
            source_entity_id=episode.id,
            mime="application/json",
            duration_ms=files.duration_ms,
        ),
    }
    baseline = content_hash(
        {
            "shots": [shot.id for shot in preview_shots],
            "assets": {key: asset.sha256 for key, asset in assets.items()},
        }
    )
    now = datetime.now(UTC)
    timeline = TimelineVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        episode_id=episode.id,
        version=1,
        status="READY",
        mp4_asset_id=assets["mp4"].id,
        srt_asset_id=assets["srt"].id,
        vtt_asset_id=assets["vtt"].id,
        manifest_asset_id=assets["manifest"].id,
        duration_ms=files.duration_ms,
        baseline_hash=baseline,
        parent_timeline_id=None,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(timeline)
    session.flush()
    cursor = 0
    for ordinal, shot in enumerate(preview_shots, start=1):
        source_shot = session.get(Shot, shot.id)
        if source_shot is None or source_shot.current_take_id is None:
            raise ValueError("时间线镜头缺少当前素材版本")
        end = cursor + shot.duration_sec * 1000
        session.add(
            TimelineItem(
                id=str(uuid4()),
                timeline_id=timeline.id,
                ordinal=ordinal,
                shot_id=shot.id,
                take_id=source_shot.current_take_id,
                start_ms=cursor,
                end_ms=end,
            )
        )
        cursor = end
    project = project_or_404(session, job.project_id)
    project.status = "PREVIEW_READY"
    project.current_timeline_version_id = timeline.id
    project.timeline_version = 1
    project.lock_version += 1
    project.updated_at = now
    episode.status = "PREVIEW_READY"
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="preview.ready",
        payload={
            "timeline_id": timeline.id,
            "version": timeline.version,
            "duration_ms": timeline.duration_ms,
        },
    )
    session.flush()
    return timeline


def timeline_to_read(session: Session, timeline: TimelineVersion) -> TimelineRead:
    assets = {
        "mp4": f"/api/v1/assets/{timeline.mp4_asset_id}/content",
        "srt": f"/api/v1/assets/{timeline.srt_asset_id}/content",
        "vtt": f"/api/v1/assets/{timeline.vtt_asset_id}/content",
        "manifest": f"/api/v1/assets/{timeline.manifest_asset_id}/content",
    }
    if timeline.stems_manifest_asset_id:
        assets["stems_manifest"] = f"/api/v1/assets/{timeline.stems_manifest_asset_id}/content"
    if timeline.qc_report_asset_id:
        assets["qc_report"] = f"/api/v1/assets/{timeline.qc_report_asset_id}/content"
    return TimelineRead(
        id=timeline.id,
        project_id=timeline.project_id,
        episode_id=timeline.episode_id,
        version=timeline.version,
        status=timeline.status,
        duration_ms=timeline.duration_ms,
        baseline_hash=timeline.baseline_hash,
        approved_at=timeline.approved_at,
        assets=assets,
    )


def list_previews(session: Session, project_id: str) -> list[TimelineRead]:
    project_or_404(session, project_id)
    timelines = session.scalars(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id)
        .order_by(TimelineVersion.version.desc())
    ).all()
    return [timeline_to_read(session, item) for item in timelines]
