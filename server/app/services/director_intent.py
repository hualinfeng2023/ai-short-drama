import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    EpisodeOutlineVersion,
    Project,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryBibleVersion,
)
from app.domain.director_intent import (
    DirectorIntent,
    DirectorIntentCandidateTarget,
    DirectorIntentChangePreview,
    DirectorIntentCompilationOutput,
    DirectorIntentDirective,
    DirectorIntentEntityRef,
    DirectorIntentEvidence,
    DirectorIntentInheritanceTarget,
    DirectorIntentPreviewSection,
    DirectorIntentScope,
    DirectorIntentTimeRange,
)
from app.services.projects import content_hash
from app.services.text_provider import generate_director_intent_compilation
from app.services.workspace import project_or_404


@dataclass(frozen=True)
class DirectorIntentContext:
    project: Project
    script: ScriptVersion
    scene: ScriptScene
    scope: DirectorIntentScope
    evidence: list[DirectorIntentEvidence]
    facts: dict[str, Any]


@dataclass(frozen=True)
class PreparedDirectorIntent:
    preview: DirectorIntentChangePreview
    confirmation_token: str
    provider: dict[str, object]


def _error(status: int, code: str, message: str, **details: object) -> HTTPException:
    detail: dict[str, object] = {"code": code, "message": message}
    if details:
        detail["details"] = details
    return HTTPException(status_code=status, detail=detail)


def _bounded_claim(value: object, *, limit: int = 1000) -> str:
    """Keep evidence readable without rejecting valid, verbose canonical source data."""

    normalized = str(value).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def _resolve_script_scene(
    session: Session,
    *,
    project: Project,
    target_type: str,
    target_id: str,
) -> tuple[ScriptVersion, ScriptScene]:
    script_scene: ScriptScene | None = None
    if target_type == "SCRIPT_SCENE":
        script_scene = session.get(ScriptScene, target_id)
    elif target_type == "SCENE":
        production_scene = session.get(Scene, target_id)
        if production_scene is None:
            raise _error(404, "DIRECTOR_TARGET_NOT_FOUND", "目标 Scene 不存在")
        shot_spec = session.scalar(
            select(ShotSpec)
            .join(Shot, ShotSpec.shot_id == Shot.id)
            .where(Shot.scene_id == production_scene.id)
            .order_by(ShotSpec.ordinal)
            .limit(1)
        )
        script_scene = (
            session.get(ScriptScene, shot_spec.script_scene_id)
            if shot_spec is not None
            else None
        )
        if script_scene is None:
            raise _error(
                409,
                "SCENE_SCRIPT_LINEAGE_MISSING",
                "该生产 Scene 尚未通过 ShotSpec 映射到 ScriptScene",
            )
    else:
        raise _error(422, "DIRECTOR_TARGET_TYPE_INVALID", "Director 目标类型无效")
    script = (
        session.get(ScriptVersion, script_scene.script_version_id)
        if script_scene is not None
        else None
    )
    if script is None or script_scene is None or script.project_id != project.id:
        raise _error(404, "DIRECTOR_TARGET_NOT_FOUND", "目标场景不属于当前项目")
    return script, script_scene


def _script_payload(script: ScriptVersion) -> dict[str, Any]:
    try:
        payload = json.loads(script.payload_json)
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid persisted canonical data
        raise _error(409, "SCRIPT_PAYLOAD_INVALID", "当前剧本结构无效，无法解析导演意图") from exc
    if not isinstance(payload, dict):
        raise _error(409, "SCRIPT_PAYLOAD_INVALID", "当前剧本结构无效，无法解析导演意图")
    return payload


def _scene_payload(payload: dict[str, Any], ordinal: int) -> dict[str, Any]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or ordinal < 1 or ordinal > len(scenes):
        return {}
    candidate = scenes[ordinal - 1]
    return candidate if isinstance(candidate, dict) else {}


