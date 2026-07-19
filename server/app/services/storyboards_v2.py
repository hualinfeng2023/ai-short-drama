import base64
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    Episode,
    Job,
    JobDependency,
    LocationVersion,
    Project,
    PropVersion,
    ReviewGate,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    VisualBibleVersion,
    WorkflowNode,
    WorkflowRun,
)
from app.schemas import JobRead
from app.services.assets import register_file, resolve_asset_path
from app.services.events import append_event
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.media import PreviewFiles, PreviewShot
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404


def _split_scene_seconds(total_seconds: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    weight_total = max(1, sum(weights))
    raw = [total_seconds * weight / weight_total for weight in weights]
    values = [max(1, int(item)) for item in raw]
    delta = total_seconds - sum(values)
    if delta > 0:
        order = sorted(
            range(len(raw)),
            key=lambda index: raw[index] - int(raw[index]),
            reverse=True,
        )
        for offset in range(delta):
            values[order[offset % len(order)]] += 1
    elif delta < 0:
        order = sorted(range(len(values)), key=lambda index: values[index], reverse=True)
        for offset in range(-delta):
            index = order[offset % len(order)]
            if values[index] > 1:
                values[index] -= 1
    return values


def _mentioned_character_keys(
    text: str,
    characters_by_key: dict[str, Character],
) -> list[str]:
    mentioned: list[str] = []
    for character_key, character in characters_by_key.items():
        if character.name in text or character_key in text:
            mentioned.append(character_key)
    return mentioned


def _scene_character_keys(
    lines: list[ScriptLine],
    characters_by_key: dict[str, Character],
) -> list[str]:
    result: list[str] = []
    for line in lines:
        candidates = [line.speaker_key, *_mentioned_character_keys(line.text, characters_by_key)]
        for character_key in candidates:
            if character_key in characters_by_key and character_key not in result:
                result.append(character_key)
    return result[:8]


def _line_character_keys(
    line: ScriptLine,
    *,
    characters_by_key: dict[str, Character],
    scene_character_keys: list[str],
) -> list[str]:
    result: list[str] = []
    candidates = [line.speaker_key, *_mentioned_character_keys(line.text, characters_by_key)]
    for character_key in candidates:
        if character_key in characters_by_key and character_key not in result:
            result.append(character_key)
    if not result and line.line_type in {"ACTION", "VOICE_OVER"}:
        result.extend(scene_character_keys)
    return result[:8]


def _create_workflow(
    session: Session,
    *,
    job: Job,
    visual_bible: VisualBibleVersion,
) -> WorkflowRun:
    existing = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.project_id == job.project_id,
            WorkflowRun.source_entity_id == visual_bible.id,
            WorkflowRun.workflow_type == "EPISODE_PRODUCTION_V2",
        )
    )
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    run = WorkflowRun(
        id=str(uuid4()),
        project_id=job.project_id,
        workflow_type="EPISODE_PRODUCTION_V2",
        source_entity_type="visual_bible_version",
        source_entity_id=visual_bible.id,
        status="RUNNING",
        current_gate=None,
        config_version="workflow-v1",
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    session.add(run)
    session.flush()
    session.add(
        WorkflowNode(
            id=str(uuid4()),
            workflow_run_id=run.id,
            node_key="storyboard.plan",
            node_type="FAN_OUT",
            entity_type="visual_bible_version",
            entity_id=visual_bible.id,
            job_id=job.id,
            status="RUNNING",
            dependency_keys_json="[]",
            output_json="{}",
            degraded=False,
            error_code=None,
            created_at=now,
            updated_at=now,
        )
    )
    return run


def create_dynamic_storyboard(session: Session, job: Job) -> tuple[StoryboardVersion, list[str]]:
    input_payload = json.loads(job.input_json)
    visual_bible = session.get(
        VisualBibleVersion,
        str(input_payload["visual_bible_version_id"]),
    )
    if visual_bible is None or visual_bible.status != "APPROVED":
        raise ValueError("批准 Visual Bible 不存在")
    script = session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == job.project_id, ScriptVersion.status == "APPROVED")
        .order_by(ScriptVersion.version.desc())
    )
    if script is None:
        raise ValueError("已批准剧本不存在")
    existing = session.scalar(
        select(StoryboardVersion).where(
            StoryboardVersion.project_id == job.project_id,
            StoryboardVersion.script_version_id == script.id,
            StoryboardVersion.visual_bible_version_id == visual_bible.id,
        )
    )
    if existing is not None:
        child_ids = list(
            session.scalars(
                select(Job.id).where(
                    Job.project_id == job.project_id,
                    Job.job_type == "GENERATE_STORYBOARD_TAKE",
                    Job.input_json.contains(existing.id),
                )
            ).all()
        )
        return existing, child_ids
    project = project_or_404(session, job.project_id)
    workflow = _create_workflow(session, job=job, visual_bible=visual_bible)
    now = datetime.now(UTC)
    episode = Episode(
        id=str(uuid4()),
        project_id=project.id,
        code=f"S01E{script.episode_ordinal:02d}",
        ordinal=script.episode_ordinal,
        title=str(json.loads(script.payload_json)["title"]),
        target_duration_sec=round(script.estimated_duration_ms / 1000),
        status="STORYBOARDING",
    )
    session.add(episode)
    session.flush()
    storyboard_version = (
        session.scalar(
            select(func.max(StoryboardVersion.version)).where(
                StoryboardVersion.project_id == project.id,
                StoryboardVersion.episode_ordinal == script.episode_ordinal,
            )
        )
        or 0
    ) + 1
    storyboard = StoryboardVersion(
        id=str(uuid4()),
        project_id=project.id,
        script_version_id=script.id,
        visual_bible_version_id=visual_bible.id,
        workflow_run_id=workflow.id,
        episode_id=episode.id,
        episode_ordinal=script.episode_ordinal,
        version=storyboard_version,
        status="GENERATING",
        payload_json="{}",
        content_hash="",
        parent_version_id=None,
        animatic_asset_id=None,
        approved_at=None,
        approved_by=None,
        created_at=now,
    )
    session.add(storyboard)
    session.flush()

    characters = list(
        session.scalars(select(Character).where(Character.project_id == project.id)).all()
    )
    characters_by_key = {item.character_key: item for item in characters}
    locations = list(
        session.scalars(
            select(LocationVersion).where(
                LocationVersion.project_id == project.id,
                LocationVersion.status == "APPROVED",
            )
        ).all()
    )
    location_by_name = {item.name: item for item in locations}
    props = list(
        session.scalars(
            select(PropVersion).where(
                PropVersion.project_id == project.id,
                PropVersion.status == "APPROVED",
            )
        ).all()
    )
    script_scenes = list(
        session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script.id)
            .order_by(ScriptScene.ordinal)
        ).all()
    )
    child_job_ids: list[str] = []
    shot_payloads: list[dict[str, object]] = []
    shot_ordinal = 1
    for script_scene in script_scenes:
        lines = list(
            session.scalars(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == script_scene.id)
                .order_by(ScriptLine.ordinal)
            ).all()
        )
        durations = _split_scene_seconds(
            round(script_scene.duration_ms / 1000),
            [item.estimated_duration_ms + item.pause_after_ms for item in lines],
        )
        scene_character_keys = _scene_character_keys(lines, characters_by_key)
        scene = Scene(
            id=str(uuid4()),
            episode_id=episode.id,
            code=f"{script_scene.ordinal:02d}",
            ordinal=script_scene.ordinal,
            title=script_scene.heading,
            purpose=script_scene.purpose,
            duration_sec=sum(durations),
            status="STORYBOARDING",
        )
        session.add(scene)
        session.flush()
        location = location_by_name.get(script_scene.location) or (
            locations[0] if locations else None
        )
        for line, duration_sec in zip(lines, durations, strict=True):
            code = f"S{shot_ordinal:02d}"
            line_character_keys = _line_character_keys(
                line,
                characters_by_key=characters_by_key,
                scene_character_keys=scene_character_keys,
            )
            bound_characters = [characters_by_key[key] for key in line_character_keys]
            character_ids = [character.id for character in bound_characters]
            identity_ids = [
                character.locked_identity_version_id
                for character in bound_characters
                if character.locked_identity_version_id
            ]
            look_ids = [
                character.active_look_version_id
                for character in bound_characters
                if character.active_look_version_id
            ]
            story_state_ids = [
                character.active_story_state_version_id
                for character in bound_characters
                if character.active_story_state_version_id
            ]
            dialogue = line.text if line.line_type in {"DIALOGUE", "VOICE_OVER"} else ""
            speaking_character = characters_by_key.get(line.speaker_key)
            speaking_label = speaking_character.name if speaking_character is not None else "旁白"
            description = (
                f"{script_scene.purpose}。{line.text}"
                if line.line_type == "ACTION"
                else f"{speaking_label}以{line.emotion}状态完成台词，保持空间连续性"
            )
            shot_size = ("WS", "MS", "MCU", "CU")[(shot_ordinal - 1) % 4]
            camera = ("STATIC", "TRACK", "DOLLY_IN", "PAN")[(shot_ordinal - 1) % 4]
            shot = Shot(
                id=str(uuid4()),
                scene_id=scene.id,
                code=code,
                ordinal=shot_ordinal,
                title=f"{script_scene.heading} · {code}",
                description=description,
                dialogue=dialogue,
                duration_sec=duration_sec,
                status="QUEUED",
                shot_size=shot_size,
                camera_movement=camera,
                current_take=0,
                candidate_take=None,
                continuity="CLEAR",
                location=script_scene.location,
                time_of_day=script_scene.time_of_day,
                current_take_id=None,
                character_ids_json=canonical_json(character_ids),
                character_look_version="Look V1",
                character_identity_version_ids_json=canonical_json(identity_ids),
                character_look_version_ids_json=canonical_json(look_ids),
                character_story_state_version_ids_json=canonical_json(story_state_ids),
                lock_version=1,
            )
            session.add(shot)
            session.flush()
            prompt_payload = {
                "description": description,
                "dialogue": dialogue,
                "style": project.style,
                "location": script_scene.location,
                "time_of_day": script_scene.time_of_day,
                "shot_size": shot_size,
                "camera": camera,
                "character_ids": character_ids,
                "character_names": [character.name for character in bound_characters],
                "character_identity_version_ids": identity_ids,
                "character_look_ids": look_ids,
                "character_story_state_version_ids": story_state_ids,
                "location_version_id": location.id if location else None,
                "prop_version_ids": [item.id for item in props],
            }
            spec = ShotSpec(
                id=str(uuid4()),
                storyboard_version_id=storyboard.id,
                shot_id=shot.id,
                script_scene_id=script_scene.id,
                script_line_ids_json=canonical_json([line.id]),
                ordinal=shot_ordinal,
                description=description,
                dialogue=dialogue,
                duration_ms=duration_sec * 1000,
                shot_size=shot_size,
                camera_movement=camera,
                character_look_ids_json=canonical_json(look_ids),
                location_version_id=location.id if location else None,
                prop_version_ids_json=canonical_json([item.id for item in props]),
                prompt_json=canonical_json(prompt_payload),
                content_hash=content_hash(prompt_payload),
                status="QUEUED",
            )
            session.add(spec)
            session.flush()
            reference_asset_ids: list[str] = []
            for character in bound_characters:
                if not character.locked_candidate_id:
                    continue
                candidate = session.get(CharacterCandidate, character.locked_candidate_id)
                if candidate is not None:
                    reference_asset_ids.append(candidate.asset_id)
            child, _ = enqueue_job(
                session,
                project_id=project.id,
                job_type="GENERATE_STORYBOARD_TAKE",
                entity_type="shot_spec",
                entity_id=spec.id,
                idempotency_key=f"{project.id}:GENERATE_STORYBOARD_TAKE:{spec.id}:v1",
                input_payload={
                    "storyboard_version_id": storyboard.id,
                    "workflow_run_id": workflow.id,
                    "shot_spec_id": spec.id,
                    "shot_id": shot.id,
                    "prompt": canonical_json(prompt_payload),
                    "reference_asset_ids": reference_asset_ids,
                    "seed": int(spec.content_hash[:8], 16),
                },
                label=f"{code} · 分镜版本",
                stage="等待生成低成本分镜",
                trace_id=job.trace_id,
                estimated_seconds=30,
                retryable=True,
            )
            session.add(
                JobDependency(
                    id=str(uuid4()),
                    job_id=child.id,
                    depends_on_job_id=job.id,
                    dependency_type="SUCCESS",
                    created_at=now,
                )
            )
            session.add(
                WorkflowNode(
                    id=str(uuid4()),
                    workflow_run_id=workflow.id,
                    node_key=f"storyboard.take.{shot_ordinal}",
                    node_type="JOB",
                    entity_type="shot_spec",
                    entity_id=spec.id,
                    job_id=child.id,
                    status="READY",
                    dependency_keys_json=canonical_json(["storyboard.plan"]),
                    output_json="{}",
                    degraded=False,
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            child_job_ids.append(child.id)
            shot_payloads.append(
                {
                    "shot_spec_id": spec.id,
                    "shot_id": shot.id,
                    "script_line_id": line.id,
                    "duration_ms": spec.duration_ms,
                    "content_hash": spec.content_hash,
                }
            )
            shot_ordinal += 1
    storyboard.payload_json = canonical_json(
        {
            "schema_version": "storyboard-v2",
            "script_version_id": script.id,
            "visual_bible_version_id": visual_bible.id,
            "shots": shot_payloads,
        }
    )
    storyboard.content_hash = content_hash(json.loads(storyboard.payload_json))
    root_node = session.scalar(
        select(WorkflowNode).where(
            WorkflowNode.workflow_run_id == workflow.id,
            WorkflowNode.node_key == "storyboard.plan",
        )
    )
    if root_node is not None:
        root_node.status = "FAN_OUT_COMPLETE"
        root_node.output_json = canonical_json({"child_job_ids": child_job_ids})
        root_node.updated_at = now
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="storyboard.planned",
        payload={
            "storyboard_version_id": storyboard.id,
            "shot_count": len(shot_payloads),
            "child_job_ids": child_job_ids,
        },
    )
    session.flush()
    return storyboard, child_job_ids


def reference_data_urls(
    session: Session,
    settings: Settings,
    asset_ids: list[str],
    *,
    mask_character_watermark: bool = False,
) -> list[str]:
    values: list[str] = []
    for asset_id in asset_ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            continue
        content = resolve_asset_path(settings, asset).read_bytes()
        if mask_character_watermark:
            content = mask_character_reference_watermark(content, asset.mime)
        values.append(f"data:{asset.mime};base64,{base64.b64encode(content).decode()}")
    return values


def mask_character_reference_watermark(content: bytes, mime: str) -> bytes:
    """Hide the known lower-right AI label before a character image is reused."""
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return content
    try:
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, UnidentifiedImageError):
        return content

    width, height = image.size
    if width < 64 or height < 64:
        return content

    left = round(width * 0.78)
    top = round(height * 0.88)
    patch_width = width - left
    patch_height = height - top
    source_top = max(0, top - patch_height)
    replacement = image.crop((left, source_top, width, top)).resize(
        (patch_width, patch_height),
        Image.Resampling.LANCZOS,
    )
    replacement = replacement.filter(
        ImageFilter.GaussianBlur(radius=max(1, round(min(patch_width, patch_height) * 0.04)))
    )

    patched = image.copy()
    patched.paste(replacement, (left, top))
    feather = max(2, round(min(width, height) * 0.006))
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle(
        (left + feather, top + feather, width, height),
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    result = Image.composite(patched, image, mask)

    output = BytesIO()
    if mime == "image/jpeg":
        result.convert("RGB").save(output, format="JPEG", quality=95, subsampling=0)
    elif mime == "image/webp":
        result.save(output, format="WEBP", quality=95)
    else:
        result.save(output, format="PNG", optimize=True)
    return output.getvalue()


def materialize_storyboard_take(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
) -> tuple[Asset, Take, Job | None]:
    payload = json.loads(job.input_json)
    spec = session.get(ShotSpec, str(payload["shot_spec_id"]))
    shot = session.get(Shot, str(payload["shot_id"]))
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if spec is None or shot is None or storyboard is None:
        raise ValueError("分镜任务实体不存在")
    existing = session.scalar(
        select(Take).where(Take.shot_id == shot.id, Take.kind == "STORYBOARD")
    )
    if existing is not None:
        asset = session.get(Asset, existing.asset_id)
        if asset is None:
            raise ValueError("分镜版本资产不存在")
        return asset, existing, None
    tmp_dir = settings.data_dir / "tmp" / job.id / "storyboard-take"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"{shot.code}{suffix}")
    image_path.write_bytes(image.content)
    take_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="storyboard",
        source=image_path,
        source_entity_type="take",
        source_entity_id=take_id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    asset.metadata_json = canonical_json(
        {
            "model": image.model,
            "provider_request_id": image.request_id,
            "source_url": image.source_url,
            "seed": payload["seed"],
            "storyboard_version_id": storyboard.id,
            "temporary": True,
        }
    )
    now = datetime.now(UTC)
    take = Take(
        id=take_id,
        shot_id=shot.id,
        kind="STORYBOARD",
        version=1,
        asset_id=asset.id,
        status="GENERATED",
        approval="APPROVED",
        is_current=True,
        parent_take_id=None,
        identity_status="NOT_APPLICABLE",
        identity_score=None,
        identity_message=None,
        identity_reference_asset_ids_json="[]",
        identity_review_decision=None,
        identity_review_issues_json="[]",
        identity_review_note=None,
        identity_review_actor=None,
        identity_reviewed_at=None,
        identity_review_look_version=None,
        created_at=now,
    )
    session.add(take)
    shot.current_take = 1
    shot.current_take_id = take.id
    shot.status = "READY"
    shot.lock_version += 1
    spec.status = "READY"
    node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
    if node is not None:
        node.status = "SUCCEEDED"
        node.output_json = canonical_json({"take_id": take.id, "asset_id": asset.id})
        node.updated_at = now
    session.flush()
    remaining = session.scalar(
        select(func.count(ShotSpec.id)).where(
            ShotSpec.storyboard_version_id == storyboard.id,
            ShotSpec.status != "READY",
        )
    )
    next_job: Job | None = None
    if remaining == 0:
        next_job, _ = enqueue_job(
            session,
            project_id=job.project_id,
            job_type="GENERATE_ANIMATIC",
            entity_type="storyboard_version",
            entity_id=storyboard.id,
            idempotency_key=f"{job.project_id}:GENERATE_ANIMATIC:{storyboard.id}:v1",
            input_payload={
                "storyboard_version_id": storyboard.id,
                "workflow_run_id": storyboard.workflow_run_id,
                "temporary_audio": True,
            },
            label="分镜 · 临时声音节奏样片",
            stage="等待生成带临时对白与音乐的节奏样片",
            trace_id=job.trace_id,
            estimated_seconds=12,
            retryable=True,
        )
        storyboard.status = "ANIMATIC_RUNNING"
        child_jobs = list(
            session.scalars(
                select(Job).where(
                    Job.project_id == job.project_id,
                    Job.job_type == "GENERATE_STORYBOARD_TAKE",
                    Job.input_json.contains(storyboard.id),
                )
            ).all()
        )
        for child in child_jobs:
            session.add(
                JobDependency(
                    id=str(uuid4()),
                    job_id=next_job.id,
                    depends_on_job_id=child.id,
                    dependency_type="SUCCESS",
                    created_at=now,
                )
            )
        if storyboard.workflow_run_id:
            session.add(
                WorkflowNode(
                    id=str(uuid4()),
                    workflow_run_id=storyboard.workflow_run_id,
                    node_key="animatic.render",
                    node_type="FAN_IN",
                    entity_type="storyboard_version",
                    entity_id=storyboard.id,
                    job_id=next_job.id,
                    status="READY",
                    dependency_keys_json=canonical_json(
                        [
                            f"storyboard.take.{item.ordinal}"
                            for item in session.scalars(
                                select(ShotSpec).where(
                                    ShotSpec.storyboard_version_id == storyboard.id
                                )
                            ).all()
                        ]
                    ),
                    output_json="{}",
                    degraded=False,
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="storyboard.take_ready",
        payload={"shot_spec_id": spec.id, "take_id": take.id},
    )
    session.flush()
    return asset, take, next_job


def animatic_inputs(
    session: Session,
    settings: Settings,
    job: Job,
) -> tuple[Project, StoryboardVersion, list[PreviewShot]]:
    payload = json.loads(job.input_json)
    storyboard = session.get(StoryboardVersion, str(payload["storyboard_version_id"]))
    if storyboard is None:
        raise ValueError("分镜版本不存在")
    project = project_or_404(session, job.project_id)
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    shots: list[PreviewShot] = []
    for spec in specs:
        shot = session.get(Shot, spec.shot_id)
        take = session.scalar(
            select(Take).where(
                Take.shot_id == spec.shot_id,
                Take.kind == "STORYBOARD",
                Take.is_current.is_(True),
            )
        )
        if shot is None or take is None:
            raise ValueError("节奏样片缺少分镜版本")
        asset = session.get(Asset, take.asset_id)
        if asset is None:
            raise ValueError("分镜资产不存在")
        shots.append(
            PreviewShot(
                id=shot.id,
                code=shot.code,
                title=shot.title,
                dialogue=shot.dialogue,
                duration_sec=shot.duration_sec,
                image_path=resolve_asset_path(settings, asset),
            )
        )
    return project, storyboard, shots


def register_animatic(
    session: Session,
    settings: Settings,
    *,
    job: Job,
    storyboard: StoryboardVersion,
    files: PreviewFiles,
) -> Asset:
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="animatic",
        source=files.mp4,
        source_entity_type="storyboard_version",
        source_entity_id=storyboard.id,
        mime="video/mp4",
        width=files.width,
        height=files.height,
        duration_ms=files.duration_ms,
    )
    asset.metadata_json = canonical_json(
        {
            "storyboard_version_id": storyboard.id,
            "temporary_dialogue": True,
            "temporary_music": True,
            "probe": files.probe,
        }
    )
    for kind, source, mime in (
        ("animatic_srt", files.srt, "application/x-subrip"),
        ("animatic_vtt", files.vtt, "text/vtt"),
        ("animatic_manifest", files.manifest, "application/json"),
    ):
        register_file(
            session,
            settings,
            project_id=job.project_id,
            kind=kind,
            source=source,
            source_entity_type="storyboard_version",
            source_entity_id=storyboard.id,
            mime=mime,
        )
    now = datetime.now(UTC)
    storyboard.animatic_asset_id = asset.id
    storyboard.status = "READY_FOR_REVIEW"
    project = project_or_404(session, job.project_id)
    project.status = "STORYBOARD_READY"
    project.lock_version += 1
    project.updated_at = now
    if storyboard.workflow_run_id:
        workflow = session.get(WorkflowRun, storyboard.workflow_run_id)
        if workflow is not None:
            workflow.status = "WAITING_FOR_GATE"
            workflow.current_gate = "G4_STORYBOARD"
            workflow.updated_at = now
        node = session.scalar(select(WorkflowNode).where(WorkflowNode.job_id == job.id))
        if node is not None:
            node.status = "SUCCEEDED"
            node.output_json = canonical_json({"animatic_asset_id": asset.id})
            node.updated_at = now
        existing_gate = session.scalar(
            select(ReviewGate).where(
                ReviewGate.workflow_run_id == storyboard.workflow_run_id,
                ReviewGate.gate_key == "G4_STORYBOARD",
            )
        )
        if existing_gate is None:
            session.add(
                ReviewGate(
                    id=str(uuid4()),
                    workflow_run_id=storyboard.workflow_run_id,
                    project_id=job.project_id,
                    gate_key="G4_STORYBOARD",
                    entity_type="storyboard_version",
                    entity_id=storyboard.id,
                    status="PENDING_REVIEW",
                    decision=None,
                    decided_by=None,
                    decided_at=None,
                    created_at=now,
                )
            )
    append_event(
        session,
        project_id=job.project_id,
        job_id=job.id,
        event_type="animatic.ready",
        payload={"storyboard_version_id": storyboard.id, "asset_id": asset.id},
    )
    session.flush()
    return asset


