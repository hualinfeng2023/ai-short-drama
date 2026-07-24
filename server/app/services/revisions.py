import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    AuditLog,
    ChangeSet,
    Episode,
    Job,
    Project,
    ReviewGate,
    Scene,
    Shot,
    Take,
    TimelineItem,
    TimelineVersion,
    WholeFilmQualityCheck,
    WorkflowRun,
)
from app.schemas import (
    ChangeSetRead,
    JobRead,
    PreviewCompareRead,
    RevisionImpactRead,
    RevisionScope,
    TimelineRead,
)
from app.services.assets import register_file
from app.services.events import append_event
from app.services.jobs import enqueue_job, job_to_read
from app.services.media import PreviewFiles, PreviewShot, write_deterministic_png
from app.services.production import timeline_to_read
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def _timeline_or_404(session: Session, timeline_id: str) -> TimelineVersion:
    timeline = session.get(TimelineVersion, timeline_id)
    if timeline is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "小样版本不存在",
                "user_action": "刷新小样版本列表",
                "retryable": False,
                "details": {"id": timeline_id},
            },
        )
    return timeline


def _current_timeline(session: Session, project: Project) -> TimelineVersion:
    if project.current_timeline_version_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PREVIEW_REQUIRED",
                "message": "执行该操作前必须先生成小样",
                "user_action": "等待小样任务完成",
                "retryable": True,
                "details": {"status": project.status},
            },
        )
    timeline = session.get(TimelineVersion, project.current_timeline_version_id)
    if timeline is None:
        raise RuntimeError("项目当前时间线不存在")
    return timeline


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


def _scope_shot_ids(
    session: Session, project: Project, scope: RevisionScope
) -> tuple[list[str], list[Shot]]:
    episode_ids = select(Episode.id).where(Episode.project_id == project.id)
    scene_ids = select(Scene.id).where(Scene.episode_id.in_(episode_ids))
    project_shots = session.scalars(
        select(Shot).where(Shot.scene_id.in_(scene_ids)).order_by(Shot.ordinal)
    ).all()
    by_id = {shot.id: shot for shot in project_shots}
    if scope.type == "PROJECT":
        if project.id not in scope.ids:
            raise HTTPException(status_code=422, detail="项目范围编号必须包含当前项目编号")
        return list(by_id), project_shots
    if scope.type == "SHOT":
        missing = [item for item in scope.ids if item not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"局部修改镜头不存在：{missing[0]}")
        return list(dict.fromkeys(scope.ids)), [by_id[item] for item in dict.fromkeys(scope.ids)]
    valid_scene_ids = {
        scene.id
        for scene in session.scalars(
            select(Scene).where(
                Scene.id.in_(scope.ids),
                Scene.episode_id.in_(episode_ids),
            )
        )
    }
    if len(valid_scene_ids) != len(set(scope.ids)):
        raise HTTPException(status_code=404, detail="局部修改场景不存在")
    selected = [shot for shot in project_shots if shot.scene_id in valid_scene_ids]
    return [shot.id for shot in selected], selected


def _intent_type(instruction: str) -> str:
    if any(token in instruction for token in ("台词", "对白", "说", "字幕", "半句")):
        return "DIALOGUE"
    if any(token in instruction for token in ("节奏", "时长", "停顿", "加快", "放慢")):
        return "TIMING"
    if any(token in instruction for token in ("声音", "音乐", "音效", "配音")):
        return "AUDIO"
    return "VISUAL"


