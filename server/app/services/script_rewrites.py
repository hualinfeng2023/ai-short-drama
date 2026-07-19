from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    ScriptExcerptRevision,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
)
from app.services.creative_story import revise_script
from app.services.events import append_event
from app.services.projects import version_conflict
from app.services.text_provider import (
    TextProviderError,
    generate_script_excerpt_rewrite,
)
from app.services.workspace import project_or_404


def _script_line_context(
    session: Session,
    *,
    script_id: str,
    line_id: str,
) -> tuple[ScriptVersion, ScriptScene, ScriptLine]:
    script = session.get(ScriptVersion, script_id)
    line = session.get(ScriptLine, line_id)
    scene = session.get(ScriptScene, line.script_scene_id) if line is not None else None
    if script is None or scene is None or line is None or scene.script_version_id != script.id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SCRIPT_LINE_NOT_FOUND",
                "message": "选中的剧本段落不存在",
            },
        )
    return script, scene, line


def script_excerpt_rewrite_to_read(
    revision: ScriptExcerptRevision,
) -> dict[str, object]:
    return {
        "id": revision.id,
        "project_id": revision.project_id,
        "base_script_version_id": revision.base_script_version_id,
        "base_line_id": revision.base_line_id,
        "parent_revision_id": revision.parent_revision_id,
        "applied_script_version_id": revision.applied_script_version_id,
        "episode_ordinal": revision.episode_ordinal,
        "scene_ordinal": revision.scene_ordinal,
        "line_ordinal": revision.line_ordinal,
        "version": revision.version,
        "selection_start": revision.selection_start,
        "selection_end": revision.selection_end,
        "original_text": revision.original_text,
        "proposed_text": revision.proposed_text,
        "action": revision.action,
        "custom_instruction": revision.custom_instruction,
        "tone": revision.tone,
        "rationale": revision.rationale,
        "status": revision.status,
        "provider": revision.provider,
        "model": revision.model,
        "created_at": revision.created_at,
        "applied_at": revision.applied_at,
    }


async def create_script_excerpt_rewrite(
    session: Session,
    settings: Settings,
    *,
    script_id: str,
    line_id: str,
    expected_version: int,
    selection_start: int,
    selection_end: int,
    action: str,
    custom_instruction: str | None,
    tone: str | None,
    parent_revision_id: str | None,
) -> dict[str, object]:
    script, scene, line = _script_line_context(
        session,
        script_id=script_id,
        line_id=line_id,
    )
    project = project_or_404(session, script.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if project.status != "SCRIPT_READY" or script.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_REWRITE_LOCKED",
                "message": "只能改写当前待审核剧本",
                "details": {
                    "project_status": project.status,
                    "script_status": script.status,
                },
            },
        )
    if selection_end > len(line.text):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SCRIPT_SELECTION_INVALID",
                "message": "选区已经超出台词范围，请重新选择",
            },
        )
    selected_text = line.text[selection_start:selection_end]
    if not selected_text.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SCRIPT_SELECTION_EMPTY",
                "message": "请选择包含文字的剧本片段",
            },
        )

    parent: ScriptExcerptRevision | None = None
    if parent_revision_id:
        parent = session.get(ScriptExcerptRevision, parent_revision_id)
        if (
            parent is None
            or parent.project_id != project.id
            or parent.episode_ordinal != script.episode_ordinal
            or parent.scene_ordinal != scene.ordinal
            or parent.line_ordinal != line.ordinal
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SCRIPT_REWRITE_PARENT_MISMATCH",
                    "message": "重试来源与当前台词不一致",
                },
            )

    try:
        result = await generate_script_excerpt_rewrite(
            settings,
            selected_text=selected_text,
            full_line=line.text,
            scene_context=f"{scene.heading}；场景目的：{scene.purpose}",
            action=action,
            tone=tone,
            custom_instruction=custom_instruction,
        )
    except TextProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "details": exc.details,
            },
        ) from exc

    version = (
        session.scalar(
            select(func.max(ScriptExcerptRevision.version)).where(
                ScriptExcerptRevision.project_id == project.id,
                ScriptExcerptRevision.episode_ordinal == script.episode_ordinal,
                ScriptExcerptRevision.scene_ordinal == scene.ordinal,
                ScriptExcerptRevision.line_ordinal == line.ordinal,
            )
        )
        or 0
    ) + 1
    revision = ScriptExcerptRevision(
        id=str(uuid4()),
        project_id=project.id,
        base_script_version_id=script.id,
        base_line_id=line.id,
        parent_revision_id=parent.id if parent else None,
        applied_script_version_id=None,
        episode_ordinal=script.episode_ordinal,
        scene_ordinal=scene.ordinal,
        line_ordinal=line.ordinal,
        version=version,
        selection_start=selection_start,
        selection_end=selection_end,
        original_text=selected_text,
        proposed_text=str(result.payload["rewritten_text"]),
        action=action,
        custom_instruction=custom_instruction,
        tone=tone,
        rationale=str(result.payload["rationale"]),
        status="GENERATED",
        provider=result.provider,
        model=result.model,
        created_at=datetime.now(UTC),
        applied_at=None,
    )
    session.add(revision)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="script.excerpt_rewrite.generated",
        payload={
            "revision_id": revision.id,
            "script_id": script.id,
            "line_id": line.id,
            "action": action,
            "version": version,
        },
    )
    session.commit()
    return script_excerpt_rewrite_to_read(revision)


