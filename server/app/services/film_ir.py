import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    ChangeSet,
    Character,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    Episode,
    EpisodeOutlineVersion,
    GenerationRecord,
    Project,
    Scene,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryBibleVersion,
    StoryboardVersion,
    StoryVersion,
    Take,
    TimelineClip,
    TimelineItem,
    TimelineVersion,
)
from app.domain.film_ir import (
    FilmIREdge,
    FilmIRObject,
    FilmIRProjection,
    FilmIRReference,
    FilmIRSource,
    FilmIRWarning,
)


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _approval(status: str, *, explicit: str | None = None) -> str:
    value = (explicit or status or "").upper()
    if value in {"APPROVED", "LOCKED", "REJECTED"}:
        return value
    return "UNREVIEWED"


def _latest_by(rows: Iterable[Any], key: str) -> list[Any]:
    latest: dict[Any, Any] = {}
    for row in rows:
        group = getattr(row, key)
        current = latest.get(group)
        if current is None or row.version > current.version:
            latest[group] = row
    return list(latest.values())


class _ProjectionBuilder:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.objects: dict[tuple[str, str], FilmIRObject] = {}
        self.edges: list[FilmIREdge] = []
        self.warnings: list[FilmIRWarning] = []
        self._edge_keys: set[tuple[str, str, str, str, str]] = set()

    def add_object(
        self,
        *,
        object_type: str,
        object_id: str,
        version_id: str | None,
        canonical_kind: str,
        status: str,
        table: str,
        row_id: str,
        attributes: dict[str, Any],
        approval: str | None = None,
        derived_id: bool = False,
    ) -> None:
        self.objects[(object_type, object_id)] = FilmIRObject(
            type=object_type,
            id=object_id,
            version_id=version_id,
            canonical_kind=canonical_kind,
            canonical_status=status,
            approval_status=_approval(status, explicit=approval),
            source=FilmIRSource(
                table=table,
                row_id=row_id,
                id_strategy="VERSION_SCOPED_DERIVED" if derived_id else "PERSISTED",
            ),
            attributes=attributes,
        )

    def add_edge(
        self,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        relation: str,
        *,
        inferred: bool,
        evidence: str,
    ) -> None:
        source = self.objects.get((source_type, source_id))
        target = self.objects.get((target_type, target_id))
        if source is None or target is None:
            return
        key = (source_type, source_id, target_type, target_id, relation)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        source_ref = FilmIRReference(type=source.type, id=source.id, version_id=source.version_id)
        target_ref = FilmIRReference(type=target.type, id=target.id, version_id=target.version_id)
        self.edges.append(
            FilmIREdge(
                source=source_ref,
                target=target_ref,
                relation=relation,
                inferred=inferred,
                evidence=evidence,
            )
        )
        source.children.append(target_ref)
        source.downstream.append(target_ref)
        target.parent.append(source_ref)
        target.upstream.append(source_ref)

    def build(self) -> FilmIRProjection:
        return FilmIRProjection(
            project_id=self.project.id,
            project_lock_version=self.project.lock_version,
            objects=sorted(self.objects.values(), key=lambda item: (item.type, item.id)),
            edges=sorted(
                self.edges,
                key=lambda edge: (
                    edge.source.type,
                    edge.source.id,
                    edge.target.type,
                    edge.target.id,
                    edge.relation,
                ),
            ),
            warnings=self.warnings,
        )