def analyze_revision(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    scope: RevisionScope,
    instruction: str,
) -> RevisionImpactRead:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    base = _current_timeline(session, project)
    shot_ids, _shots = _scope_shot_ids(session, project, scope)
    items = session.scalars(
        select(TimelineItem)
        .where(TimelineItem.timeline_id == base.id)
        .order_by(TimelineItem.ordinal)
    ).all()
    take_assets: dict[str, str] = {}
    preserved_objects: list[dict[str, object]] = []
    for item in items:
        take = session.get(Take, item.take_id)
        asset = session.get(Asset, take.asset_id) if take else None
        if asset is not None:
            take_assets[item.shot_id] = asset.sha256
            if item.shot_id not in shot_ids and take is not None:
                preserved_objects.append(
                    {
                        "shot_id": item.shot_id,
                        "take_id": take.id,
                        "asset_id": asset.id,
                        "asset_hash": asset.sha256,
                        "approval": take.approval,
                        "is_current": take.is_current,
                    }
                )
    intent_type = _intent_type(instruction)
    changed_asset_types = ["subtitle_srt", "subtitle_vtt", "timeline"]
    if intent_type == "VISUAL":
        changed_asset_types.insert(0, "storyboard")
    elif intent_type == "AUDIO":
        changed_asset_types.insert(0, "temporary_audio")
    return RevisionImpactRead(
        base_timeline_id=base.id,
        scope={"type": scope.type, "ids": shot_ids},
        intent={"type": intent_type, "instruction": instruction},
        affected={
            "shots": shot_ids,
            "asset_types": changed_asset_types,
            "preserved_hashes": [
                digest for shot_id, digest in take_assets.items() if shot_id not in shot_ids
            ],
            "preserved_objects": preserved_objects,
        },
        estimated_points=12 * len(shot_ids),
        estimated_seconds=max(4, 3 * len(shot_ids)),
        requires_confirmation=True,
        story_dna_changed=scope.type == "PROJECT",
        touches_approved=base.status == "APPROVED",
    )


def change_set_to_read(change_set: ChangeSet) -> ChangeSetRead:
    return ChangeSetRead(
        id=change_set.id,
        project_id=change_set.project_id,
        base_timeline_id=change_set.base_timeline_id,
        scope=json.loads(change_set.scope_json),
        instruction=change_set.instruction,
        impact=json.loads(change_set.impact_json),
        estimate=json.loads(change_set.estimate_json),
        status=change_set.status,
        result_timeline_id=change_set.result_timeline_id,
        created_at=change_set.created_at,
    )


def create_revision(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    scope: RevisionScope,
    instruction: str,
    confirmed: bool,
    idempotency_key: str,
    trace_id: str,
    commit: bool = True,
) -> tuple[ChangeSetRead, JobRead, bool]:
    if not confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVISION_CONFIRMATION_REQUIRED",
                "message": "执行局部修改前必须确认影响范围与估算",
                "user_action": "先查看影响并确认",
                "retryable": False,
                "details": None,
            },
        )
    impact = analyze_revision(
        session,
        project_id=project_id,
        expected_version=expected_version,
        scope=scope,
        instruction=instruction,
    )
    existing_job = session.scalar(
        select(Job).where(Job.idempotency_key == f"revision:{project_id}:{idempotency_key}")
    )
    if existing_job is not None:
        existing_change = session.get(ChangeSet, existing_job.entity_id)
        if existing_change is None:
            raise RuntimeError("局部修改任务引用了不存在的变更集")
        return change_set_to_read(existing_change), job_to_read(existing_job), True

    now = datetime.now(UTC)
    change_set = ChangeSet(
        id=str(uuid4()),
        project_id=project_id,
        base_timeline_id=impact.base_timeline_id,
        scope_json=canonical_json(impact.scope),
        instruction=instruction,
        impact_json=canonical_json(impact.model_dump()),
        estimate_json=canonical_json(
            {"points": impact.estimated_points, "seconds": impact.estimated_seconds}
        ),
        status="PENDING",
        result_timeline_id=None,
        created_at=now,
    )
    session.add(change_set)
    session.flush()
    job, replayed = enqueue_job(
        session,
        project_id=project_id,
        job_type="APPLY_REVISION",
        entity_type="change_set",
        entity_id=change_set.id,
        idempotency_key=f"revision:{project_id}:{idempotency_key}",
        input_payload={
            "change_set_id": change_set.id,
            "base_timeline_id": impact.base_timeline_id,
            "scope": impact.scope,
            "intent": impact.intent,
            "config_version": "revision-v1",
        },
        label="局部修改 · 生成新时间线",
        stage="等待执行已确认的变更集",
        trace_id=trace_id,
        estimated_seconds=impact.estimated_seconds,
        retryable=True,
        priority=1,
    )
    project = project_or_404(session, project_id)
    project.status = "PRODUCING"
    project.preview_approved = False
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project_id,
        job_id=job.id,
        event_type="revision.created",
        payload={"change_set_id": change_set.id, "base_timeline_id": impact.base_timeline_id},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(job)
    return change_set_to_read(change_set), job_to_read(job), replayed


