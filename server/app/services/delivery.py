import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    ExportArtifact,
    ExportProfile,
    ExportRecord,
    GenerationRecord,
    Job,
    Project,
    ReviewGate,
    RightsPreflight,
    SoundBriefVersion,
    TimelineVersion,
    WholeFilmQualityCheck,
)
from app.schemas import ExportMatrixRequest, ExportProfileCreate
from app.services.assets import register_file, resolve_asset_path
from app.services.events import append_event
from app.services.jobs import enqueue_job
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def _profile_read(profile: ExportProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "project_id": profile.project_id,
        "name": profile.name,
        "version": profile.version,
        "platform": profile.platform,
        "aspect_ratio": profile.aspect_ratio,
        "width": profile.width,
        "height": profile.height,
        "caption_mode": profile.caption_mode,
        "languages": json.loads(profile.languages_json),
        "audio_tracks": json.loads(profile.audio_tracks_json),
        "watermark": json.loads(profile.watermark_json),
        "content_hash": profile.content_hash,
        "status": profile.status,
        "created_at": profile.created_at,
    }


def create_export_profile(
    session: Session,
    *,
    project_id: str,
    payload: ExportProfileCreate,
) -> dict[str, object]:
    project = project_or_404(session, project_id)
    if project.lock_version != payload.expected_version:
        raise version_conflict(project, payload.expected_version)
    existing = session.scalar(
        select(ExportProfile)
        .where(ExportProfile.project_id == project_id, ExportProfile.name == payload.name)
        .order_by(ExportProfile.version.desc())
    )
    version = (existing.version if existing else 0) + 1
    profile_payload = {
        "name": payload.name,
        "platform": payload.platform,
        "aspect_ratio": payload.aspect_ratio,
        "width": payload.width,
        "height": payload.height,
        "caption_mode": payload.caption_mode,
        "languages": payload.languages,
        "audio_tracks": payload.audio_tracks,
        "watermark": payload.watermark,
    }
    profile = ExportProfile(
        id=str(uuid4()),
        project_id=project_id,
        name=payload.name,
        version=version,
        platform=payload.platform,
        aspect_ratio=payload.aspect_ratio,
        width=payload.width,
        height=payload.height,
        caption_mode=payload.caption_mode,
        languages_json=canonical_json(payload.languages),
        audio_tracks_json=canonical_json(payload.audio_tracks),
        watermark_json=canonical_json(payload.watermark),
        content_hash=content_hash(profile_payload),
        status="ACTIVE",
        created_at=datetime.now(UTC),
    )
    session.add(profile)
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    session.commit()
    return _profile_read(profile)


def list_export_profiles(session: Session, project_id: str) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    profiles = session.scalars(
        select(ExportProfile)
        .where(ExportProfile.project_id == project_id)
        .order_by(ExportProfile.created_at)
    ).all()
    return [_profile_read(item) for item in profiles]


def _preflight(
    session: Session,
    *,
    project: Project,
    timeline: TimelineVersion,
    profile: ExportProfile,
    language: str,
) -> RightsPreflight:
    blockers: list[str] = []
    checks: list[dict[str, object]] = []
    checks.append({"check": "G5_APPROVED", "passed": timeline.status == "APPROVED"})
    if timeline.status != "APPROVED" or not project.preview_approved:
        blockers.append("G5 Picture Lock 未批准")
    gate = session.scalar(
        select(ReviewGate).where(
            ReviewGate.project_id == project.id,
            ReviewGate.gate_key == "G5",
            ReviewGate.entity_id == timeline.id,
            ReviewGate.status == "APPROVED",
        )
    )
    checks.append({"check": "G5_GATE_RECORD", "passed": gate is not None})
    if gate is None:
        blockers.append("G5 审核记录缺失")
    failed_qc = list(
        session.scalars(
            select(WholeFilmQualityCheck).where(
                WholeFilmQualityCheck.timeline_id == timeline.id,
                WholeFilmQualityCheck.status == "FAILED",
            )
        ).all()
    )
    checks.append({"check": "WHOLE_FILM_QC", "passed": not failed_qc})
    if failed_qc:
        blockers.append("整片 QC 存在失败项")
    sound_brief = session.scalar(
        select(SoundBriefVersion)
        .where(SoundBriefVersion.project_id == project.id)
        .order_by(SoundBriefVersion.version.desc())
    )
    rights_ok = sound_brief is not None and sound_brief.rights_status == "SYNTHETIC_OWNED"
    checks.append({"check": "AUDIO_RIGHTS", "passed": rights_ok})
    if not rights_ok:
        blockers.append("音频权利状态不可正式交付")
    languages = json.loads(profile.languages_json)
    checks.append({"check": "LANGUAGE_DECLARED", "passed": language in languages})
    if language not in languages:
        blockers.append(f"导出规格未声明语言 {language}")
    preflight = RightsPreflight(
        id=str(uuid4()),
        project_id=project.id,
        timeline_id=timeline.id,
        export_profile_id=profile.id,
        language=language,
        status="PASSED" if not blockers else "BLOCKED",
        blockers_json=canonical_json(blockers),
        checks_json=canonical_json(checks),
        policy_version="delivery-rights-v2",
        created_at=datetime.now(UTC),
    )
    session.add(preflight)
    session.flush()
    return preflight


