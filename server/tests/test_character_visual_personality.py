from app.services.character_visuals import (
    LEGACY_PERSONALITY_VISUALIZATION,
    _enrich_legacy_personality_visualization,
    _visualize_personality,
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