def _scene_bounds(
    session: Session,
    *,
    script: ScriptVersion,
    scene: ScriptScene,
) -> tuple[int, int]:
    preceding = list(
        session.scalars(
            select(ScriptScene)
            .where(
                ScriptScene.script_version_id == script.id,
                ScriptScene.ordinal < scene.ordinal,
            )
            .order_by(ScriptScene.ordinal)
        )
    )
    start_ms = sum(item.duration_ms for item in preceding)
    return start_ms, start_ms + scene.duration_ms


def _scene_semantic_payload(
    session: Session,
    *,
    scene: ScriptScene,
    payload: dict[str, Any],
) -> dict[str, Any]:
    lines = list(
        session.scalars(
            select(ScriptLine)
            .where(ScriptLine.script_scene_id == scene.id)
            .order_by(ScriptLine.ordinal)
        )
    )
    return {
        "ordinal": scene.ordinal,
        "heading": scene.heading,
        "location": scene.location,
        "time_of_day": scene.time_of_day,
        "purpose": scene.purpose,
        "emotion": scene.emotion,
        "duration_ms": scene.duration_ms,
        "bgm_intent": scene.bgm_intent,
        "sfx_intents": json.loads(scene.sfx_intent_json),
        "character_goals": payload.get("character_goals", []),
        "lines": [
            {
                "ordinal": line.ordinal,
                "speaker_key": line.speaker_key,
                "text": line.text,
                "line_type": line.line_type,
                "emotion": line.emotion,
                "speech_rate": line.speech_rate,
                "pause_after_ms": line.pause_after_ms,
                "estimated_duration_ms": line.estimated_duration_ms,
            }
            for line in lines
        ],
    }


def _beat_rows(payload: dict[str, Any], scene_ordinal: int) -> list[dict[str, Any]]:
    engine = payload.get("short_drama_engine")
    beats = engine.get("beats") if isinstance(engine, dict) else None
    if not isinstance(beats, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict) or beat.get("scene_ordinal") != scene_ordinal:
            continue
        at_ms = beat.get("at_ms")
        if not isinstance(at_ms, int):
            continue
        rows.append(
            {
                **beat,
                "sequence": int(beat.get("sequence") or index),
                "at_ms": at_ms,
            }
        )
    return sorted(rows, key=lambda item: (int(item["at_ms"]), int(item["sequence"])))


def _goal_rows(scene_payload: dict[str, Any]) -> list[dict[str, str]]:
    goals = scene_payload.get("character_goals")
    if not isinstance(goals, list):
        return []
    required = ("character_key", "objective", "obstacle", "stakes", "tactic")
    rows: list[dict[str, str]] = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        normalized = {key: str(goal.get(key) or "").strip() for key in required}
        if all(normalized.values()):
            rows.append(normalized)
    return rows


def _shot_facts(session: Session, *, script_scene_id: str) -> list[dict[str, object]]:
    specs = list(
        session.scalars(
            select(ShotSpec)
            .where(ShotSpec.script_scene_id == script_scene_id)
            .order_by(ShotSpec.ordinal)
        )
    )
    return [
        {
            "id": item.id,
            "ordinal": item.ordinal,
            "description": item.description,
            "duration_ms": item.duration_ms,
            "shot_size": item.shot_size,
            "camera_movement": item.camera_movement,
            "content_hash": item.content_hash,
        }
        for item in specs
    ]


def _story_bible_facts(
    session: Session,
    *,
    script: ScriptVersion,
) -> tuple[StoryBibleVersion | None, dict[str, Any]]:
    outline = session.get(EpisodeOutlineVersion, script.outline_version_id)
    bible = (
        session.get(StoryBibleVersion, outline.story_bible_version_id)
        if outline is not None
        else None
    )
    if bible is None:
        return None, {}
    try:
        payload = json.loads(bible.payload_json)
    except json.JSONDecodeError:
        return bible, {}
    return bible, payload if isinstance(payload, dict) else {}


