import json
from types import SimpleNamespace

from app.services.character_visuals import (
    LEGACY_PERSONALITY_VISUALIZATION,
    _entity_kind,
    _enrich_legacy_personality_visualization,
    _visualize_personality,
    assemble_character_prompt,
)


def test_personality_visualization_uses_explicit_character_evidence() -> None:
    contexts = [
        {
            "role": "叙事主角，女儿",
            "dramatic_function": "旧案线推动者，负责验证关键线索",
            "visual_notes": "举止带着年轻人的利落感",
        },
        {
            "role": "共同主角，母亲",
            "dramatic_function": "母女关系中藏着过去秘密的一方",
            "visual_notes": "走路姿态随意，熟悉食堂事务",
        },
        {
            "role": "核心反派，集团董事长",
            "dramatic_function": "旧案线核心冲突来源",
            "visual_notes": "神态和蔼但眼神锐利，看不出情绪",
        },
        {
            "role": "职场小反派，广告部主管",
            "dramatic_function": "职场线前期冲突来源",
            "visual_notes": "喜欢颐指气使使唤下属，看不起出身普通的同事",
        },
        {
            "role": "中立配角，退休工程师",
            "dramatic_function": "旧案线索提供者",
            "visual_notes": "说话小心翼翼，怕惹事",
        },
    ]

    gazes = {
        _visualize_personality([], **context)["gaze"]
        for context in contexts
    }

    assert len(gazes) == len(contexts)
    assert LEGACY_PERSONALITY_VISUALIZATION["gaze"] not in gazes


def test_legacy_visualization_is_enriched_without_overwriting_manual_fields() -> None:
    enriched = _enrich_legacy_personality_visualization(
        {
            **LEGACY_PERSONALITY_VISUALIZATION,
            "expression": "创作者手动确认的平静表情",
        },
        identity={"story_identity": "旧案线索提供者"},
        appearance={"identifying_features": "说话小心翼翼，怕惹事"},
    )

    assert enriched["expression"] == "创作者手动确认的平静表情"
    assert enriched["gaze"] == "与人对视短暂，确认安全后才停留；谈到敏感往事时会移开目光"
    assert enriched["posture"] == "肩背略收，身体保持可退让的余地"


def test_digital_entity_uses_non_human_visual_prompt() -> None:
    assert _entity_kind(
        {
            "occupation": "Autonomous General AI System",
            "story_identity": "通过屏幕与主角对话",
        },
        {"identifying_features": "clean cold blue interface, no physical body"},
    ) == "DIGITAL_ENTITY"

    profile = SimpleNamespace(
        identity_fields_json=json.dumps(
            {
                "occupation": "Autonomous General AI System",
                "story_identity": "核心支持角色",
                "age": "3 years of operation",
                "gender_expression": "不适用",
                "region": "不适用",
            }
        ),
        appearance_fields_json=json.dumps(
            {
                "identifying_features": "clean cold blue interface, no physical body",
                "height": "不适用",
                "face_shape": "自然骨相",
            }
        ),
        personality_visualization_json="{}",
        styling_fields_json=json.dumps({"colors": "冷蓝色"}),
        project_style_json=json.dumps({"realism": "写实科幻"}),
        negative_constraints_json="[]",
        recommended_directions_json="[]",
        selected_direction=None,
    )

    prompt = assemble_character_prompt(profile)["prompt"]

    assert "非人类数字实体视觉方案" in prompt
    assert "禁止生成人类脸孔、人体、服装或真人选角照" in prompt
    assert "单人角色选角照" not in prompt
    assert "face_shape" not in prompt
    assert "gender_expression" not in prompt
