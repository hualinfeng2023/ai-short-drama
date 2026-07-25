import json
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    AudioCue,
    AudioTake,
    Episode,
    GenerationRecord,
    Scene,
    ScriptLine,
    ScriptScene,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    TimelineClip,
    TimelineItem,
    TimelineVersion,
)
from app.services.projects import content_hash


def _json_ids(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _ref(
    object_type: str,
    object_id: str,
    *,
    next_status: str | None = None,
    approval_preserved: bool | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {"type": object_type, "id": object_id}
    if next_status is not None:
        result["next_status"] = next_status
    if approval_preserved is not None:
        result["approval_preserved"] = approval_preserved
    return result


def _edge(
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation: str,
    evidence: str,
    *,
    inferred: bool = False,
) -> dict[str, object]:
    return {
        "source": {"type": source_type, "id": source_id},
        "target": {"type": target_type, "id": target_id},
        "relation": relation,
        "evidence": evidence,
        "inferred": inferred,
    }


def _take_hash(take: Take) -> str:
    return content_hash(
        {
            "status": take.status,
            "approval": take.approval,
            "asset_id": take.asset_id,
            "is_current": take.is_current,
        }
    )


def _audio_take_hash(take: AudioTake) -> str:
    return content_hash(
        {
            "status": take.status,
            "approval": take.approval,
            "asset_id": take.asset_id,
            "is_current": take.is_current,
        }
    )


def _unique_refs(values: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    unique: dict[tuple[str, str], dict[str, object]] = {}
    for value in values:
        key = (str(value.get("type", "")), str(value.get("id", "")))
        if all(key):
            unique[key] = value
    return [unique[key] for key in sorted(unique)]


def analyze_script_scene_dependencies(
    session: Session,
    *,
    project_id: str,
    script_scene_id: str,
) -> dict[str, object]:
    script_scene = session.get(ScriptScene, script_scene_id)
    if script_scene is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCRIPT_SCENE_NOT_FOUND", "message": "剧本场景不存在"},
        )
    script_lines = list(
        session.scalars(
            select(ScriptLine)
            .where(ScriptLine.script_scene_id == script_scene.id)
            .order_by(ScriptLine.ordinal)
        )
    )
    line_ids = {line.id for line in script_lines}
    specs = list(
        session.scalars(
            select(ShotSpec)
            .join(StoryboardVersion, ShotSpec.storyboard_version_id == StoryboardVersion.id)
            .where(
                StoryboardVersion.project_id == project_id,
                ShotSpec.script_scene_id == script_scene.id,
            )
        )
    )
    shot_ids = {spec.shot_id for spec in specs}
    storyboard_ids = {spec.storyboard_version_id for spec in specs}
    shots = list(session.scalars(select(Shot).where(Shot.id.in_(shot_ids)))) if shot_ids else []
    takes = (
        list(session.scalars(select(Take).where(Take.shot_id.in_(shot_ids)))) if shot_ids else []
    )
    take_ids = {take.id for take in takes}

    cue_conditions = [AudioCue.script_scene_id == script_scene.id]
    if line_ids:
        cue_conditions.append(AudioCue.script_line_id.in_(line_ids))
    if shot_ids:
        cue_conditions.append(AudioCue.shot_id.in_(shot_ids))
    audio_cues = list(
        session.scalars(
            select(AudioCue).where(
                AudioCue.project_id == project_id,
                or_(*cue_conditions),
            )
        )
    )
    cue_ids = {cue.id for cue in audio_cues}
    audio_takes = (
        list(
            session.scalars(
                select(AudioTake).where(
                    AudioTake.project_id == project_id,
                    AudioTake.audio_cue_id.in_(cue_ids),
                )
            )
        )
        if cue_ids
        else []
    )
    audio_take_ids = {take.id for take in audio_takes}

    asset_ids = {take.asset_id for take in takes} | {take.asset_id for take in audio_takes}
    assets = (
        list(
            session.scalars(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.id.in_(asset_ids),
                )
            )
        )
        if asset_ids
        else []
    )
    generation_ids = {
        value
        for value in [
            *[take.generation_record_id for take in takes],
            *[take.generation_record_id for take in audio_takes],
        ]
        if value
    }
    generations = (
        list(
            session.scalars(
                select(GenerationRecord).where(
                    GenerationRecord.project_id == project_id,
                    GenerationRecord.id.in_(generation_ids),
                )
            )
        )
        if generation_ids
        else []
    )

    timeline_ids = set(
        session.scalars(select(TimelineVersion.id).where(TimelineVersion.project_id == project_id))
    )
    timeline_items = (
        list(
            session.scalars(
                select(TimelineItem).where(
                    TimelineItem.timeline_id.in_(timeline_ids),
                    or_(
                        TimelineItem.shot_id.in_(shot_ids),
                        TimelineItem.take_id.in_(take_ids),
                    ),
                )
            )
        )
        if timeline_ids and (shot_ids or take_ids)
        else []
    )
    clip_conditions = []
    if shot_ids:
        clip_conditions.append(
            (TimelineClip.source_entity_type.ilike("shot"))
            & TimelineClip.source_entity_id.in_(shot_ids)
        )
    if take_ids:
        clip_conditions.append(
            (TimelineClip.source_entity_type.ilike("take"))
            & TimelineClip.source_entity_id.in_(take_ids)
        )
    if cue_ids:
        clip_conditions.append(
            (TimelineClip.source_entity_type.ilike("audio_cue"))
            & TimelineClip.source_entity_id.in_(cue_ids)
        )
    if asset_ids:
        clip_conditions.append(TimelineClip.asset_id.in_(asset_ids))
    timeline_clips = (
        list(
            session.scalars(
                select(TimelineClip).where(
                    TimelineClip.project_id == project_id,
                    or_(*clip_conditions),
                )
            )
        )
        if clip_conditions
        else []
    )

    all_project_shot_ids = set(
        session.scalars(
            select(Shot.id)
            .join(Scene, Shot.scene_id == Scene.id)
            .join(Episode, Scene.episode_id == Episode.id)
            .where(Episode.project_id == project_id)
        )
    )
    preserved_takes = list(
        session.scalars(
            select(Take).where(
                Take.shot_id.in_(all_project_shot_ids - shot_ids),
                Take.approval == "APPROVED",
            )
        )
    )
    preserved_audio_takes = list(
        session.scalars(
            select(AudioTake).where(
                AudioTake.project_id == project_id,
                AudioTake.audio_cue_id.not_in(cue_ids),
                AudioTake.approval == "APPROVED",
            )
        )
    )

    edges: list[dict[str, object]] = []
    for line in script_lines:
        edges.append(
            _edge(
                "ScriptScene",
                script_scene.id,
                "ScriptLine",
                line.id,
                "CONTAINS_DIALOGUE",
                "script_lines.script_scene_id",
            )
        )
    for spec in specs:
        edges.extend(
            [
                _edge(
                    "ScriptScene",
                    script_scene.id,
                    "ShotSpec",
                    spec.id,
                    "SPECIFIES",
                    "shot_specs.script_scene_id",
                ),
                _edge(
                    "ShotSpec",
                    spec.id,
                    "Shot",
                    spec.shot_id,
                    "REALIZES",
                    "shot_specs.shot_id",
                ),
            ]
        )
        if spec.location_version_id:
            edges.append(
                _edge(
                    "Location",
                    spec.location_version_id,
                    "ShotSpec",
                    spec.id,
                    "LOCATION_FOR",
                    "shot_specs.location_version_id",
                )
            )
        for look_id in _json_ids(spec.character_look_ids_json):
            edges.append(
                _edge(
                    "CharacterLook",
                    look_id,
                    "ShotSpec",
                    spec.id,
                    "LOOK_FOR",
                    "shot_specs.character_look_ids_json",
                )
            )
    for shot in shots:
        for identity_id in _json_ids(shot.character_identity_version_ids_json):
            edges.append(
                _edge(
                    "CharacterIdentity",
                    identity_id,
                    "Shot",
                    shot.id,
                    "SNAPSHOTTED_BY",
                    "shots.character_identity_version_ids_json",
                )
            )
        for look_id in _json_ids(shot.character_look_version_ids_json):
            edges.append(
                _edge(
                    "CharacterLook",
                    look_id,
                    "Shot",
                    shot.id,
                    "SNAPSHOTTED_BY",
                    "shots.character_look_version_ids_json",
                )
            )
        for state_id in _json_ids(shot.character_story_state_version_ids_json):
            edges.append(
                _edge(
                    "CharacterState",
                    state_id,
                    "Shot",
                    shot.id,
                    "SNAPSHOTTED_BY",
                    "shots.character_story_state_version_ids_json",
                )
            )
    for take in takes:
        edges.extend(
            [
                _edge("Shot", take.shot_id, "Take", take.id, "GENERATED_TAKE", "takes.shot_id"),
                _edge("Take", take.id, "Asset", take.asset_id, "OUTPUT_ASSET", "takes.asset_id"),
            ]
        )
        if take.generation_record_id:
            edges.append(
                _edge(
                    "GenerationRecord",
                    take.generation_record_id,
                    "Take",
                    take.id,
                    "PRODUCED",
                    "takes.generation_record_id",
                )
            )
    for cue in audio_cues:
        source_type = "ScriptLine" if cue.script_line_id else "ScriptScene"
        source_id = cue.script_line_id or cue.script_scene_id or script_scene.id
        edges.append(
            _edge(
                source_type,
                source_id,
                "AudioCue",
                cue.id,
                "DRIVES_AUDIO",
                "audio_cues.script_line_id/script_scene_id",
            )
        )
    for take in audio_takes:
        edges.extend(
            [
                _edge(
                    "AudioCue",
                    take.audio_cue_id,
                    "AudioTake",
                    take.id,
                    "GENERATED_AUDIO_TAKE",
                    "audio_takes.audio_cue_id",
                ),
                _edge(
                    "AudioTake",
                    take.id,
                    "Asset",
                    take.asset_id,
                    "OUTPUT_ASSET",
                    "audio_takes.asset_id",
                ),
            ]
        )
    for item in timeline_items:
        edges.append(
            _edge(
                "Take",
                item.take_id,
                "TimelineClip",
                item.id,
                "USED_BY_TIMELINE",
                "timeline_items.take_id",
            )
        )
    for clip in timeline_clips:
        source_type = {
            "shot": "Shot",
            "take": "Take",
            "audio_cue": "AudioCue",
        }.get(clip.source_entity_type.lower(), clip.source_entity_type)
        edges.append(
            _edge(
                source_type,
                clip.source_entity_id,
                "TimelineClip",
                clip.id,
                "USED_BY_TIMELINE",
                "timeline_clips.source_entity_type/source_entity_id",
            )
        )

    affected = _unique_refs(
        [
            *[_ref("ShotSpec", item.id, next_status="OUTDATED") for item in specs],
            *[_ref("Storyboard", item, next_status="SUSPECT") for item in storyboard_ids],
            *[_ref("Shot", item.id, next_status="SUSPECT") for item in shots],
            *[
                _ref(
                    "Take",
                    item.id,
                    next_status="SUSPECT",
                    approval_preserved=item.approval == "APPROVED",
                )
                for item in takes
            ],
            *[_ref("AudioCue", item.id, next_status="OUTDATED") for item in audio_cues],
            *[
                _ref(
                    "AudioTake",
                    item.id,
                    next_status="SUSPECT",
                    approval_preserved=item.approval == "APPROVED",
                )
                for item in audio_takes
            ],
            *[_ref("Asset", item.id, next_status="SUSPECT") for item in assets],
            *[_ref("TimelineClip", item.id, next_status="SUSPECT") for item in timeline_items],
            *[_ref("TimelineClip", item.id, next_status="SUSPECT") for item in timeline_clips],
        ]
    )
    preserved = [
        *[
            {
                "type": "Take",
                "id": take.id,
                "approval": take.approval,
                "state_hash": _take_hash(take),
            }
            for take in preserved_takes
        ],
        *[
            {
                "type": "AudioTake",
                "id": take.id,
                "approval": take.approval,
                "state_hash": _audio_take_hash(take),
            }
            for take in preserved_audio_takes
        ],
    ]
    return {
        "source": {
            "type": "ScriptScene",
            "id": script_scene.id,
            "version_id": script_scene.script_version_id,
        },
        "dependency_edges": edges,
        "affected_objects": affected,
        "preserved_objects": preserved,
        "shot_spec_ids": sorted(spec.id for spec in specs),
        "storyboard_ids": sorted(storyboard_ids),
        "shot_ids": sorted(shot_ids),
        "take_ids": sorted(take_ids),
        "audio_cue_ids": sorted(cue_ids),
        "audio_take_ids": sorted(audio_take_ids),
        "asset_ids": sorted(asset_ids),
        "generation_record_ids": sorted(record.id for record in generations),
        "timeline_item_ids": sorted(item.id for item in timeline_items),
        "timeline_clip_ids": sorted(clip.id for clip in timeline_clips),
    }


def apply_dependency_invalidation(
    session: Session,
    *,
    project_id: str,
    impact: dict[str, object],
) -> dict[str, int]:
    identifiers = {
        key: {str(value) for value in impact.get(key, []) if isinstance(value, str)}
        for key in (
            "shot_spec_ids",
            "storyboard_ids",
            "shot_ids",
            "take_ids",
            "audio_cue_ids",
            "audio_take_ids",
            "asset_ids",
            "timeline_clip_ids",
        )
    }
    counts: dict[str, int] = {}

    specs = list(
        session.scalars(
            select(ShotSpec)
            .join(StoryboardVersion, ShotSpec.storyboard_version_id == StoryboardVersion.id)
            .where(
                StoryboardVersion.project_id == project_id,
                ShotSpec.id.in_(identifiers["shot_spec_ids"]),
            )
        )
    )
    for spec in specs:
        spec.status = "OUTDATED"
    counts["ShotSpec"] = len(specs)

    storyboards = list(
        session.scalars(
            select(StoryboardVersion).where(
                StoryboardVersion.project_id == project_id,
                StoryboardVersion.id.in_(identifiers["storyboard_ids"]),
            )
        )
    )
    for storyboard in storyboards:
        storyboard.status = "SUSPECT"
    counts["Storyboard"] = len(storyboards)

    project_shot_ids = set(
        session.scalars(
            select(Shot.id)
            .join(Scene, Shot.scene_id == Scene.id)
            .join(Episode, Scene.episode_id == Episode.id)
            .where(
                Episode.project_id == project_id,
                Shot.id.in_(identifiers["shot_ids"]),
            )
        )
    )
    shots = list(session.scalars(select(Shot).where(Shot.id.in_(project_shot_ids))))
    for shot in shots:
        shot.status = "SUSPECT"
    counts["Shot"] = len(shots)

    takes = list(
        session.scalars(
            select(Take).where(
                Take.id.in_(identifiers["take_ids"]),
                Take.shot_id.in_(project_shot_ids),
            )
        )
    )
    for take in takes:
        take.status = "SUSPECT"
    counts["Take"] = len(takes)

    cues = list(
        session.scalars(
            select(AudioCue).where(
                AudioCue.project_id == project_id,
                AudioCue.id.in_(identifiers["audio_cue_ids"]),
            )
        )
    )
    for cue in cues:
        cue.status = "OUTDATED"
    counts["AudioCue"] = len(cues)

    audio_takes = list(
        session.scalars(
            select(AudioTake).where(
                AudioTake.project_id == project_id,
                AudioTake.id.in_(identifiers["audio_take_ids"]),
            )
        )
    )
    for take in audio_takes:
        take.status = "SUSPECT"
    counts["AudioTake"] = len(audio_takes)

    assets = list(
        session.scalars(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.id.in_(identifiers["asset_ids"]),
            )
        )
    )
    for asset in assets:
        asset.status = "SUSPECT"
    counts["Asset"] = len(assets)

    clips = list(
        session.scalars(
            select(TimelineClip).where(
                TimelineClip.project_id == project_id,
                TimelineClip.id.in_(identifiers["timeline_clip_ids"]),
            )
        )
    )
    for clip in clips:
        clip.degraded = True
    counts["TimelineClip"] = len(clips)
    session.flush()
    return counts
