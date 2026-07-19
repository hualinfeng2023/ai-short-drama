import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import available_ark_image_models, get_settings
from app.db.models import WorkerState
from app.db.session import get_engine
from app.services.media_staging import media_staging_configured, media_staging_enabled

router = APIRouter(tags=["health"])
EXPECTED_REVISION = "0025_script_excerpt_revisions"


@router.get("/health/live")
def live() -> dict[str, object]:
    return success({"status": "ok", "service": "api"})


@router.get("/health/ready")
def ready(response: Response) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, object] = {}
    is_ready = True

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in (
            settings.data_dir,
            settings.data_dir / "tmp",
            settings.data_dir / "assets",
        ):
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_probe"
            probe.touch()
            probe.unlink()
        checks["data_dir"] = {"status": "ok", "writable_roots": ["data", "tmp", "assets"]}
    except OSError as exc:
        is_ready = False
        checks["data_dir"] = {"status": "error", "message": str(exc)}

    try:
        with get_engine(settings.database_url).connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar_one()
        migration_ok = revision == EXPECTED_REVISION
        is_ready = is_ready and migration_ok
        checks["database"] = {"status": "ok" if migration_ok else "error"}
        checks["migration"] = {
            "status": "ok" if migration_ok else "error",
            "current": revision,
            "expected": EXPECTED_REVISION,
        }
    except Exception as exc:  # readiness must report dependency failures, not crash
        is_ready = False
        checks["database"] = {"status": "error", "message": str(exc)}
        checks["migration"] = {"status": "error", "expected": EXPECTED_REVISION}

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    media_tools_ok = ffmpeg is not None and ffprobe is not None
    is_ready = is_ready and media_tools_ok
    checks["media_tools"] = {
        "status": "ok" if media_tools_ok else "error",
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
    }

    if media_staging_enabled(settings):
        staging_ok = media_staging_configured(settings)
        is_ready = is_ready and staging_ok
        checks["media_staging"] = {
            "status": "ok" if staging_ok else "error",
            "provider": "volcengine-tos",
            "bucket_configured": bool(settings.tos_bucket),
            "signed_url_ttl_seconds": settings.tos_presign_ttl_seconds,
            "reason": None if staging_ok else "incomplete_tos_configuration",
        }
    else:
        checks["media_staging"] = {
            "status": "disabled",
            "provider": "volcengine-tos",
        }

    configured_font = os.getenv("CJK_FONT_PATH")
    font_candidates = [
        Path(configured_font) if configured_font else None,
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    ]
    font = next((path for path in font_candidates if path is not None and path.is_file()), None)
    font_ok = font is not None
    is_ready = is_ready and font_ok
    checks["cjk_font"] = {
        "status": "ok" if font_ok else "error",
        "configured": bool(configured_font),
    }

    if settings.job_worker_enabled:
        try:
            with Session(get_engine(settings.database_url)) as session:
                worker = session.scalar(
                    select(WorkerState).order_by(WorkerState.heartbeat_at.desc())
                )
            if worker is None:
                worker_ok = False
                checks["job_worker"] = {"status": "error", "reason": "missing_heartbeat"}
            else:
                heartbeat = worker.heartbeat_at
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                age_seconds = (datetime.now(UTC) - heartbeat).total_seconds()
                worker_ok = (
                    worker.status in {"IDLE", "RUNNING"}
                    and age_seconds <= settings.worker_heartbeat_stale_seconds
                )
                checks["job_worker"] = {
                    "status": "ok" if worker_ok else "error",
                    "worker_status": worker.status,
                    "heartbeat_age_seconds": round(age_seconds, 3),
                }
            is_ready = is_ready and worker_ok
        except Exception as exc:  # readiness reports worker-state failures
            is_ready = False
            checks["job_worker"] = {"status": "error", "message": str(exc)}
    else:
        checks["job_worker"] = {"status": "disabled"}
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return success(
        {
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
        }
    )


@router.get("/meta/config")
def meta_config() -> dict[str, object]:
    settings = get_settings()
    return success(
        {
            "app_name": settings.app_name,
            "environment": settings.environment,
            "api_version": "v1",
            "capabilities": {
                "read_only_api": False,
                "project_writes": True,
                "brief_versioning": True,
                "job_worker": settings.job_worker_enabled,
                "job_recovery": True,
                "job_events_sse": True,
                "mock_provider": True,
                "media_pipeline": True,
                "reference_uploads": True,
                "upload_ssrf_surface": False,
                "provider_calls": bool(settings.ark_api_key),
                "image_provider": "volcengine-ark" if settings.ark_api_key else "mock",
                "image_model": (
                    settings.ark_image_model if settings.ark_api_key else "deterministic-image-v1"
                ),
                "image_models": (
                    available_ark_image_models(settings)
                    if settings.ark_api_key
                    else [{"id": "deterministic-image-v1", "label": "确定性 Mock"}]
                ),
                "optional_image_provider": "volcengine-ark",
                "video_provider": "volcengine-ark",
                "video_model": settings.ark_video_model,
                "seedream_source_url_fast_path_seconds": (
                    settings.seedream_source_url_fast_path_seconds
                ),
                "media_staging_provider": "volcengine-tos",
                "media_staging_configured": media_staging_configured(settings),
                "feature_flags": settings.feature_flags,
            },
        }
    )
