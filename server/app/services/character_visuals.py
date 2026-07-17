import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    CharacterCandidateBatch,
    CharacterFamilyResemblanceConstraint,
    CharacterIdentityAsset,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    CharacterVisualProfileVersion,
    Job,
    Project,
    RelationshipEdge,
    RelationshipGraphVersion,
    StoryBibleVersion,
)
from app.schemas import JobRead
from app.services.assets import register_file
from app.services.events import append_event
from app.services.image_provider import GeneratedImage
from app.services.jobs import enqueue_job, job_to_read
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.workspace import project_or_404

DEFAULT_NEGATIVE_CONSTRAINTS = [
    "避免过度美化",
    "避免网红脸",
    "避免塑料皮肤",
    "避免身份漂移",
    "避免多余人物",
    "避免文字与水印",
    "避免肢体异常",
]

BIOLOGICAL_KINSHIP_LEVELS = {
    "BIOLOGICAL_PARENT_CHILD": "MEDIUM",
    "BIOLOGICAL_GRANDPARENT_GRANDCHILD": "LOW",
    "FULL_SIBLINGS": "MEDIUM",
    "PATERNAL_HALF_SIBLINGS": "LOW",
    "MATERNAL_HALF_SIBLINGS": "LOW",
    "FRATERNAL_TWINS": "HIGH",
    "IDENTICAL_TWINS": "VERY_HIGH",
}
SIMILARITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4}
SIMILARITY_FEATURE_COUNT = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 3}
FAMILY_TRAIT_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("brow_eye_shape", "眉眼", ("facial_features",)),
    ("nose_shape", "鼻型", ("facial_features",)),
    ("face_shape", "脸部轮廓", ()),
    ("mouth_corner", "嘴角", ("facial_features",)),
    ("skin_tone", "肤色", ()),
    ("hair_texture", "发质", ("hairstyle",)),
)
FAMILY_INDEPENDENCE_CONSTRAINTS = [
    "仅继承列出的 1～3 个家族特征，不复制参考角色整张脸",
    "保持目标角色独立的脸部比例、年龄感、体型、性别表达、性格与识别特征",
    "不得仅通过改变年龄或性别制造亲属，也不得把参考角色直接年轻化或异性化",
    "气质关联只根据家庭环境、成长经历与人物关系推导，不作为遗传结果",
]

DOSSIER_VIEWS: tuple[tuple[str, str], ...] = (
    ("FRONT", "正面头肩身份照，正视镜头"),
    ("THREE_QUARTER", "向右转 45 度的头肩身份照"),
    ("PROFILE", "标准右侧面头肩身份照"),
    ("FULL_BODY", "正面自然站立全身身份照，完整露出鞋履"),
    ("EXPRESSIONS", "基础表情组：中性、微笑、警觉、悲伤，四宫格"),
)


def _json(value: str) -> object:
    return json.loads(value)


def _as_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _age_number(value: str) -> int | None:
    match = re.search(r"(\d{1,3})", value)
    return int(match.group(1)) if match else None


def _relationship_context(
    session: Session,
    graph: RelationshipGraphVersion,
    character_key: str,
) -> list[str]:
    edges = session.scalars(
        select(RelationshipEdge)
        .where(
            RelationshipEdge.graph_version_id == graph.id,
            (
                (RelationshipEdge.source_character_key == character_key)
                | (RelationshipEdge.target_character_key == character_key)
            ),
        )
        .order_by(RelationshipEdge.ordinal)
    ).all()
    return [
        f"{item.surface_relationship}；真实定位：{item.true_relationship}；剧情功能：{item.story_function}"
        for item in edges
    ]


def _visualize_personality(personality: list[str]) -> dict[str, str]:
    source = "、".join(personality)
    expression = "克制、少量微表情"
    gaze = "稳定注视，保留环境警觉"
    posture = "脊背自然挺直，重心稳定"
    action = "动作简洁，避免夸张手势"
    if any(word in source for word in ("警觉", "防备", "敏锐")):
        gaze = "目光敏锐，先观察环境再看向对方"
        posture = "肩颈略收，身体与出口保持可移动角度"
    if any(word in source for word in ("冷静", "克制")):
        expression = "闭口中性表情，眉眼克制"
    if any(word in source for word in ("执着", "强势", "果断")):
        posture = "站姿稳定，身体正面占据空间"
        action = "说话时动作少而明确"
    return {
        "expression": expression,
        "gaze": gaze,
        "posture": posture,
        "movement": action,
        "clothing_signal": "衣物保持真实生活褶皱，以职业与阶层而非潮流标签表达身份",
        "personal_items": "只保留与职业或剧情功能直接相关的随身物品",
    }


def _profile_audit(profile: dict[str, object]) -> list[dict[str, str]]:
    identity = dict(profile["identity_fields"])
    styling = dict(profile["styling_fields"])
    project_style = dict(profile["project_style"])
    issues: list[dict[str, str]] = []
    age_text = _as_text(identity.get("age"), "")
    age = _age_number(age_text)
    if age is None:
        issues.append(
            {
                "severity": "WARNING",
                "code": "AGE_IMPRECISE",
                "message": "年龄没有可识别的数字，生图时可能出现年龄感漂移。",
                "suggestion": "补充明确年龄或窄年龄段。",
            }
        )
    elif age < 0 or age > 110:
        issues.append(
            {
                "severity": "BLOCKER",
                "code": "AGE_INVALID",
                "message": "年龄超出合理人物范围。",
                "suggestion": "修正角色年龄后重新审核。",
            }
        )
    occupation = _as_text(identity.get("occupation"), "")
    if not occupation:
        issues.append(
            {
                "severity": "BLOCKER",
                "code": "OCCUPATION_MISSING",
                "message": "职业为空，无法稳定推导服装与生活痕迹。",
                "suggestion": "补充职业或明确无业状态。",
            }
        )
    era = _as_text(identity.get("era"), "")
    project_era = _as_text(project_style.get("region_era"), "")
    if "古代" in era and any(word in project_era for word in ("当代", "现代")):
        issues.append(
            {
                "severity": "BLOCKER",
                "code": "ERA_CONFLICT",
                "message": "角色时代与项目时代冲突。",
                "suggestion": "统一角色身份时代与项目视觉年代。",
            }
        )
    forbidden = "、".join(str(item) for item in styling.get("forbidden_elements", []))
    wardrobe = _as_text(styling.get("wardrobe"), "")
    if wardrobe and any(item and item in wardrobe for item in forbidden.split("、")):
        issues.append(
            {
                "severity": "BLOCKER",
                "code": "WARDROBE_FORBIDDEN",
                "message": "服装描述包含已禁止元素。",
                "suggestion": "删除冲突造型，或调整禁止元素。",
            }
        )
    if not issues:
        issues.append(
            {
                "severity": "INFO",
                "code": "CONSISTENCY_PASSED",
                "message": "年龄、时代、职业、地域、阶层与人物关系未发现结构化冲突。",
                "suggestion": "可以确认角色基线。",
            }
        )
    return issues


def _profile_content(record: CharacterVisualProfileVersion) -> dict[str, object]:
    return {
        "identity_fields": _json(record.identity_fields_json),
        "appearance_fields": _json(record.appearance_fields_json),
        "personality_visualization": _json(record.personality_visualization_json),
        "styling_fields": _json(record.styling_fields_json),
        "project_style": _json(record.project_style_json),
        "negative_constraints": _json(record.negative_constraints_json),
        "recommended_directions": _json(record.recommended_directions_json),
        "selected_direction": record.selected_direction,
    }


