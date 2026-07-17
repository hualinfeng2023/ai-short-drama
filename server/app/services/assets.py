import hashlib
import mimetypes
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Asset


def asset_or_404(session: Session, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": "资产不存在",
                "user_action": "刷新资产列表",
                "retryable": False,
                "details": {"id": asset_id},
            },
        )
    return asset


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_file(
    session: Session,
    settings: Settings,
    *,
    project_id: str,
    kind: str,
    source: Path,
    source_entity_type: str,
    source_entity_id: str,
    mime: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
) -> Asset:
    digest = sha256_file(source)
    existing = session.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.sha256 == digest,
            Asset.kind == kind,
        )
    )
    if existing is not None:
        if source.exists():
            source.unlink()
        return existing

    suffix = source.suffix.lower() or ".bin"
    storage_key = f"assets/{digest[:2]}/{digest}{suffix}"
    destination = settings.data_dir / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        source.unlink()
    else:
        os.replace(source, destination)
    asset = Asset(
        id=str(uuid4()),
        project_id=project_id,
        kind=kind,
        storage_key=storage_key,
        sha256=digest,
        mime=mime or mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
        size_bytes=destination.stat().st_size,
        status="READY",
        provider="mock",
        is_temporary=True,
        width=width,
        height=height,
        duration_ms=duration_ms,
        source_entity_type=source_entity_type,
        source_entity_id=source_entity_id,
        created_at=datetime.now(UTC),
    )
    session.add(asset)
    session.flush()
    return asset


def resolve_asset_path(settings: Settings, asset: Asset) -> Path:
    assets_root = (settings.data_dir / "assets").resolve()
    path = (settings.data_dir / asset.storage_key).resolve()
    if not path.is_relative_to(assets_root) or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ASSET_FILE_MISSING",
                "message": "资产文件不存在",
                "user_action": "重试上游生成任务",
                "retryable": True,
                "details": {"asset_id": asset.id},
            },
        )
    return path