def _selection_candidates(
    beats: list[dict[str, Any]],
    *,
    selection: DirectorIntentTimeRange | None,
    scene_start_ms: int,
    scene_end_ms: int,
) -> list[dict[str, Any]]:
    if selection is None:
        return beats
    candidates: list[dict[str, Any]] = []
    for index, beat in enumerate(beats):
        beat_start = max(scene_start_ms, int(beat["at_ms"]))
        beat_end = (
            min(scene_end_ms, int(beats[index + 1]["at_ms"]))
            if index + 1 < len(beats)
            else scene_end_ms
        )
        if selection.start_ms < beat_end and selection.end_ms > beat_start:
            candidates.append(beat)
    return candidates


def resolve_director_intent_context(
    session: Session,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    selection: DirectorIntentTimeRange | None = None,
) -> DirectorIntentContext:
    project = project_or_404(session, project_id)
    script, scene = _resolve_script_scene(
        session,
        project=project,
        target_type=target_type,
        target_id=target_id,
    )
    payload = _script_payload(script)
    scene_payload = _scene_payload(payload, scene.ordinal)
    semantic_scene = _scene_semantic_payload(session, scene=scene, payload=scene_payload)
    scene_start_ms, scene_end_ms = _scene_bounds(session, script=script, scene=scene)
    if selection is not None and (
        selection.start_ms < scene_start_ms or selection.end_ms > scene_end_ms
    ):
        raise _error(
            422,
            "DIRECTOR_INTENT_RANGE_OUTSIDE_SCENE",
            "选择的时间范围超出目标场景",
            scene_start_ms=scene_start_ms,
            scene_end_ms=scene_end_ms,
        )

    scene_hash = content_hash(semantic_scene)
    scene_ref = DirectorIntentEntityRef(
        type="ScriptScene",
        id=scene.id,
        version_id=script.id,
        field_path=f"scenes[{scene.ordinal - 1}]",
        content_hash=scene_hash,
    )
    evidence: list[DirectorIntentEvidence] = [
        DirectorIntentEvidence(
            evidence_id="scene-context",
            source=scene_ref,
            claim=_bounded_claim(
                f"目标场景的目的为“{scene.purpose}”，情绪为“{scene.emotion}”。"
            ),
            confidence=1.0,
            time_range=DirectorIntentTimeRange(
                start_ms=scene_start_ms,
                end_ms=scene_end_ms,
            ),
        )
    ]
    story_bible, story_bible_payload = _story_bible_facts(session, script=script)
    world_rules = story_bible_payload.get("rules")
    continuity_rules = story_bible_payload.get("continuity_rules")
    normalized_world_rules = [
        str(item)
        for collection in (world_rules, continuity_rules)
        if isinstance(collection, list)
        for item in collection
        if str(item).strip()
    ]
    if story_bible is not None and normalized_world_rules:
        evidence.append(
            DirectorIntentEvidence(
                evidence_id="world-rules",
                source=DirectorIntentEntityRef(
                    type="StoryBible",
                    id=story_bible.id,
                    version_id=story_bible.id,
                    field_path="rules",
                    content_hash=story_bible.content_hash,
                ),
                claim=_bounded_claim("；".join(normalized_world_rules)),
                confidence=1.0,
            )
        )

    beats = _beat_rows(payload, scene.ordinal)
    beat_candidates = _selection_candidates(
        beats,
        selection=selection,
        scene_start_ms=scene_start_ms,
        scene_end_ms=scene_end_ms,
    )
    selected_beat = beat_candidates[0] if len(beat_candidates) == 1 else None
    beat_refs: list[DirectorIntentEntityRef] = []
    for beat in beat_candidates:
        sequence = int(beat["sequence"])
        beat_ref = DirectorIntentEntityRef(
            type="Beat",
            id=f"{script.id}:{sequence}",
            version_id=script.id,
            field_path=f"short_drama_engine.beats[{sequence - 1}]",
            content_hash=content_hash(beat),
        )
        beat_refs.append(beat_ref)
        evidence.append(
            DirectorIntentEvidence(
                evidence_id=f"beat-{sequence}",
                source=beat_ref,
                claim=_bounded_claim(
                    beat.get("description") or beat.get("summary") or "当前剧情节拍"
                ),
                confidence=1.0,
                time_range=None,
            )
        )

    goals = _goal_rows(scene_payload)
    goal_refs: list[DirectorIntentEntityRef] = []
    for index, goal in enumerate(goals):
        goal_ref = DirectorIntentEntityRef(
            type="CharacterGoal",
            id=f"character-goal:{script.id}:{scene.ordinal}:{goal['character_key']}",
            version_id=script.id,
            field_path=f"scenes[{scene.ordinal - 1}].character_goals[{index}]",
            content_hash=content_hash(goal),
        )
        goal_refs.append(goal_ref)
        evidence.append(
            DirectorIntentEvidence(
                evidence_id=f"goal-{goal['character_key']}",
                source=goal_ref,
                claim=_bounded_claim(
                    f"{goal['character_key']} 的当前目标是“{goal['objective']}”，"
                    f"阻碍是“{goal['obstacle']}”。"
                ),
                confidence=1.0,
                time_range=DirectorIntentTimeRange(
                    start_ms=scene_start_ms,
                    end_ms=scene_end_ms,
                ),
            )
        )

    if len(beat_candidates) > 1:
        resolution_status = "AMBIGUOUS"
        resolution_reason = "所选范围覆盖多个剧情节拍，请缩小时间范围或选择具体情节点。"
    elif selected_beat is None:
        resolution_status = "UNRESOLVED"
        resolution_reason = "当前场景没有可唯一解析的剧情节拍。"
    elif not goal_refs:
        resolution_status = "UNRESOLVED"
        resolution_reason = "当前 canonical ScriptScene 尚未定义角色级目标。"
    else:
        resolution_status = "RESOLVED"
        resolution_reason = "已由当前 ScriptVersion 唯一解析场景、剧情节拍、角色目标和时间范围。"

    if selected_beat is not None:
        selected_index = beats.index(selected_beat)
        next_at = (
            int(beats[selected_index + 1]["at_ms"])
            if selected_index + 1 < len(beats)
            else scene_end_ms
        )
        resolved_range = selection or DirectorIntentTimeRange(
            start_ms=max(scene_start_ms, int(selected_beat["at_ms"])),
            end_ms=min(scene_end_ms, max(int(selected_beat["at_ms"]) + 1, next_at)),
        )
    else:
        resolved_range = selection or DirectorIntentTimeRange(
            start_ms=scene_start_ms,
            end_ms=scene_end_ms,
        )

    fingerprint_payload = {
        "scene": semantic_scene,
        "beat": selected_beat,
        "goals": goals,
        "time_range": resolved_range.model_dump(mode="json"),
        "world_rules": normalized_world_rules,
    }
    shot_facts = _shot_facts(session, script_scene_id=scene.id)
    context_fingerprint = content_hash(fingerprint_payload)
    scope = DirectorIntentScope(
        resolution_status=resolution_status,
        scene=scene_ref,
        plot_beat=(
            beat_refs[0] if selected_beat is not None and len(beat_refs) == 1 else None
        ),
        character_goals=goal_refs,
        time_range=resolved_range,
        candidate_targets=[
            DirectorIntentCandidateTarget(
                target=ref,
                reason="该情节点与当前选择的时间范围重叠。",
                confidence=1.0,
            )
            for ref in beat_refs
        ]
        if resolution_status == "AMBIGUOUS"
        else [],
        resolution_reason=resolution_reason,
        context_fingerprint=context_fingerprint,
    )
    return DirectorIntentContext(
        project=project,
        script=script,
        scene=scene,
        scope=scope,
        evidence=evidence,
        facts={
            "scene": semantic_scene,
            "beat": selected_beat,
            "character_goals": goals,
            "time_range": resolved_range.model_dump(mode="json"),
            "shots": shot_facts,
            "world_rules": normalized_world_rules,
            "script_version_id": script.id,
            "script_version": script.version,
            "project_lock_version": project.lock_version,
        },
    )