def get_film_ir_projection(session: Session, project: Project) -> FilmIRProjection:
    """Build a read-only graph over canonical rows; this function never flushes or commits."""
    graph = _ProjectionBuilder(project)
    graph.add_object(
        object_type="Project",
        object_id=project.id,
        version_id=str(project.lock_version),
        canonical_kind="CANONICAL",
        status=project.status,
        table=Project.__tablename__,
        row_id=project.id,
        approval="APPROVED" if project.preview_approved else None,
        attributes={
            "name": project.name,
            "idea": project.idea,
            "current_story_version_id": project.current_story_version_id,
            "current_timeline_version_id": project.current_timeline_version_id,
        },
    )

    story = (
        session.get(StoryVersion, project.current_story_version_id)
        if project.current_story_version_id
        else session.scalar(
            select(StoryVersion)
            .where(StoryVersion.project_id == project.id)
            .order_by(StoryVersion.version.desc())
            .limit(1)
        )
    )
    if story is not None:
        graph.add_object(
            object_type="Story",
            object_id=f"story:{project.id}",
            version_id=story.id,
            canonical_kind="CANONICAL",
            status=story.status,
            table=StoryVersion.__tablename__,
            row_id=story.id,
            attributes={
                "version": story.version,
                "title": story.title,
                "logline": story.logline,
                "content_hash": story.content_hash,
            },
        )
        graph.add_edge(
            "Project",
            project.id,
            "Story",
            f"story:{project.id}",
            "CURRENT_STORY",
            inferred=False,
            evidence="projects.current_story_version_id",
        )

    scenes = list(
        session.scalars(
            select(Scene)
            .join(Episode, Scene.episode_id == Episode.id)
            .where(Episode.project_id == project.id)
            .order_by(Episode.ordinal, Scene.ordinal)
        )
    )
    for scene in scenes:
        graph.add_object(
            object_type="Scene",
            object_id=scene.id,
            version_id=None,
            canonical_kind="CANONICAL",
            status=scene.status,
            table=Scene.__tablename__,
            row_id=scene.id,
            attributes={
                "episode_id": scene.episode_id,
                "ordinal": scene.ordinal,
                "title": scene.title,
                "purpose": scene.purpose,
                "duration_sec": scene.duration_sec,
            },
        )

    scripts = _latest_by(
        session.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project.id)
            .order_by(ScriptVersion.episode_ordinal, ScriptVersion.version.desc())
        ),
        "episode_ordinal",
    )
    outline_ids = {script.outline_version_id for script in scripts}
    outlines = (
        {
            row.id: row
            for row in session.scalars(
                select(EpisodeOutlineVersion).where(EpisodeOutlineVersion.id.in_(outline_ids))
            )
        }
        if outline_ids
        else {}
    )
    bible_ids = {row.story_bible_version_id for row in outlines.values()}
    bibles = (
        {
            row.id: row
            for row in session.scalars(
                select(StoryBibleVersion).where(StoryBibleVersion.id.in_(bible_ids))
            )
        }
        if bible_ids
        else {}
    )

    script_scenes: list[ScriptScene] = []
    script_logical_ids = {
        script.id: f"script:{project.id}:{script.episode_ordinal}" for script in scripts
    }
    beat_scene_links: list[tuple[str, str]] = []
    for script in scripts:
        script_id = script_logical_ids[script.id]
        graph.add_object(
            object_type="Script",
            object_id=script_id,
            version_id=script.id,
            canonical_kind="CANONICAL",
            status=script.status,
            table=ScriptVersion.__tablename__,
            row_id=script.id,
            attributes={
                "episode_ordinal": script.episode_ordinal,
                "version": script.version,
                "estimated_duration_ms": script.estimated_duration_ms,
                "content_hash": script.content_hash,
            },
        )
        outline = outlines.get(script.outline_version_id)
        bible = bibles.get(outline.story_bible_version_id) if outline else None
        if story is not None and bible is not None and bible.story_version_id == story.id:
            graph.add_edge(
                "Story",
                f"story:{project.id}",
                "Script",
                script_id,
                "STORY_TO_SCRIPT",
                inferred=False,
                evidence=(
                    "script_versions.outline_version_id -> "
                    "episode_outline_versions.story_bible_version_id -> "
                    "story_bible_versions.story_version_id"
                ),
            )

        current_scenes = list(
            session.scalars(
                select(ScriptScene)
                .where(ScriptScene.script_version_id == script.id)
                .order_by(ScriptScene.ordinal)
            )
        )
        script_scenes.extend(current_scenes)
        script_scene_by_ordinal: dict[int, str] = {}
        for script_scene in current_scenes:
            logical_id = (
                f"script-scene:{project.id}:{script.episode_ordinal}:{script_scene.ordinal}"
            )
            script_scene_by_ordinal[script_scene.ordinal] = logical_id
            graph.add_object(
                object_type="ScriptScene",
                object_id=logical_id,
                version_id=script_scene.id,
                canonical_kind="CANONICAL",
                status=script.status,
                table=ScriptScene.__tablename__,
                row_id=script_scene.id,
                derived_id=True,
                attributes={
                    "episode_ordinal": script.episode_ordinal,
                    "ordinal": script_scene.ordinal,
                    "heading": script_scene.heading,
                    "location": script_scene.location,
                    "time_of_day": script_scene.time_of_day,
                    "purpose": script_scene.purpose,
                    "emotion": script_scene.emotion,
                    "duration_ms": script_scene.duration_ms,
                },
            )
            graph.add_edge(
                "Script",
                script_id,
                "ScriptScene",
                logical_id,
                "CONTAINS_SCENE",
                inferred=False,
                evidence="script_scenes.script_version_id",
            )

        payload = _json(script.payload_json, {})
        engine = payload.get("short_drama_engine", {}) if isinstance(payload, dict) else {}
        beats = engine.get("beats", []) if isinstance(engine, dict) else []
        for index, beat in enumerate(beats, start=1):
            if not isinstance(beat, dict):
                continue
            sequence = beat.get("sequence", index)
            persisted_key = beat.get("relationship_beat_id")
            beat_id = f"{script.id}:{sequence}"
            graph.add_object(
                object_type="Beat",
                object_id=beat_id,
                version_id=script.id,
                canonical_kind="DERIVED",
                status=script.status,
                table=ScriptVersion.__tablename__,
                row_id=script.id,
                derived_id=True,
                attributes={
                    "episode_ordinal": script.episode_ordinal,
                    "sequence": sequence,
                    "beat_type": beat.get("beat_type"),
                    "at_ms": beat.get("at_ms"),
                    "scene_ordinal": beat.get("scene_ordinal"),
                    "summary": beat.get("summary") or beat.get("description"),
                    "relationship_beat_id": persisted_key,
                },
            )
            graph.add_edge(
                "Script",
                script_id,
                "Beat",
                beat_id,
                "DERIVES_BEAT",
                inferred=True,
                evidence="script_versions.payload_json.short_drama_engine.beats",
            )
            scene_ordinal = beat.get("scene_ordinal")
            if isinstance(scene_ordinal, int) and scene_ordinal in script_scene_by_ordinal:
                beat_scene_links.append((beat_id, script_scene_by_ordinal[scene_ordinal]))
            else:
                graph.warnings.append(
                    FilmIRWarning(
                        code="BEAT_SCENE_LINEAGE_MISSING",
                        message="Beat 没有可解析的 scene_ordinal，无法投影到 ScriptScene。",
                        object_refs=[
                            FilmIRReference(type="Beat", id=beat_id, version_id=script.id)
                        ],
                    )
                )

    for beat_id, script_scene_id in beat_scene_links:
        graph.add_edge(
            "Beat",
            beat_id,
            "ScriptScene",
            script_scene_id,
            "BEAT_TO_SCRIPT_SCENE",
            inferred=True,
            evidence="script payload beat.scene_ordinal matched to script_scenes.ordinal",
        )

    shots = list(
        session.scalars(
            select(Shot)
            .join(Scene, Shot.scene_id == Scene.id)
            .join(Episode, Scene.episode_id == Episode.id)
            .where(Episode.project_id == project.id)
            .order_by(Episode.ordinal, Scene.ordinal, Shot.ordinal)
        )
    )
    shot_ids = {shot.id for shot in shots}
    storyboards = _latest_by(
        session.scalars(
            select(StoryboardVersion)
            .where(StoryboardVersion.project_id == project.id)
            .order_by(StoryboardVersion.episode_ordinal, StoryboardVersion.version.desc())
        ),
        "episode_ordinal",
    )
    for storyboard in storyboards:
        graph.add_object(
            object_type="Storyboard",
            object_id=f"storyboard:{project.id}:{storyboard.episode_ordinal}",
            version_id=storyboard.id,
            canonical_kind="DERIVED",
            status=storyboard.status,
            table=StoryboardVersion.__tablename__,
            row_id=storyboard.id,
            attributes={
                "episode_ordinal": storyboard.episode_ordinal,
                "version": storyboard.version,
                "content_hash": storyboard.content_hash,
            },
        )
        script_id = script_logical_ids.get(storyboard.script_version_id)
        if script_id:
            graph.add_edge(
                "Script",
                script_id,
                "Storyboard",
                f"storyboard:{project.id}:{storyboard.episode_ordinal}",
                "DERIVES_STORYBOARD",
                inferred=False,
                evidence="storyboard_versions.script_version_id",
            )
    specs = (
        {
            spec.shot_id: spec
            for spec in session.scalars(select(ShotSpec).where(ShotSpec.shot_id.in_(shot_ids)))
        }
        if shot_ids
        else {}
    )
    script_scene_logical_ids = {
        row.id: (
            f"script-scene:{project.id}:"
            f"{next(s.episode_ordinal for s in scripts if s.id == row.script_version_id)}:"
            f"{row.ordinal}"
        )
        for row in script_scenes
    }
    for shot in shots:
        spec = specs.get(shot.id)
        graph.add_object(
            object_type="Shot",
            object_id=shot.id,
            version_id=spec.id if spec else str(shot.lock_version),
            canonical_kind="CANONICAL",
            status=spec.status if spec else shot.status,
            table=ShotSpec.__tablename__ if spec else Shot.__tablename__,
            row_id=spec.id if spec else shot.id,
            attributes={
                "scene_id": shot.scene_id,
                "ordinal": shot.ordinal,
                "title": shot.title,
                "description": spec.description if spec else shot.description,
                "dialogue": spec.dialogue if spec else shot.dialogue,
                "duration_ms": spec.duration_ms if spec else shot.duration_sec * 1000,
                "current_take_id": shot.current_take_id,
            },
        )
        graph.add_edge(
            "Scene",
            shot.scene_id,
            "Shot",
            shot.id,
            "CONTAINS_SHOT",
            inferred=False,
            evidence="shots.scene_id",
        )
        if spec is not None:
            storyboard = next(
                (item for item in storyboards if item.id == spec.storyboard_version_id),
                None,
            )
            if storyboard is not None:
                graph.add_edge(
                    "Storyboard",
                    f"storyboard:{project.id}:{storyboard.episode_ordinal}",
                    "Shot",
                    shot.id,
                    "SPECIFIES_SHOT",
                    inferred=False,
                    evidence="shot_specs.storyboard_version_id / shot_specs.shot_id",
                )
            logical_id = script_scene_logical_ids.get(spec.script_scene_id)
            if logical_id:
                graph.add_edge(
                    "ScriptScene",
                    logical_id,
                    "Scene",
                    shot.scene_id,
                    "REALIZED_AS_SCENE",
                    inferred=False,
                    evidence="shot_specs.script_scene_id -> shot_specs.shot_id -> shots.scene_id",
                )

    characters = list(
        session.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .order_by(Character.character_key)
        )
    )
    for character in characters:
        graph.add_object(
            object_type="Character",
            object_id=character.id,
            version_id=str(character.lock_version),
            canonical_kind="CANONICAL",
            status=character.status,
            table=Character.__tablename__,
            row_id=character.id,
            approval="LOCKED" if character.locked_identity_version_id else None,
            attributes={
                "character_key": character.character_key,
                "name": character.name,
                "role": character.role,
                "locked_identity_version_id": character.locked_identity_version_id,
                "active_look_version_id": character.active_look_version_id,
                "active_story_state_version_id": character.active_story_state_version_id,
            },
        )

    version_specs = (
        (
            "CharacterIdentity",
            CharacterIdentityVersion,
            CharacterIdentityVersion.id.in_(
                {
                    row.locked_identity_version_id
                    for row in characters
                    if row.locked_identity_version_id
                }
            ),
        ),
        (
            "CharacterLook",
            CharacterLookVersion,
            CharacterLookVersion.id.in_(
                {row.active_look_version_id for row in characters if row.active_look_version_id}
            ),
        ),
        (
            "CharacterState",
            CharacterStoryStateVersion,
            CharacterStoryStateVersion.id.in_(
                {
                    row.active_story_state_version_id
                    for row in characters
                    if row.active_story_state_version_id
                }
            ),
        ),
    )
    for object_type, model, condition in version_specs:
        rows = list(session.scalars(select(model).where(condition)))
        for row in rows:
            graph.add_object(
                object_type=object_type,
                object_id=row.id,
                version_id=row.id,
                canonical_kind="CANONICAL",
                status=row.status,
                table=model.__tablename__,
                row_id=row.id,
                attributes={"character_id": row.character_id, "version": row.version},
            )
            graph.add_edge(
                "Character",
                row.character_id,
                object_type,
                row.id,
                "ACTIVE_CHARACTER_VERSION",
                inferred=False,
                evidence=f"characters active/locked version pointer -> {model.__tablename__}.id",
            )

    for shot in shots:
        for character_id in _json(shot.character_ids_json, []):
            graph.add_edge(
                "Character",
                str(character_id),
                "Shot",
                shot.id,
                "APPEARS_IN_SHOT",
                inferred=False,
                evidence="shots.character_ids_json",
            )
        for object_type, field_name in (
            ("CharacterIdentity", "character_identity_version_ids_json"),
            ("CharacterLook", "character_look_version_ids_json"),
            ("CharacterState", "character_story_state_version_ids_json"),
        ):
            for version_id in _json(getattr(shot, field_name), []):
                graph.add_edge(
                    object_type,
                    str(version_id),
                    "Shot",
                    shot.id,
                    "SNAPSHOTTED_BY_SHOT",
                    inferred=False,
                    evidence=f"shots.{field_name}",
                )

    assets = list(session.scalars(select(Asset).where(Asset.project_id == project.id)))
    for asset in assets:
        graph.add_object(
            object_type="Asset",
            object_id=asset.id,
            version_id=asset.sha256,
            canonical_kind="GENERATED",
            status=asset.status,
            table=Asset.__tablename__,
            row_id=asset.id,
            attributes={
                "kind": asset.kind,
                "mime": asset.mime,
                "is_temporary": asset.is_temporary,
                "source_entity_type": asset.source_entity_type,
                "source_entity_id": asset.source_entity_id,
            },
        )

    takes = (
        list(
            session.scalars(
                select(Take).where(Take.shot_id.in_(shot_ids)).order_by(Take.shot_id, Take.version)
            )
        )
        if shot_ids
        else []
    )
    for take in takes:
        graph.add_object(
            object_type="Take",
            object_id=take.id,
            version_id=str(take.version),
            canonical_kind="GENERATED",
            status=take.status,
            approval=take.approval,
            table=Take.__tablename__,
            row_id=take.id,
            attributes={
                "shot_id": take.shot_id,
                "kind": take.kind,
                "version": take.version,
                "asset_id": take.asset_id,
                "is_current": take.is_current,
                "parent_take_id": take.parent_take_id,
                "generation_record_id": take.generation_record_id,
            },
        )
        graph.add_edge(
            "Shot",
            take.shot_id,
            "Take",
            take.id,
            "GENERATED_TAKE",
            inferred=False,
            evidence="takes.shot_id",
        )
        graph.add_edge(
            "Take",
            take.id,
            "Asset",
            take.asset_id,
            "OUTPUT_ASSET",
            inferred=False,
            evidence="takes.asset_id",
        )
        if take.parent_take_id:
            graph.add_edge(
                "Take",
                take.parent_take_id,
                "Take",
                take.id,
                "NEXT_TAKE_VERSION",
                inferred=False,
                evidence="takes.parent_take_id",
            )

    records = list(
        session.scalars(select(GenerationRecord).where(GenerationRecord.project_id == project.id))
    )
    for record in records:
        graph.add_object(
            object_type="GenerationRecord",
            object_id=record.id,
            version_id=None,
            canonical_kind="GENERATED",
            status=record.status,
            table=GenerationRecord.__tablename__,
            row_id=record.id,
            attributes={
                "entity_type": record.entity_type,
                "entity_id": record.entity_id,
                "capability": record.capability,
                "provider": record.provider,
                "model": record.model,
                "output_asset_id": record.output_asset_id,
                "estimated_cost_usd": record.estimated_cost_usd,
            },
        )
        graph.add_edge(
            "GenerationRecord",
            record.id,
            "Asset",
            record.output_asset_id or "",
            "GENERATED_ASSET",
            inferred=False,
            evidence="generation_records.output_asset_id",
        )
    for take in takes:
        if take.generation_record_id:
            graph.add_edge(
                "GenerationRecord",
                take.generation_record_id,
                "Take",
                take.id,
                "PRODUCED_TAKE",
                inferred=False,
                evidence="takes.generation_record_id",
            )

    director_change_sets = list(
        session.scalars(
            select(ChangeSet)
            .where(ChangeSet.project_id == project.id)
            .order_by(ChangeSet.created_at)
        )
    )
    for change_set in director_change_sets:
        impact = _json(change_set.impact_json, {})
        proposal = impact.get("proposal") if isinstance(impact, dict) else None
        if not isinstance(proposal, dict):
            continue
        graph.add_object(
            object_type="DirectorProposal",
            object_id=change_set.id,
            version_id=None,
            canonical_kind="DERIVED",
            status=change_set.status,
            table=ChangeSet.__tablename__,
            row_id=change_set.id,
            attributes={
                "issue_type": proposal.get("issue_type"),
                "recommended_option": proposal.get("recommended_option"),
                "confidence": proposal.get("confidence"),
                "result_script_version_id": impact.get("result_script_version_id"),
                "rollback_script_version_id": impact.get("rollback_script_version_id"),
            },
        )
        for target in proposal.get("target_objects", []):
            if not isinstance(target, dict):
                continue
            target_type = str(target.get("type", ""))
            target_id = str(target.get("id", ""))
            if target_type == "ScriptScene":
                target_id = script_scene_logical_ids.get(target_id, target_id)
            graph.add_edge(
                "DirectorProposal",
                change_set.id,
                target_type,
                target_id,
                "PROPOSES_CHANGE_TO",
                inferred=False,
                evidence="change_sets.impact_json.proposal.target_objects",
            )
        for affected in impact.get("invalidated", []):
            if not isinstance(affected, dict):
                continue
            graph.add_edge(
                "DirectorProposal",
                change_set.id,
                str(affected.get("type", "")),
                str(affected.get("id", "")),
                "INVALIDATES",
                inferred=False,
                evidence="change_sets.impact_json.invalidated",
            )
        for preserved in proposal.get("preserved_objects", []):
            if not isinstance(preserved, dict):
                continue
            graph.add_edge(
                "DirectorProposal",
                change_set.id,
                str(preserved.get("type", "")),
                str(preserved.get("id", "")),
                "PRESERVES",
                inferred=False,
                evidence="change_sets.impact_json.proposal.preserved_objects",
            )

    timeline = (
        session.get(TimelineVersion, project.current_timeline_version_id)
        if project.current_timeline_version_id
        else session.scalar(
            select(TimelineVersion)
            .where(TimelineVersion.project_id == project.id)
            .order_by(TimelineVersion.version.desc())
            .limit(1)
        )
    )
    if timeline is not None:
        timeline_id = f"timeline:{project.id}"
        graph.add_object(
            object_type="Timeline",
            object_id=timeline_id,
            version_id=timeline.id,
            canonical_kind="DERIVED",
            status=timeline.status,
            table=TimelineVersion.__tablename__,
            row_id=timeline.id,
            attributes={
                "version": timeline.version,
                "duration_ms": timeline.duration_ms,
                "baseline_hash": timeline.baseline_hash,
            },
        )
        graph.add_edge(
            "Project",
            project.id,
            "Timeline",
            timeline_id,
            "CURRENT_DERIVED_TIMELINE",
            inferred=False,
            evidence="projects.current_timeline_version_id",
        )
        items = list(
            session.scalars(
                select(TimelineItem)
                .where(TimelineItem.timeline_id == timeline.id)
                .order_by(TimelineItem.ordinal)
            )
        )
        for item in items:
            graph.add_object(
                object_type="TimelineClip",
                object_id=item.id,
                version_id=timeline.id,
                canonical_kind="DERIVED",
                status=timeline.status,
                table=TimelineItem.__tablename__,
                row_id=item.id,
                attributes={
                    "representation": "LEGACY_SHOT_ITEM",
                    "ordinal": item.ordinal,
                    "shot_id": item.shot_id,
                    "take_id": item.take_id,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                },
            )
            graph.add_edge(
                "Timeline",
                timeline_id,
                "TimelineClip",
                item.id,
                "CONTAINS_CLIP",
                inferred=False,
                evidence="timeline_items.timeline_id",
            )
            graph.add_edge(
                "Take",
                item.take_id,
                "TimelineClip",
                item.id,
                "USED_BY_TIMELINE",
                inferred=False,
                evidence="timeline_items.take_id",
            )
        clips = list(
            session.scalars(
                select(TimelineClip)
                .where(TimelineClip.timeline_id == timeline.id)
                .order_by(TimelineClip.track_id, TimelineClip.ordinal)
            )
        )
        for clip in clips:
            graph.add_object(
                object_type="TimelineClip",
                object_id=clip.id,
                version_id=timeline.id,
                canonical_kind="DERIVED",
                status="SUSPECT" if clip.degraded else timeline.status,
                table=TimelineClip.__tablename__,
                row_id=clip.id,
                attributes={
                    "representation": "MULTITRACK_CLIP",
                    "track_id": clip.track_id,
                    "ordinal": clip.ordinal,
                    "source_entity_type": clip.source_entity_type,
                    "source_entity_id": clip.source_entity_id,
                    "asset_id": clip.asset_id,
                    "start_ms": clip.start_ms,
                    "end_ms": clip.end_ms,
                },
            )
            graph.add_edge(
                "Timeline",
                timeline_id,
                "TimelineClip",
                clip.id,
                "CONTAINS_CLIP",
                inferred=False,
                evidence="timeline_clips.timeline_id",
            )
            source_type = {
                "SHOT": "Shot",
                "TAKE": "Take",
            }.get(clip.source_entity_type.upper())
            if source_type:
                graph.add_edge(
                    source_type,
                    clip.source_entity_id,
                    "TimelineClip",
                    clip.id,
                    "USED_BY_TIMELINE",
                    inferred=False,
                    evidence="timeline_clips.source_entity_type/source_entity_id",
                )
            if clip.asset_id:
                graph.add_edge(
                    "Asset",
                    clip.asset_id,
                    "TimelineClip",
                    clip.id,
                    "USED_BY_TIMELINE",
                    inferred=False,
                    evidence="timeline_clips.asset_id",
                )

    if not script_scenes:
        graph.warnings.append(
            FilmIRWarning(
                code="SCRIPT_SCENE_LINEAGE_UNAVAILABLE",
                message="项目当前没有可投影的 ScriptVersion / ScriptScene。",
            )
        )
    if scripts and not specs:
        graph.warnings.append(
            FilmIRWarning(
                code="SCRIPT_SCENE_TO_SCENE_LINEAGE_UNAVAILABLE",
                message="尚无 ShotSpec，ScriptScene 与生产 Scene 之间没有显式映射。",
            )
        )
    return graph.build()