def storyboard_workspace(session: Session, project_id: str) -> dict[str, object]:
    project_or_404(session, project_id)
    storyboard = session.scalar(
        select(StoryboardVersion)
        .where(StoryboardVersion.project_id == project_id)
        .order_by(StoryboardVersion.version.desc())
    )
    if storyboard is None:
        return {"storyboard": None, "shots": [], "workflow": None, "gate": None}
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.storyboard_version_id == storyboard.id)
            .order_by(ShotSpec.ordinal)
        ).all()
    )
    shots = []
    for spec in specs:
        shot = session.get(Shot, spec.shot_id)
        take = session.scalar(
            select(Take).where(Take.shot_id == spec.shot_id, Take.kind == "STORYBOARD")
        )
        shots.append(
            {
                "shot_spec_id": spec.id,
                "shot_id": spec.shot_id,
                "code": shot.code if shot else f"S{spec.ordinal:02d}",
                "title": shot.title if shot else "",
                "description": spec.description,
                "dialogue": spec.dialogue,
                "duration_ms": spec.duration_ms,
                "shot_size": spec.shot_size,
                "camera_movement": spec.camera_movement,
                "character_look_ids": json.loads(spec.character_look_ids_json),
                "location_version_id": spec.location_version_id,
                "prop_version_ids": json.loads(spec.prop_version_ids_json),
                "status": spec.status,
                "image_url": f"/api/v1/assets/{take.asset_id}/content" if take else None,
                "content_hash": spec.content_hash,
            }
        )
    workflow = (
        session.get(WorkflowRun, storyboard.workflow_run_id) if storyboard.workflow_run_id else None
    )
    nodes = (
        list(
            session.scalars(
                select(WorkflowNode)
                .where(WorkflowNode.workflow_run_id == workflow.id)
                .order_by(WorkflowNode.created_at)
            ).all()
        )
        if workflow
        else []
    )
    gate = (
        session.scalar(
            select(ReviewGate).where(
                ReviewGate.workflow_run_id == workflow.id,
                ReviewGate.gate_key == "G4_STORYBOARD",
            )
        )
        if workflow
        else None
    )
    return {
        "storyboard": {
            "id": storyboard.id,
            "version": storyboard.version,
            "status": storyboard.status,
            "episode_id": storyboard.episode_id,
            "script_version_id": storyboard.script_version_id,
            "visual_bible_version_id": storyboard.visual_bible_version_id,
            "content_hash": storyboard.content_hash,
            "animatic_url": (
                f"/api/v1/assets/{storyboard.animatic_asset_id}/content"
                if storyboard.animatic_asset_id
                else None
            ),
        },
        "shots": shots,
        "workflow": (
            {
                "id": workflow.id,
                "status": workflow.status,
                "current_gate": workflow.current_gate,
                "nodes": [
                    {
                        "id": item.id,
                        "node_key": item.node_key,
                        "node_type": item.node_type,
                        "status": item.status,
                        "job_id": item.job_id,
                        "dependencies": json.loads(item.dependency_keys_json),
                        "degraded": item.degraded,
                    }
                    for item in nodes
                ],
            }
            if workflow
            else None
        ),
        "gate": (
            {
                "id": gate.id,
                "gate_key": gate.gate_key,
                "status": gate.status,
                "decision": gate.decision,
            }
            if gate
            else None
        ),
    }