def is_biological_kinship(relation_type: str) -> bool:
    return relation_type in BIOLOGICAL_KINSHIP_LEVELS


def kinship_similarity_level(relation_type: str) -> str | None:
    return BIOLOGICAL_KINSHIP_LEVELS.get(relation_type)


def _family_constraint_to_read(
    record: CharacterFamilyResemblanceConstraint,
) -> dict[str, object]:
    return {
        "id": record.id,
        "version": record.version,
        "relationship_graph_version_id": record.relationship_graph_version_id,
        "source_character_ids": _json(record.source_character_ids_json),
        "source_identity_version_ids": _json(record.source_identity_version_ids_json),
        "source_asset_ids": _json(record.source_asset_ids_json),
        "relationship_evidence": _json(record.relationship_evidence_json),
        "inherited_features": _json(record.inherited_features_json),
        "similarity_level": record.similarity_level,
        "temperament_affinity": _json(record.temperament_affinity_json),
        "independence_constraints": _json(record.independence_constraints_json),
        "status": record.status,
        "content_hash": record.content_hash,
        "created_at": record.created_at.isoformat(),
    }


def _family_temperament_affinity(
    relationships: list[tuple[RelationshipEdge, dict[str, object]]],
) -> dict[str, object]:
    upbringing_modes = {
        str(kinship.get("shared_upbringing", "UNKNOWN")) for _edge, kinship in relationships
    }
    basis = [
        {
            "relationship_key": edge.relationship_key,
            "shared_upbringing": kinship.get("shared_upbringing", "UNKNOWN"),
            "upbringing_context": kinship.get("upbringing_context"),
            "true_relationship": edge.true_relationship,
            "trust_level": edge.trust_level,
            "conflict_intensity": edge.conflict_intensity,
        }
        for edge, kinship in relationships
    ]
    tense = any(edge.conflict_intensity >= 3 or edge.trust_level < 0 for edge, _ in relationships)
    if "SAME_HOUSEHOLD" in upbringing_modes:
        level = "MEDIUM" if tense else "HIGH"
        instruction = (
            "共享家庭环境可形成相似的礼仪、警觉方式或情绪克制；关系紧张使表达方向产生分化。"
            if tense
            else "共享家庭环境可形成相近的礼仪、目光习惯和情绪表达节奏。"
        )
    elif "PARTIAL" in upbringing_modes:
        level = "LOW"
        instruction = "部分共同成长只保留轻微的行为节奏呼应，不要求相同性格。"
    elif upbringing_modes == {"SEPARATE"}:
        level = "NONE"
        instruction = "成长环境分离，不添加气质相似要求。"
    else:
        level = "NONE"
        instruction = "共同成长信息不足，不推断气质相似。"
    return {
        "level": level,
        "instruction": f"{instruction}此项来自后天环境与人物关系，不属于遗传特征。",
        "basis": basis,
    }


def _locked_family_sources(
    session: Session,
    *,
    character: Character,
    graph: RelationshipGraphVersion,
    relationships: list[tuple[RelationshipEdge, dict[str, object]]],
) -> list[tuple[Character, CharacterIdentityVersion]]:
    sources: list[tuple[Character, CharacterIdentityVersion]] = []
    seen: set[str] = set()
    for edge, _kinship in relationships:
        source_key = (
            edge.target_character_key
            if edge.source_character_key == character.character_key
            else edge.source_character_key
        )
        relative = session.scalar(
            select(Character).where(
                Character.project_id == character.project_id,
                Character.character_key == source_key,
                Character.source_relationship_graph_id == graph.id,
            )
        )
        if (
            relative is None
            or relative.id in seen
            or relative.locked_identity_version_id is None
        ):
            continue
        identity = session.get(CharacterIdentityVersion, relative.locked_identity_version_id)
        if identity is None or identity.status != "LOCKED":
            continue
        seen.add(relative.id)
        sources.append((relative, identity))
    return sources[:3]


def _extract_family_traits(
    session: Session,
    sources: list[tuple[Character, CharacterIdentityVersion]],
    *,
    maximum: int,
) -> tuple[list[dict[str, str]], list[str]]:
    traits: list[dict[str, str]] = []
    source_asset_ids: list[str] = []
    used_fields: set[str] = set()
    for relative, identity in sources:
        candidate = session.get(CharacterCandidate, identity.source_candidate_id)
        if candidate is not None and candidate.asset_id not in source_asset_ids:
            # The selected candidate is a controlled family reference,
            # never an identity-copy target.
            source_asset_ids.append(candidate.asset_id)
        stable = _json(identity.stable_traits_json)
        appearance = stable.get("appearance", {}) if isinstance(stable, dict) else {}
        if not isinstance(appearance, dict):
            appearance = {}
        for field, label, fallbacks in FAMILY_TRAIT_FIELDS:
            if field in used_fields:
                continue
            value = _as_text(appearance.get(field), "")
            if not value:
                for fallback in fallbacks:
                    value = _as_text(appearance.get(fallback), "")
                    if value:
                        break
            if not value:
                continue
            traits.append(
                {
                    "field": field,
                    "label": label,
                    "value": value,
                    "source_character_id": relative.id,
                    "source_character_name": relative.name,
                    "source_identity_version_id": identity.id,
                }
            )
            used_fields.add(field)
            if len(traits) >= maximum:
                return traits, source_asset_ids
    return traits, source_asset_ids


