import json
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, available_ark_image_resolutions
from app.db.models import Asset, Job, Project, ScriptVersion
from app.services.assets import register_file
from app.services.jobs import append_event, enqueue_job
from app.services.projects import canonical_json

PROJECT_THUMBNAIL_KIND = "PROJECT_THUMBNAIL"


def project_thumbnail_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return (720, 1280) if aspect_ratio == "9:16" else (1280, 720)


def thumbnail_generation_resolution(settings: Settings) -> str:
    """Use the smallest model-supported generation tier before exporting 720P."""
    return available_ark_image_resolutions(settings.ark_image_model)[0]


def _script_highlights(script: ScriptVersion) -> str:
    try:
        payload = json.loads(script.payload_json)
    except json.JSONDecodeError:
        return ""
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return ""
    highlights: list[str] = []
    for scene in scenes[:3]:
        if not isinstance(scene, dict):
            continue
        for key in ("heading", "purpose", "emotion"):
            value = scene.get(key)
            if isinstance(value, str) and value.strip():
                highlights.append(value.strip())
    return "；".join(highlights)[:600]


def build_project_thumbnail_prompt(project: Project, script: ScriptVersion) -> str:
    highlights = _script_highlights(script)
    context = "；".join(
        item
        for item in (
            f"项目概念：{project.idea.strip()}" if project.idea.strip() else "",
            f"剧本场景：{highlights}" if highlights else "",
        )
        if item
    )
    return (
        f"为中文短剧《{project.name}》生成一张电影感项目封面。"
        f"题材为{project.genre}，视觉风格为{project.style}。"
        f"{context}。"
        "画面要用一个清晰、有悬念的核心视觉讲述故事，主体在缩略图尺寸下仍可辨认，"
        "构图适配短剧封面；不要出现任何文字、标题、logo、水印、边框或拼贴分镜。"
    )


def enqueue_project_thumbnail(
    session: Session,
    *,
    project: Project,
    script: ScriptVersion,
    trace_id: str,
) -> tuple[Job, bool]:
    width, height = project_thumbnail_dimensions(project.aspect_ratio)
    prompt = build_project_thumbnail_prompt(project, script)
    seed = int.from_bytes(
        sha256(f"project-thumbnail:{project.id}:{script.content_hash}".encode()).digest()[:4],
        "big",
    ) & 0x7FFFFFFF
    return enqueue_job(
        session,
        project_id=project.id,
        job_type="GENERATE_PROJECT_THUMBNAIL",
        entity_type="project",
        entity_id=project.id,
        idempotency_key=(
            f"{project.id}:GENERATE_PROJECT_THUMBNAIL:"
            f"{script.id}:{script.content_hash}:{project.aspect_ratio}"
        ),
        input_payload={
            "project_id": project.id,
            "script_version_id": script.id,
            "script_content_hash": script.content_hash,
            "prompt": prompt,
            "aspect_ratio": project.aspect_ratio,
            "target_width": width,
            "target_height": height,
            "seed": seed,
        },
        label=f"{project.name} · 生成剧本缩略图",
        stage="等待 Seedream 生成 720P 剧本缩略图",
        trace_id=trace_id,
        estimated_seconds=45,
        retryable=True,
    )


def latest_script_is_current(session: Session, script: ScriptVersion) -> bool:
    latest = session.scalar(
        select(ScriptVersion)
        .where(
            ScriptVersion.project_id == script.project_id,
            ScriptVersion.episode_ordinal == script.episode_ordinal,
        )
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    )
    return latest is not None and latest.id == script.id


def render_project_thumbnail(
    content: bytes,
    *,
    width: int,
    height: int,
) -> bytes:
    with Image.open(BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    source_ratio = image.width / image.height
    target_ratio = width / height
    if source_ratio > target_ratio:
        crop_width = round(image.height * target_ratio)
        left = max(0, (image.width - crop_width) // 2)
        image = image.crop((left, 0, left + crop_width, image.height))
    else:
        crop_height = round(image.width / target_ratio)
        top = max(0, (image.height - crop_height) // 2)
        image = image.crop((0, top, image.width, top + crop_height))
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="WEBP", quality=82, method=4)
    return output.getvalue()


def materialize_project_thumbnail(
    session: Session,
    *,
    settings: Settings,
    job: Job,
    project: Project,
    script: ScriptVersion,
    content: bytes,
    model: str,
    request_id: str | None,
    generation_resolution: str,
    seed: int,
) -> Asset:
    width, height = project_thumbnail_dimensions(project.aspect_ratio)
    rendered = render_project_thumbnail(content, width=width, height=height)
    temporary = settings.data_dir / "tmp" / job.id / "project-thumbnail.webp"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(rendered)
    asset = register_file(
        session,
        settings,
        project_id=project.id,
        kind=PROJECT_THUMBNAIL_KIND,
        source=temporary,
        source_entity_type="project",
        source_entity_id=project.id,
        mime="image/webp",
        width=width,
        height=height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    asset.is_temporary = False
    asset.metadata_json = canonical_json(
        {
            "generation": {
                "model": model,
                "request_id": request_id,
                "seed": seed,
                "requested_resolution": generation_resolution,
                "target_width": width,
                "target_height": height,
                "script_version_id": script.id,
                "script_content_hash": script.content_hash,
            },
            "purpose": "project-thumbnail",
        }
    )
    project.thumbnail_asset_id = asset.id
    project.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="project.thumbnail_ready",
        payload={
            "asset_id": asset.id,
            "script_version_id": script.id,
            "width": width,
            "height": height,
        },
    )
    session.flush()
    return asset