def revision_or_404(session: Session, change_set_id: str) -> ChangeSetRead:
    change_set = session.get(ChangeSet, change_set_id)
    if change_set is None:
        raise HTTPException(status_code=404, detail="变更集不存在")
    return change_set_to_read(change_set)


def revision_inputs(
    session: Session, settings: Settings, job: Job
) -> tuple[Project, Episode, ChangeSet, list[PreviewShot], dict[str, str]]:
    change_set = session.get(ChangeSet, job.entity_id)
    if change_set is None:
        raise RuntimeError("变更集不存在")
    if change_set.result_timeline_id is not None:
        timeline = _timeline_or_404(session, change_set.result_timeline_id)
        episode = session.get(Episode, timeline.episode_id)
        if episode is None:
            raise RuntimeError("时间线所属剧集不存在")
        return project_or_404(session, job.project_id), episode, change_set, [], {}
    change_set.status = "RUNNING"
    base = _timeline_or_404(session, change_set.base_timeline_id)
    episode = session.get(Episode, base.episode_id)
    if episode is None:
        raise RuntimeError("时间线所属剧集不存在")
    project = project_or_404(session, job.project_id)
    scope = json.loads(change_set.scope_json)
    intent = json.loads(change_set.impact_json)["intent"]
    target_ids = set(scope["ids"])
    items = session.scalars(
        select(TimelineItem)
        .where(TimelineItem.timeline_id == base.id)
        .order_by(TimelineItem.ordinal)
    ).all()
    _assert_preserved_objects(session, change_set)
    preview_shots: list[PreviewShot] = []
    take_ids: dict[str, str] = {}
    for item in items:
        shot = session.get(Shot, item.shot_id)
        take = session.get(Take, item.take_id)
        asset = session.get(Asset, take.asset_id) if take else None
        if shot is None or take is None or asset is None:
            raise RuntimeError("基础时间线引用了缺失的镜头、版本或资产")
        selected_take = take
        image_path = settings.data_dir / asset.storage_key
        if item.shot_id in target_ids and intent["type"] == "VISUAL":
            existing_asset = session.scalar(
                select(Asset).where(
                    Asset.project_id == project.id,
                    Asset.kind == "storyboard_revision",
                    Asset.source_entity_type == "change_set",
                    Asset.source_entity_id == change_set.id,
                )
            )
            existing = (
                session.scalar(select(Take).where(Take.asset_id == existing_asset.id))
                if existing_asset is not None
                else None
            )
            next_version = (
                session.scalar(select(func.max(Take.version)).where(Take.shot_id == shot.id)) or 0
            ) + 1
            if existing is None:
                width, height = (360, 640) if project.aspect_ratio == "9:16" else (640, 360)
                source = settings.data_dir / "tmp" / job.id / "revision-images" / f"{shot.id}.png"
                write_deterministic_png(
                    source,
                    width,
                    height,
                    f"{base.baseline_hash}:{change_set.instruction}:{shot.id}",
                )
                take_id = str(uuid4())
                new_asset = register_file(
                    session,
                    settings,
                    project_id=project.id,
                    kind="storyboard_revision",
                    source=source,
                    source_entity_type="change_set",
                    source_entity_id=change_set.id,
                    mime="image/png",
                    width=width,
                    height=height,
                    duration_ms=shot.duration_sec * 1000,
                )
                existing = Take(
                    id=take_id,
                    shot_id=shot.id,
                    kind="STORYBOARD_REVISION",
                    version=next_version,
                    asset_id=new_asset.id,
                    status="QC_PASSED",
                    approval="PENDING_REVIEW",
                    is_current=False,
                    parent_take_id=take.id,
                    created_at=datetime.now(UTC),
                )
                session.add(existing)
                session.flush()
            selected_take = existing
            selected_asset = session.get(Asset, selected_take.asset_id)
            if selected_asset is None:
                raise RuntimeError("局部修改版本资产不存在")
            image_path = settings.data_dir / selected_asset.storage_key
        dialogue = shot.dialogue
        if item.shot_id in target_ids and intent["type"] in {"DIALOGUE", "AUDIO", "TIMING"}:
            dialogue = change_set.instruction
        preview_shots.append(
            PreviewShot(
                id=shot.id,
                code=shot.code,
                title=shot.title,
                dialogue=dialogue,
                duration_sec=shot.duration_sec,
                image_path=image_path,
            )
        )
        take_ids[shot.id] = selected_take.id
    session.commit()
    return project, episode, change_set, preview_shots, take_ids