def refresh_family_resemblance_constraint(
    session: Session,
    *,
    character: Character,
    graph: RelationshipGraphVersion,
) -> CharacterFamilyResemblanceConstraint | None:
    edges = session.scalars(
        select(RelationshipEdge)
        .where(
            RelationshipEdge.graph_version_id == graph.id,
            (
                (RelationshipEdge.source_character_key == character.character_key)
                | (RelationshipEdge.target_character_key == character.character_key)
            ),
        )
        .order_by(RelationshipEdge.ordinal)
    ).all()
    relationships: list[tuple[RelationshipEdge, dict[str, object]]] = []
    for edge in edges:
        relationship_types = _json(edge.relationship_types_json)
        kinship = _json(edge.family_kinship_json)
        if (
            isinstance(relationship_types, list)
            and "FAMILY" in relationship_types
            and isinstance(kinship, dict)
            and is_biological_kinship(str(kinship.get("relation_type", "")))
        ):
            relationships.append((edge, kinship))

    latest = session.scalar(
        select(CharacterFamilyResemblanceConstraint)
        .where(CharacterFamilyResemblanceConstraint.character_id == character.id)
        .order_by(CharacterFamilyResemblanceConstraint.version.desc())
    )
    if not relationships:
        if latest is not None and latest.status != "SUPERSEDED":
            latest.status = "SUPERSEDED"
        return None

    levels = [
        kinship_similarity_level(str(kinship["relation_type"])) or "LOW"
        for _edge, kinship in relationships
    ]
    similarity_level = max(levels, key=lambda item: SIMILARITY_RANK[item])
    sources = _locked_family_sources(
        session,
        character=character,
        graph=graph,
        relationships=relationships,
    )
    inherited_features, source_asset_ids = _extract_family_traits(
        session,
        sources,
        maximum=SIMILARITY_FEATURE_COUNT[similarity_level],
    )
    relationship_evidence = []
    for edge, kinship in relationships:
        relative_key = (
            edge.target_character_key
            if edge.source_character_key == character.character_key
            else edge.source_character_key
        )
        relationship_evidence.append(
            {
                "relationship_key": edge.relationship_key,
                "relative_character_key": relative_key,
                "relation_type": kinship["relation_type"],
                "shared_upbringing": kinship.get("shared_upbringing", "UNKNOWN"),
                "upbringing_context": kinship.get("upbringing_context"),
            }
        )
    status = "ACTIVE" if sources and inherited_features else "WAITING_FOR_LOCKED_RELATIVE"
    temperament = _family_temperament_affinity(relationships)
    payload = {
        "relationship_graph_version_id": graph.id,
        "source_character_ids": [item[0].id for item in sources],
        "source_identity_version_ids": [item[1].id for item in sources],
        "source_asset_ids": source_asset_ids,
        "relationship_evidence": relationship_evidence,
        "inherited_features": inherited_features,
        "similarity_level": similarity_level,
        "temperament_affinity": temperament,
        "independence_constraints": FAMILY_INDEPENDENCE_CONSTRAINTS,
        "status": status,
    }
    digest = content_hash(payload)
    if latest is not None and latest.content_hash == digest and latest.status != "SUPERSEDED":
        return latest
    if latest is not None and latest.status != "SUPERSEDED":
        latest.status = "SUPERSEDED"
    version = (
        session.scalar(
            select(func.max(CharacterFamilyResemblanceConstraint.version)).where(
                CharacterFamilyResemblanceConstraint.character_id == character.id
            )
        )
        or 0
    ) + 1
    record = CharacterFamilyResemblanceConstraint(
        id=str(uuid4()),
        project_id=character.project_id,
        character_id=character.id,
        relationship_graph_version_id=graph.id,
        version=version,
        source_character_ids_json=canonical_json(payload["source_character_ids"]),
        source_identity_version_ids_json=canonical_json(payload["source_identity_version_ids"]),
        source_asset_ids_json=canonical_json(payload["source_asset_ids"]),
        relationship_evidence_json=canonical_json(payload["relationship_evidence"]),
        inherited_features_json=canonical_json(payload["inherited_features"]),
        similarity_level=similarity_level,
        temperament_affinity_json=canonical_json(temperament),
        independence_constraints_json=canonical_json(FAMILY_INDEPENDENCE_CONSTRAINTS),
        status=status,
        content_hash=digest,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    return record


def refresh_family_resemblance_constraints(
    session: Session,
    *,
    characters: list[Character],
    graph: RelationshipGraphVersion,
) -> None:
    for character in characters:
        refresh_family_resemblance_constraint(session, character=character, graph=graph)


def _new_profile(
    session: Session,
    *,
    project: Project,
    character: Character,
    bible: StoryBibleVersion,
    graph: RelationshipGraphVersion,
    profile: dict[str, object],
    source_hash: str,
    status: str = "READY_FOR_REVIEW",
) -> CharacterVisualProfileVersion:
    version = (
        session.scalar(
            select(func.max(CharacterVisualProfileVersion.version)).where(
                CharacterVisualProfileVersion.character_id == character.id
            )
        )
        or 0
    ) + 1
    issues = _profile_audit(profile)
    payload = {**profile, "conflict_report": issues}
    record = CharacterVisualProfileVersion(
        id=str(uuid4()),
        project_id=project.id,
        character_id=character.id,
        version=version,
        source_story_bible_version_id=bible.id,
        source_relationship_graph_id=graph.id,
        identity_fields_json=canonical_json(profile["identity_fields"]),
        appearance_fields_json=canonical_json(profile["appearance_fields"]),
        personality_visualization_json=canonical_json(profile["personality_visualization"]),
        styling_fields_json=canonical_json(profile["styling_fields"]),
        project_style_json=canonical_json(profile["project_style"]),
        negative_constraints_json=canonical_json(profile["negative_constraints"]),
        conflict_report_json=canonical_json(issues),
        recommended_directions_json=canonical_json(profile["recommended_directions"]),
        selected_direction=(
            str(profile["selected_direction"]) if profile.get("selected_direction") else None
        ),
        source_content_hash=source_hash,
        content_hash=content_hash(payload),
        status=status,
        confirmed_at=None,
        confirmed_by=None,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    character.current_profile_version_id = record.id
    character.source_story_bible_version_id = bible.id
    character.source_relationship_graph_id = graph.id
    return record


def prepare_character_visuals(
    session: Session,
    *,
    project: Project,
    bible: StoryBibleVersion,
    graph: RelationshipGraphVersion,
) -> list[Character]:
    payload = json.loads(bible.payload_json)
    character_payloads = payload.get("characters", [])
    if not isinstance(character_payloads, list) or not character_payloads:
        raise ValueError("角色文字设定为空")
    world = _as_text(payload.get("world"), "当代中国城市")
    now = datetime.now(UTC)
    prepared: list[Character] = []
    active_character_keys: set[str] = set()
    for raw in character_payloads:
        if not isinstance(raw, dict):
            continue
        key = _as_text(raw.get("key"), "")
        if not key:
            continue
        active_character_keys.add(key)
        relationships = _relationship_context(session, graph, key)
        source_hash = content_hash(
            {"character": raw, "relationships": relationships, "world": world}
        )
        character = session.scalar(
            select(Character).where(
                Character.project_id == project.id,
                Character.character_key == key,
            )
        )
        if character is None:
            character = Character(
                id=str(uuid4()),
                project_id=project.id,
                character_key=key,
                name=_as_text(raw.get("name"), key),
                role=_as_text(raw.get("role"), "SUPPORTING"),
                visual_brief=_as_text(raw.get("visual_notes"), "自然、可信的现实人物"),
                status="NOT_GENERATED",
                locked_candidate_id=None,
                source_story_bible_version_id=None,
                source_relationship_graph_id=None,
                current_profile_version_id=None,
                locked_identity_version_id=None,
                active_look_version_id=None,
                active_story_state_version_id=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(character)
            session.flush()
        current = (
            session.get(CharacterVisualProfileVersion, character.current_profile_version_id)
            if character.current_profile_version_id
            else None
        )
        if current is not None and current.source_content_hash == source_hash:
            prepared.append(character)
            continue
        personality = [str(item) for item in raw.get("personality", []) if str(item).strip()]
        gender = _as_text(raw.get("gender"), "unspecified")
        gender_expression = {"female": "女性表达", "male": "男性表达"}.get(
            gender, "按角色设定自然表达"
        )
        visual_notes = _as_text(raw.get("visual_notes"), "现实人物，保留生活痕迹")
        profile: dict[str, object] = {
            "identity_fields": {
                "age": _as_text(raw.get("age"), "30岁左右"),
                "gender_expression": gender_expression,
                "region": "按故事发生地域呈现，不使用刻板标签",
                "era": "当代" if any(word in world for word in ("当代", "现代", "城市")) else world,
                "occupation": _as_text(raw.get("occupation"), "待明确职业"),
                "social_class": "由职业、居住环境与服装材质综合表达",
                "story_identity": _as_text(raw.get("dramatic_function"), character.role),
                "relationship_position": "；".join(relationships) or "暂无直接人物关系冲突",
            },
            "appearance_fields": {
                "face_shape": "自然骨相，不做网红化调整",
                "facial_features": "五官比例真实，保留个体不对称",
                "brow_eye_shape": "眉眼走向自然，保留稳定辨识度",
                "nose_shape": "鼻梁与鼻翼比例真实，不做模板化塑形",
                "mouth_corner": "静止时嘴角走向自然，保留轻微不对称",
                "skin_tone": "符合地域与生活环境的自然肤色",
                "hairstyle": "符合职业和生活状态的日常发型",
                "hair_texture": "符合地域、年龄与生活环境的自然发质",
                "body_type": "与年龄和职业匹配的自然体型",
                "identifying_features": visual_notes,
                "life_marks": "保留轻微肤质、疲劳与日常使用痕迹",
            },
            "personality_visualization": _visualize_personality(personality),
            "styling_fields": {
                "wardrobe": visual_notes,
                "materials": "真实织物纹理，符合职业与阶层",
                "colors": "低饱和主色，角色之间保持可区分",
                "shoes_bags": "只使用符合行动场景的鞋包",
                "accessories": "仅保留剧情和身份需要的配饰",
                "forbidden_elements": ["夸张奢侈品牌标识", "无剧情依据的礼服", "现代网红妆容"],
            },
            "project_style": {
                "realism": _as_text(project.style, "写实电影感"),
                "region_era": world,
                "photography": "角色选角照，统一相机高度与镜头焦段",
                "lighting": "柔和中性主光，清楚保留面部骨相",
                "color": "自然肤色，低饱和电影色彩",
                "camera_language": "正面胸像使用 85mm 等效焦段，中性背景，人物居中",
            },
            "negative_constraints": list(DEFAULT_NEGATIVE_CONSTRAINTS),
            "recommended_directions": [
                {"key": "documentary", "label": "纪实可信", "reason": "突出生活痕迹与身份可信度"},
                {
                    "key": "cinematic",
                    "label": "电影克制",
                    "reason": "强化光影和情绪张力但不过度美化",
                },
                {"key": "genre", "label": "类型强化", "reason": "在写实基线内强化职业与剧情功能"},
            ],
            "selected_direction": "cinematic",
        }
        if current is not None:
            current.status = "SUPERSEDED"
        _new_profile(
            session,
            project=project,
            character=character,
            bible=bible,
            graph=graph,
            profile=profile,
            source_hash=source_hash,
        )
        character.name = _as_text(raw.get("name"), character.name)
        character.role = _as_text(raw.get("role"), character.role)
        character.visual_brief = visual_notes
        character.status = (
            "TEXT_CHANGED" if character.locked_identity_version_id else "NOT_GENERATED"
        )
        character.lock_version += 1 if current is not None else 0
        character.updated_at = now
        prepared.append(character)
    stale_characters = session.scalars(
        select(Character).where(
            Character.project_id == project.id,
            Character.character_key.not_in(active_character_keys),
        )
    ).all()
    for stale in stale_characters:
        stale.status = "SUPERSEDED"
        stale.updated_at = now
    session.flush()
    refresh_family_resemblance_constraints(session, characters=prepared, graph=graph)
    append_event(
        session,
        project_id=project.id,
        event_type="character.visual_profiles_prepared",
        payload={"character_ids": [item.id for item in prepared], "graph_id": graph.id},
    )
    session.flush()
    return prepared


def _profile_to_read(record: CharacterVisualProfileVersion) -> dict[str, object]:
    return {
        "id": record.id,
        "version": record.version,
        "status": record.status,
        **_profile_content(record),
        "conflict_report": _json(record.conflict_report_json),
        "content_hash": record.content_hash,
        "confirmed_at": record.confirmed_at,
        "confirmed_by": record.confirmed_by,
    }


def character_visual_workspace(session: Session, project_id: str) -> dict[str, object]:
    project = project_or_404(session, project_id)
    active_graph_id = session.scalar(
        select(RelationshipGraphVersion.id)
        .where(
            RelationshipGraphVersion.project_id == project_id,
            RelationshipGraphVersion.status == "APPROVED",
        )
        .order_by(RelationshipGraphVersion.version.desc())
    )
    character_query = select(Character).where(Character.project_id == project_id)
    if active_graph_id:
        character_query = character_query.where(
            Character.source_relationship_graph_id == active_graph_id
        )
    characters = session.scalars(character_query.order_by(Character.created_at)).all()
    result: list[dict[str, object]] = []
    for character in characters:
        profile = (
            session.get(CharacterVisualProfileVersion, character.current_profile_version_id)
            if character.current_profile_version_id
            else None
        )
        family_constraint = session.scalar(
            select(CharacterFamilyResemblanceConstraint)
            .where(
                CharacterFamilyResemblanceConstraint.character_id == character.id,
                CharacterFamilyResemblanceConstraint.relationship_graph_version_id
                == active_graph_id,
                CharacterFamilyResemblanceConstraint.status != "SUPERSEDED",
            )
            .order_by(CharacterFamilyResemblanceConstraint.version.desc())
        )
        candidates = session.scalars(
            select(CharacterCandidate)
            .where(
                CharacterCandidate.character_id == character.id,
                CharacterCandidate.profile_version_id == character.current_profile_version_id,
            )
            .order_by(CharacterCandidate.ordinal)
        ).all()
        batches = session.scalars(
            select(CharacterCandidateBatch)
            .where(CharacterCandidateBatch.character_id == character.id)
            .order_by(CharacterCandidateBatch.version)
        ).all()
        identities = session.scalars(
            select(CharacterIdentityVersion)
            .where(CharacterIdentityVersion.character_id == character.id)
            .order_by(CharacterIdentityVersion.version)
        ).all()
        identity_reads: list[dict[str, object]] = []
        for identity in identities:
            assets = session.scalars(
                select(CharacterIdentityAsset)
                .where(CharacterIdentityAsset.identity_version_id == identity.id)
                .order_by(CharacterIdentityAsset.created_at)
            ).all()
            identity_reads.append(
                {
                    "id": identity.id,
                    "version": identity.version,
                    "source_candidate_id": identity.source_candidate_id,
                    "profile_version_id": identity.profile_version_id,
                    "status": identity.status,
                    "content_hash": identity.content_hash,
                    "locked_at": identity.locked_at,
                    "locked_by": identity.locked_by,
                    "assets": [
                        {
                            "id": item.id,
                            "view_type": item.view_type,
                            "asset_id": item.asset_id,
                            "asset_url": f"/api/v1/assets/{item.asset_id}/content",
                            "status": item.status,
                        }
                        for item in assets
                    ],
                }
            )
        looks = session.scalars(
            select(CharacterLookVersion)
            .where(CharacterLookVersion.character_id == character.id)
            .order_by(CharacterLookVersion.version)
        ).all()
        states = session.scalars(
            select(CharacterStoryStateVersion)
            .where(CharacterStoryStateVersion.character_id == character.id)
            .order_by(CharacterStoryStateVersion.version)
        ).all()
        result.append(
            {
                "id": character.id,
                "project_id": character.project_id,
                "character_key": character.character_key,
                "name": character.name,
                "role": character.role,
                "visual_brief": character.visual_brief,
                "status": character.status,
                "locked_candidate_id": character.locked_candidate_id,
                "current_profile_version_id": character.current_profile_version_id,
                "locked_identity_version_id": character.locked_identity_version_id,
                "active_look_version_id": character.active_look_version_id,
                "active_story_state_version_id": character.active_story_state_version_id,
                "lock_version": character.lock_version,
                "profile": _profile_to_read(profile) if profile else None,
                "family_resemblance_constraint": (
                    _family_constraint_to_read(family_constraint) if family_constraint else None
                ),
                "batches": [
                    {
                        "id": item.id,
                        "version": item.version,
                        "profile_version_id": item.profile_version_id,
                        "family_constraint_version_id": item.family_constraint_version_id,
                        "requested_count": item.requested_count,
                        "composition": item.composition,
                        "status": item.status,
                    }
                    for item in batches
                ],
                "candidates": [
                    {
                        "id": item.id,
                        "ordinal": item.ordinal,
                        "batch_id": item.batch_id,
                        "profile_version_id": item.profile_version_id,
                        "asset_id": item.asset_id,
                        "asset_url": f"/api/v1/assets/{item.asset_id}/content",
                        "seed": item.seed,
                        "status": item.status,
                        "review_status": item.review_status,
                        "selected": item.selected,
                    }
                    for item in candidates
                ],
                "identities": identity_reads,
                "looks": [
                    {
                        "id": item.id,
                        "version": item.version,
                        "label": item.label,
                        "identity_version_id": item.identity_version_id,
                        "payload": _json(item.payload_json),
                        "status": item.status,
                        "change_reason": item.change_reason,
                    }
                    for item in looks
                ],
                "story_states": [
                    {
                        "id": item.id,
                        "version": item.version,
                        "label": item.label,
                        "identity_version_id": item.identity_version_id,
                        "look_version_id": item.look_version_id,
                        "payload": _json(item.payload_json),
                        "status": item.status,
                    }
                    for item in states
                ],
            }
        )
    return {
        "project_id": project.id,
        "project_status": project.status,
        "project_lock_version": project.lock_version,
        "default_candidate_count": 3,
        "generation_policy": "SYSTEM_PREPARES_USER_TRIGGERS_HUMAN_LOCKS",
        "characters": result,
    }


def update_visual_profile(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    expected_version: int,
    changes: dict[str, object],
    actor: str,
) -> dict[str, object]:
    project_or_404(session, project_id)
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "角色不存在"})
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    current = session.get(CharacterVisualProfileVersion, character.current_profile_version_id)
    if current is None:
        raise HTTPException(
            status_code=409, detail={"code": "PROFILE_NOT_READY", "message": "角色视觉档案尚未准备"}
        )
    bible = session.get(StoryBibleVersion, current.source_story_bible_version_id)
    graph = session.get(RelationshipGraphVersion, current.source_relationship_graph_id)
    if bible is None or graph is None:
        raise ValueError("角色视觉档案的来源版本不存在")
    profile = _profile_content(current)
    for key in (
        "identity_fields",
        "appearance_fields",
        "personality_visualization",
        "styling_fields",
        "project_style",
    ):
        patch = changes.get(key)
        if isinstance(patch, dict):
            profile[key] = {**dict(profile[key]), **patch}
    if isinstance(changes.get("negative_constraints"), list):
        profile["negative_constraints"] = changes["negative_constraints"]
    if changes.get("selected_direction"):
        profile["selected_direction"] = changes["selected_direction"]
    current.status = "SUPERSEDED"
    record = _new_profile(
        session,
        project=project_or_404(session, project_id),
        character=character,
        bible=bible,
        graph=graph,
        profile=profile,
        source_hash=current.source_content_hash,
    )
    character.status = (
        "REVIEW_REQUIRED" if character.locked_identity_version_id else "NOT_GENERATED"
    )
    character.lock_version += 1
    character.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project_id,
        event_type="character.visual_profile_reviewed",
        payload={"character_id": character.id, "profile_version_id": record.id, "actor": actor},
    )
    session.commit()
    return _profile_to_read(record)


