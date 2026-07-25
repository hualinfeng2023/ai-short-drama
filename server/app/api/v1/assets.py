import json
from collections.abc import Iterator
from urllib.parse import unquote
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import get_settings
from app.db.models import Asset, IdempotencyKey, Project
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import AssetRead
from app.services.assets import asset_or_404, resolve_asset_path
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash
from app.services.uploads import ensure_project_capacity, upload_rule, validate_and_register_upload
from app.services.workspace import project_or_404

router = APIRouter(prefix="/api/v1", tags=["assets"])


def asset_to_read(asset: Asset) -> AssetRead:
    return AssetRead(
        id=asset.id,
        project_id=asset.project_id,
        kind=asset.kind,
        sha256=asset.sha256,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        status=asset.status,
        provider=asset.provider,
        is_temporary=asset.is_temporary,
        width=asset.width,
        height=asset.height,
        duration_ms=asset.duration_ms,
        original_filename=asset.original_filename,
        metadata=json.loads(asset.metadata_json or "{}"),
        rights_status=asset.rights_status,
        source_entity_type=asset.source_entity_type,
        source_entity_id=asset.source_entity_id,
        created_at=asset.created_at,
        content_url=f"/api/v1/assets/{asset.id}/content",
    )


@router.get("/projects/{project_id}/assets")
def project_assets(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    project_or_404(session, project_id)
    assets = session.scalars(
        select(Asset)
        .where(Asset.project_id == project_id, Asset.kind.like("REFERENCE_%"))
        .order_by(Asset.created_at.desc())
    ).all()
    return success([asset_to_read(item) for item in assets])


@router.post("/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
async def upload_project_asset(
    project_id: str,
    request: Request,
    filename_encoded: str = Header(alias="X-Filename", min_length=1, max_length=768),
    rights_confirmed: bool = Header(default=False, alias="X-Rights-Confirmed"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not rights_confirmed:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "RIGHTS_REQUIRED",
                "message": "上传前必须确认对素材拥有使用权",
                "retryable": False,
            },
        )
    filename = unquote(filename_encoded)
    extension, limit, _mime, _kind = upload_rule(filename)
    content_length = request.headers.get("content-length")
    try:
        declared_size = int(content_length) if content_length else 0
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Content-Length 无效") from exc
    if declared_size > limit:
        raise HTTPException(
            status_code=413,
            detail={"code": "UPLOAD_TOO_LARGE", "message": "素材超过该类型上传上限"},
        )
    if declared_size:
        ensure_project_capacity(session, project_id, declared_size)
    settings = get_settings()
    upload_dir = settings.data_dir / "tmp" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    temporary = upload_dir / f"{uuid4()}{extension}"
    size = 0
    try:
        with temporary.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "UPLOAD_TOO_LARGE",
                            "message": "素材超过该类型上传上限",
                        },
                    )
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="上传文件为空")
        asset = validate_and_register_upload(
            session,
            settings,
            project_id=project_id,
            source=temporary,
            filename=filename,
            declared_content_type=request.headers.get("content-type"),
        )
        return success(asset_to_read(asset))
    finally:
        temporary.unlink(missing_ok=True)


def _reference_asset_delete_project_id(
    session: Session,
    *,
    asset_id: str,
    idempotency_key: str | None,
) -> str:
    asset = session.get(Asset, asset_id)
    if asset is not None:
        return asset.project_id
    if idempotency_key is not None:
        records = session.scalars(
            select(IdempotencyKey).where(
                IdempotencyKey.key == idempotency_key,
                IdempotencyKey.resource_id == asset_id,
                IdempotencyKey.scope.like("domain-command:%"),
            )
        ).all()
        for record in records:
            stored = json.loads(record.response_json)
            if stored.get("command_type") == "DELETE_REFERENCE_ASSET":
                return record.scope.removeprefix("domain-command:")
    asset_or_404(session, asset_id)
    raise AssertionError("asset_or_404 must raise for a missing asset")


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference_asset(
    asset_id: str,
    response: Response,
    actor: str = Header(default="创作者", alias="X-Actor", min_length=1, max_length=80),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> None:
    project_id = _reference_asset_delete_project_id(
        session,
        asset_id=asset_id,
        idempotency_key=idempotency_key,
    )
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{project_id}:domain-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="DELETE_REFERENCE_ASSET",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=asset_id,
            target_version_id=asset_id,
            expected_version=ExpectedVersion(
                project_lock_version=project.lock_version,
                target_version_id=asset_id,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key or f"reference-asset-delete-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"reference-asset-delete:{asset_id}",
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()


@router.get("/assets/{asset_id}")
def asset(asset_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(asset_to_read(asset_or_404(session, asset_id)))


def _file_range(path, start: int, end: int) -> Iterator[bytes]:  # noqa: ANN001
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/assets/{asset_id}/content")
def asset_content(
    asset_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    session: Session = Depends(get_session),
):  # noqa: ANN201
    model = asset_or_404(session, asset_id)
    path = resolve_asset_path(get_settings(), model)
    if not range_header:
        return FileResponse(
            path,
            media_type=model.mime,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=31536000"},
        )
    if not range_header.startswith("bytes=") or "," in range_header:
        raise HTTPException(status_code=416, detail="仅支持单一 bytes Range")
    try:
        raw_start, raw_end = range_header.removeprefix("bytes=").split("-", 1)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Range 格式无效") from exc
    size = path.stat().st_size
    try:
        if raw_start:
            start = int(raw_start)
            end = min(int(raw_end) if raw_end else size - 1, size - 1)
        else:
            suffix_length = int(raw_end)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, size - suffix_length)
            end = size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Range 格式无效") from exc
    if start < 0 or start >= size or end < start:
        raise HTTPException(status_code=416, detail="Range 超出文件边界")
    return StreamingResponse(
        _file_range(path, start, end),
        status_code=206,
        media_type=model.mime,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Cache-Control": "private, max-age=31536000",
        },
    )