def _preserved_objects(change_set: ChangeSet) -> list[dict[str, object]]:
    impact = json.loads(change_set.impact_json)
    affected = impact.get("affected", {}) if isinstance(impact, dict) else {}
    preserved = affected.get("preserved_objects", []) if isinstance(affected, dict) else []
    return [item for item in preserved if isinstance(item, dict)]


def _assert_preserved_objects(
    session: Session,
    change_set: ChangeSet,
    *,
    selected_take_ids: dict[str, str] | None = None,
) -> None:
    changed: list[dict[str, object]] = []
    for expected in _preserved_objects(change_set):
        shot_id = expected.get("shot_id")
        take_id = expected.get("take_id")
        asset_id = expected.get("asset_id")
        if not all(isinstance(value, str) for value in (shot_id, take_id, asset_id)):
            changed.append({"reason": "INVALID_PRESERVED_SNAPSHOT", "expected": expected})
            continue
        take = session.get(Take, take_id)
        asset = session.get(Asset, asset_id)
        reasons: list[str] = []
        if take is None or take.shot_id != shot_id:
            reasons.append("TAKE_CHANGED")
        if asset is None or take is None or take.asset_id != asset.id:
            reasons.append("ASSET_REFERENCE_CHANGED")
        if asset is not None and asset.sha256 != expected.get("asset_hash"):
            reasons.append("ASSET_HASH_CHANGED")
        if take is not None and take.approval != expected.get("approval"):
            reasons.append("APPROVAL_CHANGED")
        if take is not None and take.is_current is not expected.get("is_current"):
            reasons.append("CURRENT_SELECTION_CHANGED")
        if (
            selected_take_ids is not None
            and selected_take_ids.get(shot_id) != take_id
        ):
            reasons.append("TIMELINE_REFERENCE_CHANGED")
        if reasons:
            changed.append(
                {
                    "shot_id": shot_id,
                    "take_id": take_id,
                    "reasons": reasons,
                }
            )
    if changed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRESERVED_OBJECT_CHANGED",
                "message": "范围外受保护素材已经变化，变更集不能继续执行",
                "user_action": "重新分析影响范围并创建新的变更集",
                "retryable": False,
                "details": {"changed": changed},
            },
        )


def register_revision_preview(
    session: Session,
    settings: Settings,
    *,
    job: Job,
    episode: Episode,
    change_set: ChangeSet,
    preview_shots: list[PreviewShot],
    take_ids: dict[str, str],
    files: PreviewFiles,
) -> TimelineVersion:
    if change_set.result_timeline_id is not None:
        return _timeline_or_404(session, change_set.result_timeline_id)
    _assert_preserved_objects(
        session,
        change_set,
        selected_take_ids=take_ids,
    )
    next_version = (
        session.scalar(
            select(func.max(TimelineVersion.version)).where(
                TimelineVersion.project_id == job.project_id
            )
        )
        or 0
    ) + 1
    assets = {
        "mp4": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="preview_mp4",
            source=files.mp4,
            source_entity_type="change_set",
            source_entity_id=change_set.id,
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
            source_entity_type="change_set",
            source_entity_id=change_set.id,
            mime="application/x-subrip",
            duration_ms=files.duration_ms,
        ),
        "vtt": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="subtitle_vtt",
            source=files.vtt,
            source_entity_type="change_set",
            source_entity_id=change_set.id,
            mime="text/vtt",
            duration_ms=files.duration_ms,
        ),
        "manifest": register_file(
            session,
            settings,
            project_id=job.project_id,
            kind="preview_manifest",
            source=files.manifest,
            source_entity_type="change_set",
            source_entity_id=change_set.id,
            mime="application/json",
            duration_ms=files.duration_ms,
        ),
    }
    baseline = content_hash(
        {
            "base_timeline_id": change_set.base_timeline_id,
            "change_set_id": change_set.id,
            "take_ids": take_ids,
            "assets": {name: asset.sha256 for name, asset in assets.items()},
        }
    )
    now = datetime.now(UTC)
    timeline = TimelineVersion(
        id=str(uuid4()),
        project_id=job.project_id,
        episode_id=episode.id,
        version=next_version,
        status="READY",
        mp4_asset_id=assets["mp4"].id,
        srt_asset_id=assets["srt"].id,
        vtt_asset_id=assets["vtt"].id,
        manifest_asset_id=assets["manifest"].id,
        duration_ms=files.duration_ms,
        baseline_hash=baseline,
        parent_timeline_id=change_set.base_timeline_id,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(timeline)
    session.flush()
    cursor = 0
    for ordinal, shot in enumerate(preview_shots, start=1):
        end = cursor + shot.duration_sec * 1000
        session.add(
            TimelineItem(
                id=str(uuid4()),
                timeline_id=timeline.id,
                ordinal=ordinal,
                shot_id=shot.id,
                take_id=take_ids[shot.id],
                start_ms=cursor,
                end_ms=end,
            )
        )
        cursor = end
    change_set.status = "SUCCEEDED"
    change_set.result_timeline_id = timeline.id
    project = project_or_404(session, job.project_id)
    project.current_timeline_version_id = timeline.id
    project.timeline_version = timeline.version
    project.status = "PREVIEW_READY"
    project.preview_approved = False
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="revision.ready",
        payload={
            "change_set_id": change_set.id,
            "timeline_id": timeline.id,
            "version": timeline.version,
        },
    )
    session.commit()
    session.refresh(timeline)
    return timeline