def confirm_visual_profile(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    profile_version_id: str,
    expected_version: int,
    actor: str,
) -> dict[str, object]:
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "角色不存在"})
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    profile = session.get(CharacterVisualProfileVersion, profile_version_id)
    if (
        profile is None
        or profile.character_id != character.id
        or character.current_profile_version_id != profile.id
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "PROFILE_VERSION_STALE", "message": "只能确认当前角色视觉版本"},
        )
    blockers = [
        item
        for item in _json(profile.conflict_report_json)
        if isinstance(item, dict) and item.get("severity") == "BLOCKER"
    ]
    if blockers:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROFILE_CONFLICT",
                "message": "角色视觉档案仍有阻断冲突",
                "details": {"issues": blockers},
            },
        )
    now = datetime.now(UTC)
    profile.status = "CONFIRMED"
    profile.confirmed_at = now
    profile.confirmed_by = actor
    character.status = "NOT_GENERATED" if not character.locked_identity_version_id else "LOCKED"
    character.lock_version += 1
    character.updated_at = now
    append_event(
        session,
        project_id=project_id,
        event_type="character.visual_baseline_confirmed",
        payload={"character_id": character.id, "profile_version_id": profile.id, "actor": actor},
    )
    session.commit()
    return _profile_to_read(profile)


