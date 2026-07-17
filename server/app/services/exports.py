import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    AuditLog,
    ExportRecord,
    Job,
    Project,
    TimelineVersion,
    UsageLedger,
)
from app.schemas import ExportEstimateRead, ExportRead, JobRead
from app.services.assets import register_file
from app.services.events import append_event
from app.services.jobs import enqueue_job, job_to_read
from app.services.projects import version_conflict
from app.services.workspace import project_or_404

EXPORT_POINTS = 10


def _export_or_404(session: Session, export_id: str) -> ExportRecord:
    export = session.get(ExportRecord, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="导出记录不存在")
    return export


def export_to_read(export: ExportRecord) -> ExportRead:
    assets: dict[str, str] = {}
    for name, asset_id in (
        ("mp4", export.mp4_asset_id),
        ("srt", export.srt_asset_id),
        ("vtt", export.vtt_asset_id),
        ("manifest", export.manifest_asset_id),
        ("cover", export.cover_asset_id),
        ("stems_manifest", export.stems_manifest_asset_id),
        ("qc_report", export.qc_report_asset_id),
    ):
        if asset_id:
            assets[name] = f"/api/v1/assets/{asset_id}/content"
    return ExportRead(
        id=export.id,
        project_id=export.project_id,
        timeline_id=export.timeline_id,
        status=export.status,
        profile=export.profile,
        export_profile_id=export.export_profile_id,
        language=export.language,
        rights_status=export.rights_status,
        assets=assets,
        created_at=export.created_at,
        completed_at=export.completed_at,
    )


def _approved_timeline(session: Session, project: Project) -> TimelineVersion:
    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project.current_timeline_version_id
        else None
    )
    if timeline is None or timeline.status != "APPROVED" or not project.preview_approved:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APPROVED_PREVIEW_REQUIRED",
                "message": "导出前必须先批准当前小样",
                "user_action": "返回小样页并批准当前时间线",
                "retryable": False,
                "details": {"status": project.status},
            },
        )
    return timeline


def estimate_export(session: Session, *, project_id: str, profile: str) -> ExportEstimateRead:
    project = project_or_404(session, project_id)
    timeline = _approved_timeline(session, project)
    blockers: list[str] = []
    if project.available_points < EXPORT_POINTS:
        blockers.append("积分不足")
    return ExportEstimateRead(
        timeline_id=timeline.id,
        profile=profile,
        estimated_points=EXPORT_POINTS,
        estimated_seconds=3,
        rights_status="RESTRICTED_DEMO",
        blocked=bool(blockers),
        blockers=blockers,
        outputs=["MP4", "SRT", "VTT", "JSON_MANIFEST"],
    )


def create_export(
    session: Session,
    *,
    project_id: str,
    expected_version: int,
    profile: str,
    rights_confirmed: bool,
    actor: str,
    idempotency_key: str,
    trace_id: str,
) -> tuple[ExportRead, JobRead, bool]:
    job_key = f"export:{project_id}:{idempotency_key}"
    existing_job = session.scalar(select(Job).where(Job.idempotency_key == job_key))
    if existing_job is not None:
        existing_export = _export_or_404(session, existing_job.entity_id)
        return export_to_read(existing_export), job_to_read(existing_job), True
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    timeline = _approved_timeline(session, project)
    if not rights_confirmed:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "RIGHTS_CONFIRMATION_REQUIRED",
                "message": "导出已阻断：必须确认模拟生成或临时素材仅用于演示验证",
                "user_action": "查看权利提示并明确确认",
                "retryable": False,
                "details": {"rights_status": "RESTRICTED_DEMO"},
            },
        )
    if project.available_points < EXPORT_POINTS:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "INSUFFICIENT_POINTS",
                "message": "可用积分不足",
                "user_action": "补充演示积分后重试",
                "retryable": False,
                "details": {"required": EXPORT_POINTS, "available": project.available_points},
            },
        )
    now = datetime.now(UTC)
    export = ExportRecord(
        id=str(uuid4()),
        project_id=project.id,
        timeline_id=timeline.id,
        status="PENDING",
        profile=profile,
        mp4_asset_id=None,
        srt_asset_id=None,
        vtt_asset_id=None,
        manifest_asset_id=None,
        rights_status="RESTRICTED_DEMO",
        created_at=now,
        completed_at=None,
    )
    session.add(export)
    session.flush()
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="EXPORT_PACKAGE",
        entity_type="export",
        entity_id=export.id,
        idempotency_key=job_key,
        input_payload={
            "export_id": export.id,
            "timeline_id": timeline.id,
            "profile": profile,
            "rights_status": export.rights_status,
            "actor": actor,
            "config_version": "export-v1",
        },
        label=f"导出 · 时间线第 {timeline.version} 版",
        stage="等待导出权利预检与打包",
        trace_id=trace_id,
        estimated_seconds=3,
        retryable=True,
        priority=1,
    )
    project.available_points -= EXPORT_POINTS
    project.status = "EXPORTING"
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    session.add(
        UsageLedger(
            id=str(uuid4()),
            project_id=project.id,
            job_id=job.id,
            entry_type="RESERVED",
            points=EXPORT_POINTS,
            description=f"导出 {profile} 预留",
            created_at=now,
        )
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="export.created",
        payload={"export_id": export.id, "timeline_id": timeline.id, "profile": profile},
    )
    session.commit()
    session.refresh(job)
    return export_to_read(export), job_to_read(job), replayed