def get_timeline(session: Session, timeline_id: str) -> TimelineRead:
    return timeline_to_read(session, _timeline_or_404(session, timeline_id))


def compare_timelines(session: Session, left_id: str, right_id: str) -> PreviewCompareRead:
    left = _timeline_or_404(session, left_id)
    right = _timeline_or_404(session, right_id)
    if left.project_id != right.project_id:
        raise HTTPException(status_code=409, detail="只能比较同一项目的时间线")
    fields = {
        "mp4": (left.mp4_asset_id, right.mp4_asset_id),
        "srt": (left.srt_asset_id, right.srt_asset_id),
        "vtt": (left.vtt_asset_id, right.vtt_asset_id),
        "manifest": (left.manifest_asset_id, right.manifest_asset_id),
    }
    left_items = {
        item.shot_id: item.take_id
        for item in session.scalars(select(TimelineItem).where(TimelineItem.timeline_id == left.id))
    }
    right_items = {
        item.shot_id: item.take_id
        for item in session.scalars(
            select(TimelineItem).where(TimelineItem.timeline_id == right.id)
        )
    }
    changed_shots = sorted(
        shot_id
        for shot_id in set(left_items) | set(right_items)
        if left_items.get(shot_id) != right_items.get(shot_id)
    )
    changed_assets = [name for name, pair in fields.items() if pair[0] != pair[1]]
    unchanged_assets = [name for name, pair in fields.items() if pair[0] == pair[1]]
    return PreviewCompareRead(
        left=timeline_to_read(session, left),
        right=timeline_to_read(session, right),
        changed_assets=changed_assets,
        unchanged_assets=unchanged_assets,
        changed_shot_ids=changed_shots,
        summary=(
            f"时间线第 {left.version} 版 → 第 {right.version} 版："
            f"{len(changed_shots)} 个镜头版本发生变化，{len(changed_assets)} 类媒体发生变化"
        ),
    )


def _apply_timeline_takes(session: Session, timeline: TimelineVersion) -> None:
    items = session.scalars(
        select(TimelineItem).where(TimelineItem.timeline_id == timeline.id)
    ).all()
    for item in items:
        shot = session.get(Shot, item.shot_id)
        selected_take = session.get(Take, item.take_id)
        if shot is None or selected_take is None:
            raise RuntimeError("时间线素材版本应用失败")
        for take in session.scalars(
            select(Take).where(
                Take.shot_id == shot.id,
                Take.kind == selected_take.kind,
            )
        ):
            take.is_current = take.id == selected_take.id
            if take.id == selected_take.id:
                take.approval = "APPROVED"
            elif take.approval == "APPROVED":
                take.approval = "SUPERSEDED"
        shot.current_take_id = selected_take.id
        shot.current_take = selected_take.version
        shot.candidate_take = None
        shot.status = "APPROVED"
        shot.lock_version += 1