def assemble_character_prompt(
    profile: CharacterVisualProfileVersion,
    family_constraint: CharacterFamilyResemblanceConstraint | None = None,
) -> dict[str, object]:
    content = _profile_content(profile)
    sections = [
        ("身份", content["identity_fields"]),
        ("外貌", content["appearance_fields"]),
        ("性格视觉化", content["personality_visualization"]),
        ("造型", content["styling_fields"]),
        ("项目风格", content["project_style"]),
    ]
    fragments: list[str] = []
    for label, values in sections:
        fields = dict(values)
        formatted = "；".join(
            f"{key}：{'、'.join(value) if isinstance(value, list) else value}"
            for key, value in fields.items()
            if value
        )
        fragments.append(f"{label}：{formatted}")
    family_content = (
        _family_constraint_to_read(family_constraint)
        if family_constraint is not None and family_constraint.status == "ACTIVE"
        else None
    )
    if family_content is not None:
        features = "；".join(
            f"{item['label']}参考{item['source_character_name']}：{item['value']}"
            for item in family_content["inherited_features"]
            if isinstance(item, dict)
        )
        independence = "；".join(str(item) for item in family_content["independence_constraints"])
        temperament = family_content["temperament_affinity"]
        fragments.append(
            "Family Resemblance Constraint："
            f"相似等级 {family_content['similarity_level']}；只继承以下家族特征：{features}；"
            f"{independence}；气质约束：{temperament.get('instruction', '')}"
        )
    negatives = "、".join(str(item) for item in content["negative_constraints"])
    prompt = "。".join(
        [
            "单人角色选角照，正面胸像，视线平视镜头，中性纯色背景，统一 85mm 等效焦段和相机高度",
            *fragments,
            f"视觉方向：{content.get('selected_direction') or 'cinematic'}",
            f"负面约束：{negatives}",
            "画面中只能出现一人，不得出现文字、标识或拼贴",
        ]
    )
    return {
        "prompt": prompt,
        "structured_fields": {
            **content,
            "family_resemblance_constraint": family_content,
        },
        "schema_version": "character-prompt-v2-family-resemblance",
    }