def _blocked_compilation(context: DirectorIntentContext) -> DirectorIntentCompilationOutput:
    evidence_ref = context.evidence[0].evidence_id
    return DirectorIntentCompilationOutput(
        directives=[
            DirectorIntentDirective(
                channel=channel,
                status="UNRESOLVED",
                instruction="作用范围尚未唯一解析，不能生成该通道的导演指令。",
                observable_effect="当前通道保持不变。",
                confidence=0,
                evidence_refs=[evidence_ref],
            )
            for channel in ("NARRATIVE", "CAMERA", "PERFORMANCE", "SOUND", "PACING")
        ],
        rationale="必须先唯一解析剧情节拍、角色目标和时间范围，才能生成导演意图。",
        overall_confidence=0,
        conflict_checks=[
            {
                "code": "INTENT_SCOPE_UNRESOLVED",
                "category": "SCOPE",
                "severity": "BLOCKING",
                "status": "FAIL",
                "message": context.scope.resolution_reason,
                "evidence_refs": [evidence_ref],
            }
        ],
    )


def _preview_before(channel: str, facts: dict[str, Any]) -> str:
    scene = facts.get("scene") if isinstance(facts.get("scene"), dict) else {}
    beat = facts.get("beat") if isinstance(facts.get("beat"), dict) else {}
    goals = facts.get("character_goals")
    shots = facts.get("shots")
    time_range = facts.get("time_range") if isinstance(facts.get("time_range"), dict) else {}
    if channel == "NARRATIVE":
        return (
            f"场景目的：{scene.get('purpose') or '未定义'}；"
            f"当前情节点：{beat.get('description') or beat.get('summary') or '未解析'}"
        )
    if channel == "CAMERA":
        if isinstance(shots, list) and shots:
            return "；".join(
                f"{item.get('shot_size') or '未知景别'} / "
                f"{item.get('camera_movement') or '未知运动'} / "
                f"{int(item.get('duration_ms') or 0) / 1000:g} 秒"
                for item in shots
                if isinstance(item, dict)
            )
        return "尚未生成正式分镜；摄影意图将作为后续分镜约束。"
    if channel == "PERFORMANCE":
        goal = goals[0] if isinstance(goals, list) and goals else {}
        lines = scene.get("lines") if isinstance(scene.get("lines"), list) else []
        pause_total = sum(
            int(item.get("pause_after_ms") or 0) for item in lines if isinstance(item, dict)
        )
        return (
            f"角色策略：{goal.get('tactic') or '未定义'}；"
            f"对白停顿合计：{pause_total / 1000:g} 秒"
        )
    if channel == "SOUND":
        return (
            f"背景音乐：{scene.get('bgm_intent') or '无'}；"
            f"现场音：{'、'.join(scene.get('sfx_intents') or []) or '无'}"
        )
    return (
        f"作用时间：{int(time_range.get('start_ms') or 0) / 1000:g}–"
        f"{int(time_range.get('end_ms') or 0) / 1000:g} 秒；"
        f"场景预算：{int(scene.get('duration_ms') or 0) / 1000:g} 秒"
    )


