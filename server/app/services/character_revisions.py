from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ChangeSet, EpisodeOutlineVersion, RelationshipGraphVersion, ScriptVersion, StoryBibleVersion
from app.schemas import CharacterRevisionChanges
from app.services.events import append_event
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.relationship_graph_workflow import _create_revision_copy, _graph_or_404, _graph_payload, graph_to_read, replace_graph_payload
from app.services.text_provider import TextProviderError, _ark_json
from app.services.workspace import project_or_404


class CharacterRevisionIssue(BaseModel):
    severity: Literal["BLOCKER", "WARNING", "INFO"]
    code: str
    field: str | None = None
    message: str
    suggestion: str


class CharacterRevisionAIReview(BaseModel):
    verdict: Literal["PASS", "CONFLICT"]
    summary: str
    issues: list[CharacterRevisionIssue] = Field(default_factory=list, max_length=20)
    story_sync_notes: list[str] = Field(default_factory=list, max_length=20)
    relationship_sync_notes: list[str] = Field(default_factory=list, max_length=20)


STRUCTURAL_FIELDS = {"name", "role", "dramatic_function", "desire", "fear", "secret"}


def _error(status_code: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, "details": details})


def _revision_context(
    session: Session,
    *,
    project_id: str,
    base_story_bible_id: str,
    base_relationship_graph_id: str,
    character_key: str,
    changes: CharacterRevisionChanges,
    expected_version: int,
) -> dict[str, Any]:
    project = project_or_404(session, project_id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    bible = session.get(StoryBibleVersion, base_story_bible_id)
    graph = _graph_or_404(session, base_relationship_graph_id)
    if bible is None or bible.project_id != project_id or graph.project_id != project_id:
        raise _error(404, "CHARACTER_REVISION_BASE_NOT_FOUND", "角色修改引用的故事版本不存在。")
    if graph.story_bible_version_id != bible.id:
        raise _error(409, "CHARACTER_REVISION_BASE_MISMATCH", "故事设定与关系版本不匹配，请刷新后重试。")
    payload = json.loads(bible.payload_json)
    characters = payload.get("characters")
    if not isinstance(characters, list):
        raise _error(422, "STORY_BIBLE_CHARACTERS_INVALID", "故事设定缺少结构化角色列表。")
    character = next((item for item in characters if isinstance(item, dict) and item.get("key") == character_key), None)
    if character is None:
        raise _error(404, "CHARACTER_NOT_FOUND", "当前故事设定中不存在该角色。", character_key=character_key)
    proposed = {**character, **changes.model_dump(exclude_none=True)}
    changed_fields = sorted(key for key, value in proposed.items() if character.get(key) != value)
    if not changed_fields:
        raise _error(422, "CHARACTER_REVISION_EMPTY", "角色信息没有发生变化。")
    graph_payload = _graph_payload(session, graph)
    related_edges = [edge for edge in graph_payload.edges if character_key in {edge.source_character_key, edge.target_character_key}]
    affected_outlines = session.scalar(select(func.count()).select_from(EpisodeOutlineVersion).where(EpisodeOutlineVersion.project_id == project_id)) or 0
    affected_scripts = session.scalar(select(func.count()).select_from(ScriptVersion).where(ScriptVersion.project_id == project_id)) or 0
    affected = {
        "relationship_keys": [edge.relationship_key for edge in related_edges],
        "relationship_count": len(related_edges),
        "outline_count": affected_outlines,
        "script_count": affected_scripts,
        "regenerate_asset_types": ["角色关系", "分集大纲", "剧本", "分镜"],
        "preserved_asset_types": ["项目简报", "已确认故事方向", "历史版本"],
    }
    signature = {
        "project_id": project_id,
        "project_version": project.lock_version,
        "base_story_bible_id": bible.id,
        "base_story_bible_hash": bible.content_hash,
        "base_relationship_graph_id": graph.id,
        "base_relationship_graph_hash": graph.content_hash,
        "character_key": character_key,
        "changes": changes.model_dump(exclude_none=True),
        "changed_fields": changed_fields,
        "affected": affected,
    }
    return {
        "project": project,
        "bible": bible,
        "graph": graph,
        "story_bible": payload,
        "relationship_graph": graph_payload.model_dump(mode="json"),
        "original_character": character,
        "proposed_character": proposed,
        "changed_fields": changed_fields,
        "affected": affected,
        "impact_hash": content_hash(signature),
    }


def _rules_review(context: dict[str, Any]) -> CharacterRevisionAIReview:
    structural = sorted(set(context["changed_fields"]) & STRUCTURAL_FIELDS)
    issues: list[CharacterRevisionIssue] = []
    if structural:
        issues.append(CharacterRevisionIssue(
            severity="BLOCKER",
            code="CHARACTER_STORY_LOGIC_IMPACT",
            field="、".join(structural),
            message="修改涉及角色动机或叙事功能，现有剧情推进不能继续原样使用。",
            suggestion="创建同步修改版，并重新确认相关人物关系后生成新剧本。",
        ))
    if context["affected"]["relationship_count"]:
        issues.append(CharacterRevisionIssue(
            severity="WARNING",
            code="CHARACTER_RELATIONSHIP_IMPACT",
            message=f"该角色参与 {context['affected']['relationship_count']} 条人物关系，需要重新核对关系描述与变化节拍。",
            suggestion="系统将复制相关关系为可编辑草稿，不覆盖已批准版本。",
        ))
    return CharacterRevisionAIReview(
        verdict="CONFLICT" if any(issue.severity == "BLOCKER" for issue in issues) else "PASS",
        summary="角色修改需要通过版本化流程同步故事设定与人物关系。" if issues else "未发现明显故事逻辑冲突。",
        issues=issues,
        story_sync_notes=["创建新的故事设定版本", "旧分集大纲与剧本保留为历史版本", "关系确认后重新生成受影响故事资产"],
        relationship_sync_notes=[f"重新核对关系：{key}" for key in context["affected"]["relationship_keys"]],
    )


async def review_character_revision(
    session: Session,
    settings: Settings,
    **kwargs: Any,
) -> dict[str, Any]:
    context = _revision_context(session, **kwargs)
    fallback = _rules_review(context)
    provider = "rules"
    model = "character-consistency-v1"
    review = fallback
    if settings.ark_api_key:
        prompt = (
            "你是短剧角色连续性审核员。判断角色修改是否与当前故事世界、角色动机和人物关系冲突。"
            "只返回 JSON，不要改写用户没有修改的事实。BLOCKER 表示必须同步修改故事或关系；"
            "WARNING 表示需要人工核对；PASS 也必须给出同步说明。输出符合 JSON Schema：\n"
            f"{json.dumps(CharacterRevisionAIReview.model_json_schema(), ensure_ascii=False)}\n"
            f"Story Bible:\n{json.dumps(context['story_bible'], ensure_ascii=False)}\n"
            f"Relationship Graph:\n{json.dumps(context['relationship_graph'], ensure_ascii=False)}\n"
            f"Original Character:\n{json.dumps(context['original_character'], ensure_ascii=False)}\n"
            f"Proposed Character:\n{json.dumps(context['proposed_character'], ensure_ascii=False)}"
        )
        try:
            result = await _ark_json(settings, prompt=prompt, validator=CharacterRevisionAIReview)
            review = CharacterRevisionAIReview.model_validate(result.payload)
            provider, model = result.provider, result.model
        except TextProviderError:
            provider, model = "rules-fallback", "character-consistency-v1"
    return {
        "base_story_bible_id": context["bible"].id,
        "base_relationship_graph_id": context["graph"].id,
        "character_key": kwargs["character_key"],
        "original_character": context["original_character"],
        "proposed_character": context["proposed_character"],
        "changed_fields": context["changed_fields"],
        "affected": context["affected"],
        "impact_hash": context["impact_hash"],
        "requires_confirmation": True,
        "review": review.model_dump(mode="json"),
        "provider": provider,
        "model": model,
    }


def create_character_revision(
    session: Session,
    *,
    confirmed: bool,
    impact_hash: str,
    actor: str,
    **kwargs: Any,
) -> dict[str, Any]:
    if not confirmed:
        raise _error(409, "CHARACTER_REVISION_CONFIRMATION_REQUIRED", "必须确认影响范围后才能创建修改版。")
    context = _revision_context(session, **kwargs)
    if impact_hash != context["impact_hash"]:
        raise _error(409, "CHARACTER_REVISION_IMPACT_STALE", "影响范围已经变化，请重新审核。")
    project, source_bible, source_graph = context["project"], context["bible"], context["graph"]
    payload = context["story_bible"]
    payload["characters"] = [
        context["proposed_character"]
        if isinstance(item, dict) and item.get("key") == kwargs["character_key"]
        else item
        for item in payload["characters"]
    ]
    now = datetime.now(UTC)
    bible_version = (session.scalar(select(func.max(StoryBibleVersion.version)).where(StoryBibleVersion.project_id == project.id)) or 0) + 1
    bible = StoryBibleVersion(
        id=str(uuid4()), project_id=project.id, story_version_id=source_bible.story_version_id,
        version=bible_version, status="DRAFT", payload_json=canonical_json(payload),
        critic_json=canonical_json({"character_revision": {"character_key": kwargs["character_key"], "changed_fields": context["changed_fields"]}}),
        content_hash=content_hash(payload), parent_version_id=source_bible.id,
        schema_version="story-bible-v2", provider="manual", model="character-revision-v1",
        config_version=source_bible.config_version, approved_at=None, approved_by=None, created_at=now,
    )
    session.add(bible)
    session.flush()
    revision_graph = _create_revision_copy(session, source=source_graph, actor=actor, note=f"角色 {kwargs['character_key']} 信息修改")
    graph_payload = _graph_payload(session, revision_graph)
    affected_keys = set(context["affected"]["relationship_keys"])
    for edge in graph_payload.edges:
        if edge.relationship_key in affected_keys:
            edge.locked = False
    graph_payload.generation_notes.append("角色信息已修改；请重新核对相关关系，确认后再生成故事线。")
    revision_graph.story_bible_version_id = bible.id
    revision_graph.provider = "manual"
    revision_graph.model = "character-synced-revision-v1"
    replace_graph_payload(session, revision_graph, graph_payload)
    graph_critic = json.loads(revision_graph.critic_json)
    revision_graph.critic_json = canonical_json(
        {
            **graph_critic,
            "generation_notes": graph_payload.generation_notes,
            "character_revision": {
                "character_key": kwargs["character_key"],
                "changed_fields": context["changed_fields"],
                "impact_hash": impact_hash,
            },
        }
    )
    source_bible.status = "SUPERSEDED"
    source_graph.status = "SUPERSEDED"
    change_set = ChangeSet(
        id=str(uuid4()), project_id=project.id, base_timeline_id=None,
        base_relationship_graph_id=source_graph.id,
        scope_json=canonical_json({"type": "CHARACTER", "ids": [kwargs["character_key"]]}),
        instruction=f"修改角色 {kwargs['character_key']} 并同步故事与关系基线",
        impact_json=canonical_json({"impact_hash": impact_hash, "affected": context["affected"]}),
        estimate_json=canonical_json({"points": 0, "seconds": 0}), status="CONFIRMED",
        result_timeline_id=None, result_relationship_graph_id=revision_graph.id, created_at=now,
    )
    session.add(change_set)
    project.lock_version += 1
    project.preview_approved = False
    project.export_ready = False
    project.updated_at = now
    append_event(session, project_id=project.id, event_type="story_bible.character_revision_confirmed", payload={"character_key": kwargs["character_key"], "story_bible_id": bible.id, "relationship_graph_id": revision_graph.id, "change_set_id": change_set.id})
    session.commit()
    return {
        "story_bible": {"id": bible.id, "version": bible.version, "status": bible.status, "payload": payload, "content_hash": bible.content_hash},
        "relationship_graph": graph_to_read(session, revision_graph, project=project),
        "change_set": {"id": change_set.id, "status": change_set.status, "impact": context["affected"]},
    }
