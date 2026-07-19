from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import RelationshipEdge, StoryBibleVersion
from app.schemas import (
    RelationshipUpbringingSuggestionRead,
    RelationshipUpbringingSuggestionRequest,
)
from app.services.relationship_graph_workflow import (
    _graph_or_404,
    _http_error,
    _story_bible_payload,
)
from app.services.text_provider import TextProviderError, _ark_json


class UpbringingSuggestionOutput(BaseModel):
    suggestion: str = Field(min_length=20, max_length=1000)


RELATION_LABELS = {
    "UNSPECIFIED": "尚未明确来源的亲属关系",
    "BIOLOGICAL_PARENT_CHILD": "亲生父母与子女关系",
    "BIOLOGICAL_GRANDPARENT_GRANDCHILD": "亲生祖辈与孙辈关系",
    "FULL_SIBLINGS": "同父同母的兄弟姐妹关系",
    "PATERNAL_HALF_SIBLINGS": "同父异母的兄弟姐妹关系",
    "MATERNAL_HALF_SIBLINGS": "同母异父的兄弟姐妹关系",
    "IDENTICAL_TWINS": "同卵双胞胎关系",
    "FRATERNAL_TWINS": "异卵双胞胎关系",
    "ADOPTIVE_PARENT_CHILD": "养父母与养子女关系",
    "STEP_PARENT_CHILD": "继父母与继子女关系",
    "IN_LAW": "姻亲关系",
    "OTHER_NON_BIOLOGICAL": "其他非血缘亲属关系",
}

UPBRINGING_LABELS = {
    "SAME_HOUSEHOLD": "长期在同一家庭环境中生活",
    "PARTIAL": "只有部分成长阶段共同生活",
    "SEPARATE": "主要在不同环境中成长",
    "UNKNOWN": "共同成长环境尚未确认",
}

CHARACTER_FACT_FIELDS = (
    "key",
    "name",
    "role",
    "age",
    "occupation",
    "personality",
    "dramatic_function",
    "desire",
    "fear",
    "secret",
)


def _character_facts(story_bible: dict[str, object], character_key: str) -> dict[str, object]:
    characters = story_bible.get("characters")
    if not isinstance(characters, list):
        return {"key": character_key, "name": character_key}
    character = next(
        (
            item
            for item in characters
            if isinstance(item, dict) and item.get("key") == character_key
        ),
        None,
    )
    if character is None:
        return {"key": character_key, "name": character_key}
    return {
        field: character[field]
        for field in CHARACTER_FACT_FIELDS
        if field in character and character[field] not in (None, "")
    }


def relationship_upbringing_context(
    session: Session,
    *,
    graph_id: str,
    relationship_key: str,
    payload: RelationshipUpbringingSuggestionRequest,
) -> dict[str, Any]:
    graph = _graph_or_404(session, graph_id)
    edge = session.scalar(
        select(RelationshipEdge).where(
            RelationshipEdge.graph_version_id == graph.id,
            RelationshipEdge.relationship_key == relationship_key,
        )
    )
    if edge is None:
        raise _http_error(
            404,
            "RELATIONSHIP_NOT_FOUND",
            "当前关系不存在，无法生成成长经历说明。",
            user_action="刷新关系网后重新选择关系",
            details={"graph_id": graph_id, "relationship_key": relationship_key},
        )
    bible = session.get(StoryBibleVersion, graph.story_bible_version_id)
    if bible is None:
        raise _http_error(
            404,
            "STORY_BIBLE_NOT_FOUND",
            "当前关系缺少对应的角色设定，无法生成成长经历说明。",
            user_action="返回故事工作区并刷新",
        )
    story_bible = _story_bible_payload(bible)
    return {
        "relationship_key": relationship_key,
        "source_character": _character_facts(story_bible, edge.source_character_key),
        "target_character": _character_facts(story_bible, edge.target_character_key),
        "family_kinship": payload.family_kinship.model_dump(mode="json"),
        "surface_relationship": payload.surface_relationship,
        "true_relationship": payload.true_relationship,
    }