def create_export_matrix(
    session: Session,
    *,
    project_id: str,
    payload: ExportMatrixRequest,
    trace_id: str,
) -> list[dict[str, object]]:
    project = project_or_404(session, project_id)
    if project.lock_version != payload.expected_version:
        raise version_conflict(project, payload.expected_version)
    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project.current_timeline_version_id
        else None
    )
    if timeline is None:
        raise HTTPException(status_code=409, detail="导出前必须存在当前时间线")
    profiles = list(
        session.scalars(
            select(ExportProfile).where(
                ExportProfile.project_id == project_id,
                ExportProfile.id.in_(payload.profile_ids),
                ExportProfile.status == "ACTIVE",
            )
        ).all()
    )
    if len(profiles) != len(set(payload.profile_ids)):
        raise HTTPException(status_code=404, detail="一个或多个导出规格不存在")
    now = datetime.now(UTC)
    results: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for profile in profiles:
        for language in payload.languages:
            preflight = _preflight(
                session,
                project=project,
                timeline=timeline,
                profile=profile,
                language=language,
            )
            if preflight.status != "PASSED":
                blocked.append(
                    {
                        "profile_id": profile.id,
                        "language": language,
                        "blockers": json.loads(preflight.blockers_json),
                    }
                )
                continue
            existing = session.scalar(
                select(ExportRecord).where(
                    ExportRecord.timeline_id == timeline.id,
                    ExportRecord.export_profile_id == profile.id,
                    ExportRecord.language == language,
                )
            )
            if existing is not None:
                job = session.scalar(
                    select(Job).where(Job.entity_type == "export", Job.entity_id == existing.id)
                )
                results.append(
                    {
                        "export_id": existing.id,
                        "job_id": job.id if job else None,
                        "profile_id": profile.id,
                        "language": language,
                        "replayed": True,
                    }
                )
                continue
            export = ExportRecord(
                id=str(uuid4()),
                project_id=project.id,
                timeline_id=timeline.id,
                status="PENDING",
                profile=profile.name,
                export_profile_id=profile.id,
                language=language,
                rights_preflight_id=preflight.id,
                picture_master_asset_id=timeline.mp4_asset_id,
                mp4_asset_id=None,
                srt_asset_id=None,
                vtt_asset_id=None,
                manifest_asset_id=None,
                cover_asset_id=None,
                stems_manifest_asset_id=None,
                qc_report_asset_id=None,
                rights_status="SYNTHETIC_OWNED",
                created_at=now,
                completed_at=None,
            )
            session.add(export)
            session.flush()
            job, replayed = enqueue_job(
                session,
                project_id=project.id,
                job_type="EXPORT_PACKAGE_V2",
                entity_type="export",
                entity_id=export.id,
                idempotency_key=(
                    f"{project.id}:EXPORT_PACKAGE_V2:{timeline.id}:{profile.id}:{language}"
                ),
                input_payload={
                    "export_id": export.id,
                    "timeline_id": timeline.id,
                    "export_profile_id": profile.id,
                    "language": language,
                    "rights_preflight_id": preflight.id,
                    "actor": payload.actor,
                    "manifest_schema": "delivery-manifest-v2",
                },
                label=f"{profile.name} · {language}",
                stage="等待多平台多语言交付打包",
                trace_id=trace_id,
                estimated_seconds=8,
                retryable=True,
                priority=1,
            )
            results.append(
                {
                    "export_id": export.id,
                    "job_id": job.id,
                    "profile_id": profile.id,
                    "language": language,
                    "replayed": replayed,
                }
            )
    if blocked:
        session.rollback()
        raise HTTPException(
            status_code=423,
            detail={
                "code": "RIGHTS_PREFLIGHT_BLOCKED",
                "message": "一个或多个交付组合未通过权利与批准预检",
                "retryable": False,
                "details": blocked,
            },
        )
    project.status = "EXPORTING"
    project.export_ready = False
    project.lock_version += 1
    project.updated_at = now
    session.commit()
    return results