def generate_character_candidates(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    profile_version_id: str,
    expected_version: int,
    count: int,
    actor: str,
    trace_id: str,
) -> tuple[dict[str, object], list[JobRead]]:
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "角色不存在"})
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    profile = session.get(CharacterVisualProfileVersion, profile_version_id)
    if profile is None or profile.character_id != character.id or profile.status != "CONFIRMED":
        raise HTTPException(
            status_code=409,
            detail={"code": "BASELINE_NOT_CONFIRMED", "message": "先确认当前角色基线，再生成候选"},
        )
    if character.current_profile_version_id != profile.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_VERSION_STALE",
                "message": "角色文字设定已变化，请重新审核当前版本",
            },
        )
    active = session.scalar(
        select(Job.id).where(
            Job.entity_id == character.id,
            Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
            Job.status.in_({"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}),
        )
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail={"code": "GENERATION_IN_PROGRESS", "message": "角色候选正在生成"},
        )
    batch_version = (
        session.scalar(
            select(func.max(CharacterCandidateBatch.version)).where(
                CharacterCandidateBatch.character_id == character.id
            )
        )
        or 0
    ) + 1
    first_ordinal = (
        session.scalar(
            select(func.max(CharacterCandidate.ordinal)).where(
                CharacterCandidate.character_id == character.id
            )
        )
        or 0
    ) + 1
    graph = session.get(RelationshipGraphVersion, profile.source_relationship_graph_id)
    if graph is None:
        raise ValueError("角色关系网版本不存在")
    family_constraint = refresh_family_resemblance_constraint(
        session,
        character=character,
        graph=graph,
    )
    prompt = assemble_character_prompt(profile, family_constraint)
    batch = CharacterCandidateBatch(
        id=str(uuid4()),
        project_id=project_id,
        character_id=character.id,
        profile_version_id=profile.id,
        family_constraint_version_id=(
            family_constraint.id
            if family_constraint is not None and family_constraint.status == "ACTIVE"
            else None
        ),
        version=batch_version,
        requested_count=count,
        composition="FRONT_BUST",
        status="GENERATING",
        prompt_json=canonical_json(prompt),
        created_at=datetime.now(UTC),
    )
    session.add(batch)
    session.flush()
    jobs: list[JobRead] = []
    for offset in range(count):
        ordinal = first_ordinal + offset
        seed = int(content_hash(f"{profile.content_hash}:{batch.version}:{ordinal}")[:8], 16)
        job, _ = enqueue_job(
            session,
            project_id=project_id,
            job_type="GENERATE_CHARACTER_VISUAL_CANDIDATE",
            entity_type="character",
            entity_id=character.id,
            idempotency_key=f"{project_id}:CHARACTER_VISUAL:{batch.id}:{ordinal}",
            input_payload={
                "character_id": character.id,
                "batch_id": batch.id,
                "profile_version_id": profile.id,
                "ordinal": ordinal,
                "candidate_count": count,
                "prompt": prompt["prompt"],
                "prompt_snapshot": prompt,
                "family_constraint_version_id": batch.family_constraint_version_id,
                "reference_asset_ids": (
                    _json(family_constraint.source_asset_ids_json)
                    if family_constraint is not None and family_constraint.status == "ACTIVE"
                    else []
                ),
                "seed": seed,
            },
            label=f"{character.name} · 形象候选 {offset + 1}/{count}",
            stage="等待生成统一构图正面胸像",
            trace_id=trace_id,
            estimated_seconds=35,
            retryable=True,
        )
        jobs.append(job_to_read(job))
    character.status = "GENERATING"
    character.lock_version += 1
    character.updated_at = datetime.now(UTC)
    append_event(
        session,
        project_id=project_id,
        event_type="character.candidate_generation_requested",
        payload={
            "character_id": character.id,
            "batch_id": batch.id,
            "count": count,
            "family_constraint_version_id": batch.family_constraint_version_id,
            "actor": actor,
        },
    )
    session.commit()
    return {
        "id": batch.id,
        "version": batch.version,
        "requested_count": batch.requested_count,
        "family_constraint_version_id": batch.family_constraint_version_id,
        "status": batch.status,
    }, jobs


def materialize_visual_candidate(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
) -> tuple[Asset, CharacterCandidate]:
    payload = json.loads(job.input_json)
    character = session.get(Character, str(payload["character_id"]))
    batch = session.get(CharacterCandidateBatch, str(payload["batch_id"]))
    if character is None or batch is None:
        raise ValueError("角色候选批次不存在")
    ordinal = int(payload["ordinal"])
    existing = session.scalar(
        select(CharacterCandidate).where(
            CharacterCandidate.character_id == character.id,
            CharacterCandidate.ordinal == ordinal,
        )
    )
    if existing is not None:
        asset = session.get(Asset, existing.asset_id)
        if asset is None:
            raise ValueError("角色候选资产不存在")
        return asset, existing
    tmp_dir = settings.data_dir / "tmp" / job.id / "character-visual"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"candidate-{ordinal}{suffix}")
    image_path.write_bytes(image.content)
    candidate_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="character_candidate",
        source=image_path,
        source_entity_type="character_candidate",
        source_entity_id=candidate_id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    asset.metadata_json = canonical_json(
        {"model": image.model, "provider_request_id": image.request_id, "seed": payload["seed"]}
    )
    candidate = CharacterCandidate(
        id=candidate_id,
        project_id=job.project_id,
        character_id=character.id,
        batch_id=batch.id,
        profile_version_id=str(payload["profile_version_id"]),
        ordinal=ordinal,
        asset_id=asset.id,
        seed=str(payload["seed"]),
        status="READY",
        prompt_snapshot_json=canonical_json(payload["prompt_snapshot"]),
        review_status="PENDING_SELECTION",
        selected=False,
        created_at=datetime.now(UTC),
    )
    session.add(candidate)
    session.flush()
    ready_count = session.scalar(
        select(func.count(CharacterCandidate.id)).where(CharacterCandidate.batch_id == batch.id)
    )
    if ready_count >= batch.requested_count:
        batch.status = "READY"
        character.status = "PENDING_SELECTION"
        character.lock_version += 1
        character.updated_at = datetime.now(UTC)
        append_event(
            session,
            project_id=job.project_id,
            job_id=job.id,
            event_type="character.candidates_ready",
            payload={"character_id": character.id, "batch_id": batch.id},
        )
    return asset, candidate


def select_character_candidate(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    candidate_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
) -> tuple[dict[str, object], list[JobRead]]:
    character = session.get(Character, character_id)
    candidate = session.get(CharacterCandidate, candidate_id)
    if (
        character is None
        or character.project_id != project_id
        or candidate is None
        or candidate.character_id != character.id
    ):
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "角色候选不存在"}
        )
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    if (
        candidate.status != "READY"
        or candidate.profile_version_id != character.current_profile_version_id
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "CANDIDATE_STALE", "message": "候选不是当前角色基线生成的可用版本"},
        )
    profile = session.get(CharacterVisualProfileVersion, candidate.profile_version_id)
    if profile is None:
        raise ValueError("候选角色基线不存在")
    version = (
        session.scalar(
            select(func.max(CharacterIdentityVersion.version)).where(
                CharacterIdentityVersion.character_id == character.id
            )
        )
        or 0
    ) + 1
    prompt_snapshot = _json(candidate.prompt_snapshot_json)
    stable_traits = {
        "identity": _json(profile.identity_fields_json),
        "appearance": _json(profile.appearance_fields_json),
    }
    identity = CharacterIdentityVersion(
        id=str(uuid4()),
        project_id=project_id,
        character_id=character.id,
        version=version,
        source_candidate_id=candidate.id,
        profile_version_id=profile.id,
        stable_traits_json=canonical_json(stable_traits),
        prompt_snapshot_json=canonical_json(prompt_snapshot),
        content_hash=content_hash({"candidate": candidate.id, "stable_traits": stable_traits}),
        status="GENERATING_DOSSIER",
        locked_at=None,
        locked_by=None,
        created_at=datetime.now(UTC),
    )
    session.add(identity)
    session.flush()
    candidate.selected = True
    candidate.review_status = "DOSSIER_GENERATING"
    character.status = "PENDING_REVIEW"
    character.lock_version += 1
    character.updated_at = datetime.now(UTC)
    jobs: list[JobRead] = []
    for view_type, view_instruction in DOSSIER_VIEWS:
        job, _ = enqueue_job(
            session,
            project_id=project_id,
            job_type="GENERATE_CHARACTER_IDENTITY_DOSSIER",
            entity_type="character_identity",
            entity_id=identity.id,
            idempotency_key=f"{project_id}:CHARACTER_DOSSIER:{identity.id}:{view_type}",
            input_payload={
                "character_id": character.id,
                "identity_version_id": identity.id,
                "candidate_id": candidate.id,
                "reference_asset_id": candidate.asset_id,
                "view_type": view_type,
                "prompt": (
                    f"严格保持参考图中同一人的脸型、五官、年龄感、体型和识别特征。{view_instruction}。"
                    "中性背景、统一光线和焦段，不改变服装、妆容、发型，不添加其他人物、文字或水印。"
                ),
                "seed": int(content_hash(f"{identity.id}:{view_type}")[:8], 16),
            },
            label=f"{character.name} · 身份档案 · {view_type}",
            stage="等待生成角色身份档案",
            trace_id=trace_id,
            estimated_seconds=35,
            retryable=True,
        )
        jobs.append(job_to_read(job))
    append_event(
        session,
        project_id=project_id,
        event_type="character.candidate_selected",
        payload={
            "character_id": character.id,
            "candidate_id": candidate.id,
            "identity_version_id": identity.id,
            "actor": actor,
        },
    )
    session.commit()
    return {"id": identity.id, "version": identity.version, "status": identity.status}, jobs