def _blocked_reasons(
    context: DirectorIntentContext,
    compilation: DirectorIntentCompilationOutput,
) -> list[str]:
    reasons: list[str] = []
    if context.scope.resolution_status != "RESOLVED":
        reasons.append(context.scope.resolution_reason)
    if compilation.overall_confidence < 0.65:
        reasons.append("导演意图总体置信度低于 0.65")
    if any(item.status == "UNRESOLVED" for item in compilation.directives):
        reasons.append("至少一个专业通道尚未解析")
    reasons.extend(
        item.message
        for item in compilation.conflict_checks
        if item.severity == "BLOCKING" and item.status != "PASS"
    )
    return list(dict.fromkeys(reasons))


async def prepare_director_intent_preview(
    session: Session,
    settings: Settings,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    instruction: str,
    selection: DirectorIntentTimeRange | None = None,
) -> PreparedDirectorIntent:
    context = resolve_director_intent_context(
        session,
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        selection=selection,
    )
    if context.scope.resolution_status == "RESOLVED":
        generated = await generate_director_intent_compilation(
            settings,
            context=context.facts,
            instruction=instruction,
            evidence_ids={item.evidence_id for item in context.evidence},
        )
        compilation = DirectorIntentCompilationOutput.model_validate(generated.payload)
        provider = {
            "provider": generated.provider,
            "model": generated.model,
            "request_id": generated.request_id,
            "repair_attempts": generated.repair_attempts,
        }
    else:
        compilation = _blocked_compilation(context)
        provider = {
            "provider": "deterministic",
            "model": "director-context-resolver-v1",
            "request_id": None,
            "repair_attempts": 0,
        }
    blocked_reasons = _blocked_reasons(context, compilation)
    can_confirm = not blocked_reasons
    intent = DirectorIntent(
        intent_id=str(uuid4()),
        intent_version=1,
        project_id=project_id,
        source_request=instruction,
        scope=context.scope,
        evidence=context.evidence,
        directives=compilation.directives,
        rationale=compilation.rationale,
        overall_confidence=compilation.overall_confidence,
        conflict_checks=compilation.conflict_checks,
        inheritance_targets=[
            DirectorIntentInheritanceTarget(
                consumer="STORYBOARD",
                status="PENDING",
                inherited_fields=["NARRATIVE", "CAMERA", "PERFORMANCE", "PACING"],
            ),
            DirectorIntentInheritanceTarget(
                consumer="PROMPT",
                status="PENDING",
                inherited_fields=["CAMERA", "PERFORMANCE"],
            ),
            DirectorIntentInheritanceTarget(
                consumer="AUDIO",
                status="PENDING",
                inherited_fields=["PERFORMANCE", "SOUND", "PACING"],
            ),
            DirectorIntentInheritanceTarget(
                consumer="TIMELINE",
                status="PENDING",
                inherited_fields=["SOUND", "PACING"],
            ),
        ],
        state="PREVIEW_READY" if can_confirm else "BLOCKED",
        can_confirm=can_confirm,
        blocked_reasons=blocked_reasons,
    )
    preview = DirectorIntentChangePreview(
        intent=intent,
        sections=[
            DirectorIntentPreviewSection(
                channel=item.channel,
                before=_preview_before(item.channel, context.facts),
                after=item.instruction,
                why=item.observable_effect,
                confidence=item.confidence,
                evidence_refs=item.evidence_refs,
            )
            for item in compilation.directives
        ],
        preserved_invariants=[
            "已锁定的角色身份、外观与关系不变",
            "Story Bible 世界规则和连续性规则不变",
            "作用范围外的场景、镜头与时间段不变",
        ],
        downstream_summary=[
            "确认后，分镜、提示词、音频和时间线按同一 intent_version 读取获准字段。",
            "当前预览不修改正式时间线，也不触发媒体生成。",
        ],
    )
    confirmation_token = content_hash(
        {
            "intent_id": intent.intent_id,
            "intent_version": intent.intent_version,
            "context_fingerprint": intent.scope.context_fingerprint,
            "preview": preview.model_dump(mode="json"),
        }
    )
    return PreparedDirectorIntent(
        preview=preview,
        confirmation_token=confirmation_token,
        provider=provider,
    )