def _local_upbringing_suggestion(context: dict[str, Any]) -> str:
    kinship = context["family_kinship"]
    source = context["source_character"]
    target = context["target_character"]
    source_name = str(source.get("name") or source.get("key") or "角色一")
    target_name = str(target.get("name") or target.get("key") or "角色二")
    relation = RELATION_LABELS.get(
        str(kinship.get("relation_type")),
        "亲属关系",
    )
    shared_upbringing = UPBRINGING_LABELS.get(
        str(kinship.get("shared_upbringing")),
        "共同成长环境尚未确认",
    )
    current = str(kinship.get("upbringing_context") or "").strip().rstrip("。")
    surface = str(context["surface_relationship"]).strip().rstrip("。")
    truth = str(context["true_relationship"]).strip().rstrip("。")
    if current:
        basis = f"现有说明是：{current}"
    else:
        basis = f"当前已知的相处状态是：{surface}"
    suggestion = (
        f"{source_name}与{target_name}属于{relation}，{shared_upbringing}。"
        f"{basis}；这段成长背景延续到当下，表现为{surface}，"
        f"更深层的关系背景是{truth}。"
    )
    return suggestion[:1000]


def _upbringing_prompt(context: dict[str, Any]) -> str:
    return (
        "你是短剧人物关系编辑。请为当前亲属关系生成一段“成长经历说明”。\n"
        "要求：\n"
        "1. 使用自然、专业的简体中文，控制在 60 至 160 字；\n"
        "2. 只使用输入中已经明确的角色事实、亲属来源、共同成长环境和关系描述；\n"
        "3. 不新增人物、疾病、伤害、家庭变故、职业经历或重大剧情事件；\n"
        "4. 不推断或改写亲属来源；非血缘亲属不得写成亲生血缘；\n"
        "5. 共同成长环境为“尚不明确”时，必须明确写成尚待确认，不得自行补全；\n"
        "6. 说明成长背景如何延续为当前的相处方式、情绪表达或隔阂，但不要写空泛评价；\n"
        "7. 严格返回 JSON，不要 Markdown 或解释。输出必须符合以下 JSON Schema：\n"
        f"{json.dumps(UpbringingSuggestionOutput.model_json_schema(), ensure_ascii=False)}\n"
        "输入：\n"
        f"{json.dumps(context, ensure_ascii=False)}"
    )


async def generate_upbringing_suggestion(
    context: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> RelationshipUpbringingSuggestionRead:
    resolved_settings = settings or get_settings()
    if resolved_settings.ark_api_key:
        try:
            result = await _ark_json(
                resolved_settings,
                prompt=_upbringing_prompt(context),
                validator=UpbringingSuggestionOutput,
            )
            output = UpbringingSuggestionOutput.model_validate(result.payload)
            return RelationshipUpbringingSuggestionRead(
                suggestion=output.suggestion,
                provider=result.provider,
                model=result.model,
            )
        except TextProviderError as exc:
            warning = f"{exc}；已改用当前结构化设定生成本地建议。"
    else:
        warning = "ARK_API_KEY 未配置；已改用当前结构化设定生成本地建议。"
    return RelationshipUpbringingSuggestionRead(
        suggestion=_local_upbringing_suggestion(context),
        provider="local-fallback",
        model="relationship-upbringing-v1",
        warning=warning,
    )


async def suggest_relationship_upbringing(
    session: Session,
    *,
    graph_id: str,
    relationship_key: str,
    payload: RelationshipUpbringingSuggestionRequest,
    settings: Settings | None = None,
) -> RelationshipUpbringingSuggestionRead:
    context = relationship_upbringing_context(
        session,
        graph_id=graph_id,
        relationship_key=relationship_key,
        payload=payload,
    )
    return await generate_upbringing_suggestion(context, settings=settings)