def materialize_identity_asset(
    session: Session,
    settings: Settings,
    job: Job,
    image: GeneratedImage,
) -> tuple[Asset, CharacterIdentityAsset]:
    payload = json.loads(job.input_json)
    identity = session.get(CharacterIdentityVersion, str(payload["identity_version_id"]))
    character = session.get(Character, str(payload["character_id"]))
    if identity is None or character is None:
        raise ValueError("角色身份版本不存在")
    view_type = str(payload["view_type"])
    existing = session.scalar(
        select(CharacterIdentityAsset).where(
            CharacterIdentityAsset.identity_version_id == identity.id,
            CharacterIdentityAsset.view_type == view_type,
        )
    )
    if existing is not None:
        asset = session.get(Asset, existing.asset_id)
        if asset is None:
            raise ValueError("角色身份资产不存在")
        return asset, existing
    tmp_dir = settings.data_dir / "tmp" / job.id / "character-identity"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if image.mime == "image/png" else ".jpg"
    image_path = Path(tmp_dir / f"{view_type.lower()}{suffix}")
    image_path.write_bytes(image.content)
    record_id = str(uuid4())
    asset = register_file(
        session,
        settings,
        project_id=job.project_id,
        kind="character_identity",
        source=image_path,
        source_entity_type="character_identity_asset",
        source_entity_id=record_id,
        mime=image.mime,
        width=image.width,
        height=image.height,
    )
    asset.provider = "volcengine-ark" if settings.ark_api_key else "mock"
    record = CharacterIdentityAsset(
        id=record_id,
        project_id=job.project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        view_type=view_type,
        asset_id=asset.id,
        status="READY",
        created_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    count = session.scalar(
        select(func.count(CharacterIdentityAsset.id)).where(
            CharacterIdentityAsset.identity_version_id == identity.id
        )
    )
    if count >= len(DOSSIER_VIEWS):
        identity.status = "READY_FOR_REVIEW"
        character.status = "REVIEW_REQUIRED"
        character.lock_version += 1
        character.updated_at = datetime.now(UTC)
        candidate = session.get(CharacterCandidate, identity.source_candidate_id)
        if candidate is not None:
            candidate.review_status = "READY_FOR_REVIEW"
        append_event(
            session,
            project_id=job.project_id,
            job_id=job.id,
            event_type="character.identity_dossier_ready",
            payload={"character_id": character.id, "identity_version_id": identity.id},
        )
    return asset, record


def _create_base_look_and_state(
    session: Session,
    *,
    character: Character,
    identity: CharacterIdentityVersion,
    profile: CharacterVisualProfileVersion,
    actor: str,
) -> tuple[CharacterLookVersion, CharacterStoryStateVersion]:
    now = datetime.now(UTC)
    look_version = (
        session.scalar(
            select(func.max(CharacterLookVersion.version)).where(
                CharacterLookVersion.character_id == character.id
            )
        )
        or 0
    ) + 1
    styling = _json(profile.styling_fields_json)
    look = CharacterLookVersion(
        id=str(uuid4()),
        project_id=character.project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        parent_version_id=character.active_look_version_id,
        version=look_version,
        label=f"造型 {look_version} · 基础造型",
        usage_scope="GLOBAL",
        payload_json=canonical_json(styling),
        reference_asset_ids_json="[]",
        content_hash=content_hash({"identity": identity.id, "styling": styling}),
        status="APPROVED",
        change_reason="角色身份锁定后的基础造型",
        approved_at=now,
        approved_by=actor,
        created_at=now,
    )
    session.add(look)
    session.flush()
    state_version = (
        session.scalar(
            select(func.max(CharacterStoryStateVersion.version)).where(
                CharacterStoryStateVersion.character_id == character.id
            )
        )
        or 0
    ) + 1
    state_payload = {"emotion": "中性", "injury": "无", "wetness": "干燥", "fatigue": "正常"}
    state = CharacterStoryStateVersion(
        id=str(uuid4()),
        project_id=character.project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        look_version_id=look.id,
        version=state_version,
        label="基础剧情状态",
        payload_json=canonical_json(state_payload),
        content_hash=content_hash(state_payload),
        status="ACTIVE",
        created_at=now,
    )
    session.add(state)
    session.flush()
    return look, state


def lock_character_identity(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    identity_version_id: str,
    expected_version: int,
    actor: str,
    trace_id: str,
) -> tuple[dict[str, object], JobRead | None]:
    project = project_or_404(session, project_id)
    character = session.get(Character, character_id)
    identity = session.get(CharacterIdentityVersion, identity_version_id)
    if (
        character is None
        or character.project_id != project_id
        or identity is None
        or identity.character_id != character.id
    ):
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "角色身份版本不存在"}
        )
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    if identity.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={"code": "IDENTITY_NOT_READY", "message": "身份档案尚未生成完成或仍需审核"},
        )
    profile = session.get(CharacterVisualProfileVersion, identity.profile_version_id)
    candidate = session.get(CharacterCandidate, identity.source_candidate_id)
    if profile is None or candidate is None:
        raise ValueError("角色身份来源不存在")
    now = datetime.now(UTC)
    previous = (
        session.get(CharacterIdentityVersion, character.locked_identity_version_id)
        if character.locked_identity_version_id
        else None
    )
    if previous is not None and previous.id != identity.id:
        previous.status = "SUPERSEDED"
    identity.status = "LOCKED"
    identity.locked_at = now
    identity.locked_by = actor
    candidate.review_status = "LOCKED"
    candidate.selected = True
    character.locked_candidate_id = candidate.id
    character.locked_identity_version_id = identity.id
    look, state = _create_base_look_and_state(
        session, character=character, identity=identity, profile=profile, actor=actor
    )
    character.active_look_version_id = look.id
    character.active_story_state_version_id = state.id
    character.status = "LOCKED"
    character.lock_version += 1
    character.updated_at = now
    session.flush()
    all_characters = session.scalars(
        select(Character).where(
            Character.project_id == project_id,
            Character.source_relationship_graph_id == character.source_relationship_graph_id,
        )
    ).all()
    graph = session.get(RelationshipGraphVersion, character.source_relationship_graph_id)
    if graph is None:
        raise ValueError("角色身份对应的关系网版本不存在")
    refresh_family_resemblance_constraints(session, characters=list(all_characters), graph=graph)
    script_job: JobRead | None = None
    if all_characters and all(item.locked_identity_version_id for item in all_characters):
        from app.services.relationship_graph_workflow import (
            enqueue_script_package_for_locked_identities,
        )

        job, _ = enqueue_script_package_for_locked_identities(
            session,
            project=project,
            actor=actor,
            trace_id=trace_id,
        )
        script_job = job_to_read(job)
        project.status = "SCRIPT_PACKAGE_RUNNING"
        project.lock_version += 1
        project.updated_at = now
    append_event(
        session,
        project_id=project_id,
        job_id=script_job.id if script_job else None,
        event_type="character.identity_locked",
        payload={
            "character_id": character.id,
            "identity_version_id": identity.id,
            "look_version_id": look.id,
            "story_state_version_id": state.id,
            "script_started": script_job is not None,
        },
    )
    session.commit()
    return {
        "character_id": character.id,
        "identity_version_id": identity.id,
        "look_version_id": look.id,
        "story_state_version_id": state.id,
        "status": character.status,
        "lock_version": character.lock_version,
    }, script_job