def _localized_subtitle(source: Path, output: Path, language: str) -> None:
    lines = source.read_text(encoding="utf-8").splitlines()
    localized: list[str] = []
    for line in lines:
        if line and "-->" not in line and not line.isdigit() and line != "WEBVTT":
            localized.append(f"[{language}] {line}")
        else:
            localized.append(line)
    output.write_text("\n".join(localized), encoding="utf-8")


def _artifact(
    session: Session,
    *,
    export: ExportRecord,
    artifact_type: str,
    asset_id: str,
    language: str,
    reused_from_asset_id: str | None = None,
) -> None:
    session.add(
        ExportArtifact(
            id=str(uuid4()),
            export_id=export.id,
            artifact_type=artifact_type,
            language=language,
            asset_id=asset_id,
            reused_from_asset_id=reused_from_asset_id,
            metadata_json=canonical_json({"delivery_manifest": "v2"}),
            created_at=datetime.now(UTC),
        )
    )


def materialize_export_v2(
    session: Session,
    settings: Settings,
    job: Job,
) -> ExportRecord:
    export = session.get(ExportRecord, job.entity_id)
    if export is None:
        raise ValueError("导出记录不存在")
    if export.status == "READY":
        return export
    timeline = session.get(TimelineVersion, export.timeline_id)
    profile = (
        session.get(ExportProfile, export.export_profile_id) if export.export_profile_id else None
    )
    preflight = (
        session.get(RightsPreflight, export.rights_preflight_id)
        if export.rights_preflight_id
        else None
    )
    if timeline is None or profile is None or preflight is None or preflight.status != "PASSED":
        raise ValueError("交付输入或权利预检不完整")
    if timeline.status != "APPROVED":
        raise ValueError("只有第 5 阶段已批准的时间线可以导出")
    export.status = "RUNNING"
    source_assets: dict[str, Asset] = {}
    for name, asset_id in (
        ("picture_master", timeline.mp4_asset_id),
        ("srt", timeline.srt_asset_id),
        ("vtt", timeline.vtt_asset_id),
        ("timeline_manifest", timeline.manifest_asset_id),
        ("stems_manifest", timeline.stems_manifest_asset_id),
        ("qc_report", timeline.qc_report_asset_id),
    ):
        if asset_id is None:
            raise ValueError(f"交付输入缺失: {name}")
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise ValueError(f"交付资产不存在: {name}")
        source_assets[name] = asset
    tmp_dir = settings.data_dir / "tmp" / job.id / "delivery-v2"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    localized_srt = tmp_dir / f"subtitle-{export.language}.srt"
    localized_vtt = tmp_dir / f"subtitle-{export.language}.vtt"
    _localized_subtitle(
        resolve_asset_path(settings, source_assets["srt"]),
        localized_srt,
        export.language,
    )
    _localized_subtitle(
        resolve_asset_path(settings, source_assets["vtt"]),
        localized_vtt,
        export.language,
    )
    srt_asset = register_file(
        session,
        settings,
        project_id=export.project_id,
        kind=f"EXPORT_SUBTITLE_SRT_{export.language}",
        source=localized_srt,
        source_entity_type="export",
        source_entity_id=export.id,
        mime="application/x-subrip",
        duration_ms=timeline.duration_ms,
    )
    vtt_asset = register_file(
        session,
        settings,
        project_id=export.project_id,
        kind=f"EXPORT_SUBTITLE_VTT_{export.language}",
        source=localized_vtt,
        source_entity_type="export",
        source_entity_id=export.id,
        mime="text/vtt",
        duration_ms=timeline.duration_ms,
    )
    srt_asset.is_temporary = False
    vtt_asset.is_temporary = False
    cover_path = tmp_dir / "cover.jpg"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-i",
            str(resolve_asset_path(settings, source_assets["picture_master"])),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(cover_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-2000:] or "封面提取失败")
    cover_asset = register_file(
        session,
        settings,
        project_id=export.project_id,
        kind=f"EXPORT_COVER_{profile.platform}",
        source=cover_path,
        source_entity_type="export",
        source_entity_id=export.id,
        mime="image/jpeg",
        width=profile.width,
        height=profile.height,
    )
    cover_asset.is_temporary = False
    records = list(
        session.scalars(
            select(GenerationRecord).where(GenerationRecord.project_id == export.project_id)
        ).all()
    )
    manifest_payload = {
        "schema_version": "delivery-manifest-v2",
        "export_id": export.id,
        "project_id": export.project_id,
        "timeline": {
            "id": timeline.id,
            "version": timeline.version,
            "baseline_hash": timeline.baseline_hash,
            "approved_at": timeline.approved_at.isoformat() if timeline.approved_at else None,
            "approved_by": timeline.approved_by,
        },
        "profile": _profile_read(profile),
        "language": export.language,
        "picture_master": {
            "asset_id": timeline.mp4_asset_id,
            "sha256": source_assets["picture_master"].sha256,
            "reused_across_languages": True,
        },
        "localized_assets": {"srt": srt_asset.id, "vtt": vtt_asset.id},
        "cover_asset_id": cover_asset.id,
        "stems_manifest_asset_id": timeline.stems_manifest_asset_id,
        "qc_report_asset_id": timeline.qc_report_asset_id,
        "rights_preflight": {
            "id": preflight.id,
            "status": preflight.status,
            "policy_version": preflight.policy_version,
            "checks": json.loads(preflight.checks_json),
        },
        "generation_records": [
            {
                "id": record.id,
                "capability": record.capability,
                "provider": record.provider,
                "model": record.model,
                "prompt_hash": record.prompt_hash,
                "output_asset_id": record.output_asset_id,
                "status": record.status,
            }
            for record in records
        ],
        "rights_status": "SYNTHETIC_OWNED",
        "auto_publish": False,
    }
    manifest_path = tmp_dir / "delivery-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    manifest_asset = register_file(
        session,
        settings,
        project_id=export.project_id,
        kind="EXPORT_MANIFEST_V2",
        source=manifest_path,
        source_entity_type="export",
        source_entity_id=export.id,
        mime="application/json",
    )
    manifest_asset.is_temporary = False
    export.picture_master_asset_id = timeline.mp4_asset_id
    export.mp4_asset_id = timeline.mp4_asset_id
    export.srt_asset_id = srt_asset.id
    export.vtt_asset_id = vtt_asset.id
    export.manifest_asset_id = manifest_asset.id
    export.cover_asset_id = cover_asset.id
    export.stems_manifest_asset_id = timeline.stems_manifest_asset_id
    export.qc_report_asset_id = timeline.qc_report_asset_id
    export.status = "READY"
    export.completed_at = datetime.now(UTC)
    for artifact_type, asset_id, language, reused in (
        ("PICTURE_MASTER", timeline.mp4_asset_id, "und", timeline.mp4_asset_id),
        ("SUBTITLE_SRT", srt_asset.id, export.language, None),
        ("SUBTITLE_VTT", vtt_asset.id, export.language, None),
        ("COVER", cover_asset.id, "und", None),
        ("AUDIO_STEMS_MANIFEST", timeline.stems_manifest_asset_id, "und", None),
        ("QC_REPORT", timeline.qc_report_asset_id, "und", None),
        ("PROVENANCE_MANIFEST", manifest_asset.id, export.language, None),
    ):
        if asset_id:
            _artifact(
                session,
                export=export,
                artifact_type=artifact_type,
                asset_id=asset_id,
                language=language,
                reused_from_asset_id=reused,
            )
    pending = session.scalar(
        select(ExportRecord.id).where(
            ExportRecord.project_id == export.project_id,
            ExportRecord.timeline_id == timeline.id,
            ExportRecord.id != export.id,
            ExportRecord.status != "READY",
        )
    )
    if pending is None:
        project = project_or_404(session, export.project_id)
        project.status = "EXPORTED"
        project.export_ready = True
        project.lock_version += 1
        project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=export.project_id,
        job_id=job.id,
        event_type="delivery.export_ready",
        payload={
            "export_id": export.id,
            "profile_id": profile.id,
            "language": export.language,
            "picture_master_reused": True,
        },
    )
    session.flush()
    return export