def approve_timeline(
    session: Session,
    *,
    timeline_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
    record_audit: bool = True,
) -> TimelineRead:
    timeline = _timeline_or_404(session, timeline_id)
    project = project_or_404(session, timeline.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.current_timeline_version_id != timeline.id:
        raise HTTPException(status_code=409, detail="只能批准当前小样")
    if timeline.status == "APPROVED":
        return timeline_to_read(session, timeline)
    change_set = session.scalar(
        select(ChangeSet).where(ChangeSet.result_timeline_id == timeline.id)
    )
    if change_set is not None:
        timeline_take_ids = {
            item.shot_id: item.take_id
            for item in session.scalars(
                select(TimelineItem).where(TimelineItem.timeline_id == timeline.id)
            ).all()
        }
        _assert_preserved_objects(
            session,
            change_set,
            selected_take_ids=timeline_take_ids,
        )
    now = datetime.now(UTC)
    gate = session.scalar(
        select(ReviewGate).where(
            ReviewGate.project_id == project.id,
            ReviewGate.gate_key == "G5",
            ReviewGate.entity_type == "timeline",
            ReviewGate.entity_id == timeline.id,
        )
    )
    if timeline.baseline_hash.startswith("multitrack:"):
        checks = list(
            session.scalars(
                select(WholeFilmQualityCheck).where(
                    WholeFilmQualityCheck.timeline_id == timeline.id
                )
            ).all()
        )
        failed = [item.check_type for item in checks if item.status == "FAILED"]
        if len(checks) < 8 or failed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WHOLE_FILM_QC_REQUIRED",
                    "message": "批准第 5 阶段前必须完成全部整片质量检查",
                    "retryable": False,
                    "details": {"failed_checks": failed, "check_count": len(checks)},
                },
            )
        if gate is None or gate.status not in {"PENDING", "APPROVED"}:
            raise HTTPException(status_code=409, detail="当前时间线缺少可批准的第 5 阶段")
    _apply_timeline_takes(session, timeline)
    timeline.status = "APPROVED"
    timeline.approved_at = now
    timeline.approved_by = actor
    project.status = "APPROVED"
    project.preview_approved = True
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    if gate is not None:
        gate.status = "APPROVED"
        gate.decision = "APPROVE"
        gate.decided_by = actor
        gate.decided_at = now
        workflow = session.get(WorkflowRun, gate.workflow_run_id)
        if workflow is not None:
            workflow.status = "COMPLETED"
            workflow.current_gate = None
            workflow.completed_at = now
            workflow.updated_at = now
    if record_audit:
        _audit(
            session,
            project_id=project.id,
            actor=actor,
            action="APPROVE_PREVIEW",
            entity_type="timeline",
            entity_id=timeline.id,
            before_hash=None,
            after_hash=timeline.baseline_hash,
            trace_id=trace_id,
        )
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="preview.approved",
        payload={"timeline_id": timeline.id, "version": timeline.version, "actor": actor},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(timeline)
    return timeline_to_read(session, timeline)


def rollback_timeline(
    session: Session,
    *,
    timeline_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
    commit: bool = True,
    record_audit: bool = True,
) -> TimelineRead:
    timeline = _timeline_or_404(session, timeline_id)
    project = project_or_404(session, timeline.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    previous = _current_timeline(session, project)
    _apply_timeline_takes(session, timeline)
    project.current_timeline_version_id = timeline.id
    project.timeline_version = timeline.version
    project.preview_approved = timeline.status == "APPROVED"
    project.export_ready = False
    project.status = "APPROVED" if timeline.status == "APPROVED" else "PREVIEW_READY"
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    if record_audit:
        _audit(
            session,
            project_id=project.id,
            actor=actor,
            action="ROLLBACK_PREVIEW",
            entity_type="timeline",
            entity_id=timeline.id,
            before_hash=previous.baseline_hash,
            after_hash=timeline.baseline_hash,
            trace_id=trace_id,
        )
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="preview.rolled_back",
        payload={"from_timeline_id": previous.id, "to_timeline_id": timeline.id},
    )
    session.flush()
    if commit:
        session.commit()
    session.refresh(timeline)
    return timeline_to_read(session, timeline)