def apply_character_change(
    session: Session,
    *,
    project_id: str,
    character_id: str,
    expected_version: int,
    change_type: str,
    payload: dict[str, object],
    decision: str | None,
    actor: str,
) -> dict[str, object]:
    character = session.get(Character, character_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "角色不存在"})
    if character.lock_version != expected_version:
        raise version_conflict(character, expected_version)
    if change_type == "TEXT_ONLY":
        return {
            "action": "NO_IMAGE_GENERATION",
            "preserved_identity_version_id": character.locked_identity_version_id,
        }
    identity = session.get(CharacterIdentityVersion, character.locked_identity_version_id)
    if identity is None:
        raise HTTPException(
            status_code=409, detail={"code": "IDENTITY_NOT_LOCKED", "message": "角色身份尚未锁定"}
        )
    now = datetime.now(UTC)
    if change_type == "STORY_STATE":
        version = (
            session.scalar(
                select(func.max(CharacterStoryStateVersion.version)).where(
                    CharacterStoryStateVersion.character_id == character.id
                )
            )
            or 0
        ) + 1
        state = CharacterStoryStateVersion(
            id=str(uuid4()),
            project_id=project_id,
            character_id=character.id,
            identity_version_id=identity.id,
            look_version_id=character.active_look_version_id,
            version=version,
            label=_as_text(payload.get("label"), f"剧情状态 {version}"),
            payload_json=canonical_json(payload),
            content_hash=content_hash(payload),
            status="ACTIVE",
            created_at=now,
        )
        session.add(state)
        session.flush()
        character.active_story_state_version_id = state.id
        action = "STORY_STATE_VERSION_CREATED"
        entity_id = state.id
    elif change_type == "LOOK":
        version = (
            session.scalar(
                select(func.max(CharacterLookVersion.version)).where(
                    CharacterLookVersion.character_id == character.id
                )
            )
            or 0
        ) + 1
        look = CharacterLookVersion(
            id=str(uuid4()),
            project_id=project_id,
            character_id=character.id,
            identity_version_id=identity.id,
            parent_version_id=character.active_look_version_id,
            version=version,
            label=_as_text(payload.get("label"), f"造型 {version}"),
            usage_scope="SCENE",
            payload_json=canonical_json(payload),
            reference_asset_ids_json="[]",
            content_hash=content_hash({"identity": identity.id, "payload": payload}),
            status="READY_FOR_REVIEW",
            change_reason=_as_text(payload.get("reason"), "用户修改服装、妆容或发型"),
            approved_at=None,
            approved_by=None,
            created_at=now,
        )
        session.add(look)
        session.flush()
        character.active_look_version_id = look.id
        action = "LOOK_VERSION_CREATED"
        entity_id = look.id
    else:
        if decision is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDENTITY_DECISION_REQUIRED",
                    "message": "重大身份变化必须明确保留当前身份或重新生成候选",
                    "details": {"options": ["PRESERVE_IDENTITY", "REGENERATE"]},
                },
            )
        if decision == "PRESERVE_IDENTITY":
            character.status = "LOCKED"
            action = "IDENTITY_PRESERVED"
            entity_id = identity.id
        else:
            character.status = "RE_REVIEW_REQUIRED"
            profile = session.get(
                CharacterVisualProfileVersion,
                character.current_profile_version_id,
            )
            if profile is not None:
                profile.status = "READY_FOR_REVIEW"
                profile.confirmed_at = None
                profile.confirmed_by = None
            action = "IDENTITY_REGENERATION_REQUIRED"
            entity_id = character.current_profile_version_id
    character.lock_version += 1
    character.updated_at = now
    append_event(
        session,
        project_id=project_id,
        event_type="character.change_applied",
        payload={
            "character_id": character.id,
            "change_type": change_type,
            "action": action,
            "actor": actor,
        },
    )
    session.commit()
    return {
        "action": action,
        "entity_id": entity_id,
        "character_status": character.status,
        "lock_version": character.lock_version,
        "existing_shots_preserved": True,
    }


def mark_character_generation_failed(session: Session, job: Job) -> None:
    if job.job_type not in {
        "GENERATE_CHARACTER_VISUAL_CANDIDATE",
        "GENERATE_CHARACTER_IDENTITY_DOSSIER",
    }:
        return
    payload = json.loads(job.input_json)
    character = session.get(Character, str(payload.get("character_id", "")))
    if character is None:
        return
    character.status = "GENERATION_FAILED"
    character.lock_version += 1
    character.updated_at = datetime.now(UTC)
    batch_id = payload.get("batch_id")
    if batch_id:
        batch = session.get(CharacterCandidateBatch, str(batch_id))
        if batch is not None:
            batch.status = "FAILED"