def list_script_excerpt_rewrites(
    session: Session,
    *,
    script_id: str,
    line_id: str,
) -> list[dict[str, object]]:
    script, scene, line = _script_line_context(
        session,
        script_id=script_id,
        line_id=line_id,
    )
    revisions = session.scalars(
        select(ScriptExcerptRevision)
        .where(
            ScriptExcerptRevision.project_id == script.project_id,
            ScriptExcerptRevision.episode_ordinal == script.episode_ordinal,
            ScriptExcerptRevision.scene_ordinal == scene.ordinal,
            ScriptExcerptRevision.line_ordinal == line.ordinal,
        )
        .order_by(ScriptExcerptRevision.version.desc())
    ).all()
    return [script_excerpt_rewrite_to_read(item) for item in revisions]


def apply_script_excerpt_rewrite(
    session: Session,
    *,
    revision_id: str,
    script_id: str,
    line_id: str,
    expected_version: int,
) -> dict[str, object]:
    revision = session.get(ScriptExcerptRevision, revision_id)
    if revision is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "改写版本不存在"},
        )
    if revision.status == "APPLIED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_REWRITE_ALREADY_APPLIED",
                "message": "这个改写版本已经使用",
            },
        )
    script, scene, line = _script_line_context(
        session,
        script_id=script_id,
        line_id=line_id,
    )
    project = project_or_404(session, script.project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    same_lineage = (
        revision.project_id == project.id
        and revision.episode_ordinal == script.episode_ordinal
        and revision.scene_ordinal == scene.ordinal
        and revision.line_ordinal == line.ordinal
    )
    current_selection = line.text[revision.selection_start : revision.selection_end]
    same_source = revision.base_script_version_id == script.id and revision.base_line_id == line.id
    if not same_lineage or not same_source or current_selection != revision.original_text:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCRIPT_REWRITE_SOURCE_CHANGED",
                "message": "原文已经变化，请基于最新剧本重新选择并改写",
            },
        )

    revised_text = (
        line.text[: revision.selection_start]
        + revision.proposed_text
        + line.text[revision.selection_end :]
    )
    script_result = revise_script(
        session,
        script_id=script.id,
        expected_version=expected_version,
        scope="LINE",
        entity_id=line.id,
        changes={"text": revised_text},
    )
    revision = session.get(ScriptExcerptRevision, revision_id)
    if revision is None:
        raise RuntimeError("改写版本在应用后丢失")
    revision.status = "APPLIED"
    revision.applied_script_version_id = str(script_result["id"])
    revision.applied_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project.id,
        job_id=None,
        event_type="script.excerpt_rewrite.applied",
        payload={
            "revision_id": revision.id,
            "source_script_id": script.id,
            "script_id": str(script_result["id"]),
        },
    )
    session.commit()
    return {
        "rewrite": script_excerpt_rewrite_to_read(revision),
        "script": script_result,
    }