def approve_storyboard(
    session: Session,
    *,
    storyboard_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
) -> tuple[dict[str, object], JobRead, bool]:
    storyboard = session.get(StoryboardVersion, storyboard_id)
    if storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜版本不存在"},
        )
    project = project_or_404(session, storyboard.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "STORYBOARD_READY" or storyboard.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={"code": "STORYBOARD_NOT_READY", "message": "分镜尚未达到批准条件"},
        )
    now = datetime.now(UTC)
    storyboard.status = "APPROVED"
    storyboard.approved_at = now
    storyboard.approved_by = actor
    project.status = "STORYBOARD_APPROVED"
    project.lock_version += 1
    project.updated_at = now
    workflow = session.get(WorkflowRun, storyboard.workflow_run_id)
    if workflow is not None:
        workflow.status = "RUNNING"
        workflow.current_gate = None
        workflow.updated_at = now
    gate = session.scalar(
        select(ReviewGate).where(
            ReviewGate.workflow_run_id == storyboard.workflow_run_id,
            ReviewGate.gate_key == "G4_STORYBOARD",
        )
    )
    if gate is not None:
        gate.status = "APPROVED"
        gate.decision = "APPROVE"
        gate.decided_by = actor
        gate.decided_at = now
    job, replayed = enqueue_job(
        session,
        project_id=project.id,
        job_type="START_MEDIA_PRODUCTION",
        entity_type="storyboard_version",
        entity_id=storyboard.id,
        idempotency_key=f"{project.id}:START_MEDIA_PRODUCTION:{storyboard.id}:v1",
        input_payload={
            "storyboard_version_id": storyboard.id,
            "workflow_run_id": storyboard.workflow_run_id,
            "config_version": "media-production-v1",
        },
        label=f"{project.name} · 正式媒体生产",
        stage="等待展开正式关键帧、视频与音频任务",
        trace_id=trace_id,
        estimated_seconds=4,
        retryable=True,
    )
    append_event(
        session,
        project_id=project.id,
        job_id=job.id,
        event_type="storyboard.approved",
        payload={"storyboard_version_id": storyboard.id},
    )
    session.commit()
    session.refresh(job)
    return (
        {
            "id": storyboard.id,
            "version": storyboard.version,
            "status": storyboard.status,
        },
        job_to_read(job),
        replayed,
    )


def list_workflow_runs(session: Session, project_id: str) -> list[dict[str, object]]:
    project_or_404(session, project_id)
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project_id)
        .order_by(WorkflowRun.created_at.desc())
    ).all()
    result = []
    for run in runs:
        nodes = session.scalars(
            select(WorkflowNode)
            .where(WorkflowNode.workflow_run_id == run.id)
            .order_by(WorkflowNode.created_at)
        ).all()
        result.append(
            {
                "id": run.id,
                "workflow_type": run.workflow_type,
                "status": run.status,
                "current_gate": run.current_gate,
                "source_entity_type": run.source_entity_type,
                "source_entity_id": run.source_entity_id,
                "nodes": [
                    {
                        "id": item.id,
                        "node_key": item.node_key,
                        "node_type": item.node_type,
                        "entity_type": item.entity_type,
                        "entity_id": item.entity_id,
                        "job_id": item.job_id,
                        "status": item.status,
                        "dependencies": json.loads(item.dependency_keys_json),
                        "degraded": item.degraded,
                        "error_code": item.error_code,
                    }
                    for item in nodes
                ],
            }
        )
    return result