def materialize_export(session: Session, settings: Settings, job: Job) -> ExportRecord:
    export = _export_or_404(session, job.entity_id)
    if export.status == "READY":
        return export
    export.status = "RUNNING"
    timeline = session.get(TimelineVersion, export.timeline_id)
    if timeline is None or timeline.status != "APPROVED":
        raise RuntimeError("已批准的时间线不存在")
    asset_ids = {
        "mp4": timeline.mp4_asset_id,
        "srt": timeline.srt_asset_id,
        "vtt": timeline.vtt_asset_id,
        "preview_manifest": timeline.manifest_asset_id,
    }
    assets: dict[str, Asset] = {}
    for name, asset_id in asset_ids.items():
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise RuntimeError(f"导出输入资产缺失: {name}")
        assets[name] = asset
    tmp_dir = settings.data_dir / "tmp" / job.id / "export"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(tmp_dir) / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "export-manifest-v1",
                "export_id": export.id,
                "project_id": export.project_id,
                "timeline": {
                    "id": timeline.id,
                    "version": timeline.version,
                    "baseline_hash": timeline.baseline_hash,
                    "approved_at": (
                        timeline.approved_at.isoformat() if timeline.approved_at else None
                    ),
                    "approved_by": timeline.approved_by,
                },
                "profile": export.profile,
                "provider": "mock",
                "is_temporary": True,
                "rights": {
                    "status": export.rights_status,
                    "policy_version": "mvp-demo-rights-v1",
                    "notice": "风险预检不等于法律意见或平台审核保证",
                },
                "assets": {
                    name: {
                        "asset_id": asset.id,
                        "sha256": asset.sha256,
                        "mime": asset.mime,
                        "size_bytes": asset.size_bytes,
                    }
                    for name, asset in assets.items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    manifest = register_file(
        session,
        settings,
        project_id=export.project_id,
        kind="export_manifest",
        source=manifest_path,
        source_entity_type="export",
        source_entity_id=export.id,
        mime="application/json",
        duration_ms=timeline.duration_ms,
    )
    now = datetime.now(UTC)
    export.mp4_asset_id = timeline.mp4_asset_id
    export.srt_asset_id = timeline.srt_asset_id
    export.vtt_asset_id = timeline.vtt_asset_id
    export.manifest_asset_id = manifest.id
    export.status = "READY"
    export.completed_at = now
    project = project_or_404(session, export.project_id)
    project.status = "EXPORTED"
    project.export_ready = True
    project.lock_version += 1
    project.updated_at = now
    committed = session.scalar(
        select(UsageLedger).where(
            UsageLedger.job_id == job.id, UsageLedger.entry_type == "COMMITTED"
        )
    )
    if committed is None:
        session.add(
            UsageLedger(
                id=str(uuid4()),
                project_id=project.id,
                job_id=job.id,
                entry_type="COMMITTED",
                points=EXPORT_POINTS,
                description=f"导出 {export.profile} 已完成",
                created_at=now,
            )
        )
    session.add(
        AuditLog(
            id=str(uuid4()),
            project_id=project.id,
            actor=str(json.loads(job.input_json).get("actor", "demo-user")),
            action="EXPORT_PACKAGE",
            entity_type="export",
            entity_id=export.id,
            before_hash=timeline.baseline_hash,
            after_hash=manifest.sha256,
            trace_id=job.trace_id,
            created_at=now,
        )
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="export.ready",
        payload={"export_id": export.id, "manifest_asset_id": manifest.id},
    )
    session.commit()
    session.refresh(export)
    return export


def get_export(session: Session, export_id: str) -> ExportRead:
    return export_to_read(_export_or_404(session, export_id))


def list_exports(session: Session, project_id: str) -> list[ExportRead]:
    project_or_404(session, project_id)
    exports = session.scalars(
        select(ExportRecord)
        .where(ExportRecord.project_id == project_id)
        .order_by(ExportRecord.created_at.desc())
    ).all()
    return [export_to_read(item) for item in exports]
