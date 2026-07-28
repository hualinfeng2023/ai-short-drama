from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.services.storyboards_v2 import (
    _is_face_hidden_brief,
    _line_character_keys,
    _scene_character_keys,
    _shot_delivery,
    _visual_description_for_line,
    build_storyboard_take_prompt,
    compile_single_frame_visual_brief,
    mask_character_reference_watermark,
)


def _characters() -> dict[str, SimpleNamespace]:
    return {
        "protagonist": SimpleNamespace(name="林悦", visual_brief="短发亚洲女性"),
        "witness": SimpleNamespace(name="周启", visual_brief="中年男性"),
        "spouse_wife": SimpleNamespace(
            name="妻子",
            visual_brief="穿深灰简约素色服装，外表年轻温和",
        ),
        "spouse_husband": SimpleNamespace(
            name="丈夫",
            visual_brief="穿和妻子同色系深灰服装，肩部宽厚",
        ),
        "child": SimpleNamespace(
            name="孩子",
            visual_brief="刚出生包裹在米白色柔软襁褓中，仅露出小手，不露出可识别身份的面部特征",
        ),
        "doctor": SimpleNamespace(
            name="医生",
            visual_brief="全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏",
        ),
    }


def _line(
    speaker: str,
    text: str,
    line_type: str,
    *,
    emotion: str = "平静",
) -> SimpleNamespace:
    return SimpleNamespace(
        speaker_key=speaker,
        text=text,
        line_type=line_type,
        emotion=emotion,
    )


def test_action_line_binds_every_character_explicitly_mentioned() -> None:
    characters = _characters()
    line = _line("NARRATOR", "林悦把照片推给周启。", "ACTION")

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["protagonist", "witness"],
        delivery="ACTION",
    ) == ["protagonist", "witness"]


def test_voice_over_binds_only_characters_named_in_action_visual() -> None:
    characters = _characters()
    lines = [
        _line("NARRATOR", "灯灭了。", "VOICE_OVER"),
        _line("protagonist", "把门锁上。", "DIALOGUE"),
        _line("witness", "已经晚了。", "DIALOGUE"),
    ]
    scene_character_keys = _scene_character_keys(lines, characters)  # type: ignore[arg-type]

    assert scene_character_keys == ["protagonist", "witness"]
    # 无画面锚点提名时，VO 不再整场继承 scene cast
    assert (
        _line_character_keys(
            lines[0],
            characters_by_key=characters,  # type: ignore[arg-type]
            scene_character_keys=scene_character_keys,
            delivery="VOICE_OVER",
            visual_anchor_text="灯灭了。废弃走廊。",
        )
        == []
    )
    assert _line_character_keys(
        lines[0],
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=scene_character_keys,
        delivery="VOICE_OVER",
        visual_anchor_text="林悦站在窗边，灯灭了。",
    ) == ["protagonist"]


def test_dialogue_line_keeps_the_speaking_character_when_no_one_else_is_named() -> None:
    characters = _characters()
    line = _line("witness", "已经晚了。", "DIALOGUE")

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["protagonist", "witness"],
        delivery="DIALOGUE",
    ) == ["witness"]


def test_dialogue_mentioning_child_does_not_bind_child_character() -> None:
    characters = _characters()
    line = _line(
        "doctor",
        "这座城市的永生配额固定：今天你们的孩子出生，今晚就会有一个永生者自然老去死亡。",
        "VOICE_OVER",
    )

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["doctor", "child"],
        delivery="VOICE_OVER",
        visual_anchor_text="正面完全对称广角镜头，空荡产房中央金属桌，夫妻分坐两端",
    ) == ["spouse_wife", "spouse_husband"]


def test_action_couple_alias_and_glove_hands_bind_locked_cast() -> None:
    characters = _characters()
    line = _line(
        "scene_action",
        "正面完全对称广角镜头，空荡产房中央金属桌，夫妻分坐两端，"
        "一双戴无菌白手套的手从镜头下方伸出，将两枚黑色生物识别器推到夫妻面前",
        "ACTION",
    )

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["doctor", "child"],
        delivery="ACTION",
    ) == ["spouse_wife", "spouse_husband", "doctor"]


def test_scene_cast_ignores_voice_over_name_mentions() -> None:
    characters = _characters()
    lines = [
        _line(
            "scene_action",
            "正面完全对称广角镜头，空荡产房中央金属桌，夫妻分坐两端",
            "ACTION",
        ),
        _line("doctor", "一个孩子出生，一个永生者今晚老去。", "VOICE_OVER"),
    ]

    assert _scene_character_keys(lines, characters) == [  # type: ignore[arg-type]
        "spouse_wife",
        "spouse_husband",
    ]


def test_face_hidden_dialogue_is_treated_as_voice_over_delivery() -> None:
    characters = _characters()
    line = _line("doctor", "守恒法不变。", "DIALOGUE", emotion="平静客观")
    speaker = characters["doctor"]

    assert _shot_delivery(line, speaker) == "VOICE_OVER"  # type: ignore[arg-type]
    assert _is_face_hidden_brief(speaker.visual_brief)


def test_voice_over_description_inherits_action_visual() -> None:
    line = _line("doctor", "这是人类最后一个胚胎。", "VOICE_OVER", emotion="平静无起伏")
    description = _visual_description_for_line(
        line,  # type: ignore[arg-type]
        delivery="VOICE_OVER",
        purpose="开场抛出核心钩子",
        speaking_label="医生",
        action_visual="开场抛出核心钩子。极微距镜头拍摄胚胎舱表面",
    )

    assert "画外音覆盖于" in description
    assert "极微距镜头拍摄胚胎舱表面" in description
    assert "完成台词" not in description


def test_character_reference_masks_the_lower_right_watermark_region() -> None:
    source = Image.new("RGB", (200, 120), (20, 40, 70))
    draw = ImageDraw.Draw(source)
    draw.rectangle((160, 106, 196, 116), fill=(240, 240, 240))
    encoded = BytesIO()
    source.save(encoded, format="PNG")

    cleaned = mask_character_reference_watermark(encoded.getvalue(), "image/png")

    with Image.open(BytesIO(cleaned)) as output:
        result = output.convert("RGB")
        assert result.size == source.size
        assert result.getpixel((190, 110)) != (240, 240, 240)
        assert result.getpixel((20, 20)) == (20, 40, 70)


def test_character_reference_mask_leaves_unsupported_content_unchanged() -> None:
    content = b"not-an-image"

    assert mask_character_reference_watermark(content, "image/gif") == content


def test_character_reference_without_watermark_is_not_reencoded_or_masked() -> None:
    source = Image.new("RGB", (200, 120), (245, 245, 245))
    encoded = BytesIO()
    source.save(encoded, format="PNG")
    content = encoded.getvalue()

    assert mask_character_reference_watermark(content, "image/png") == content


def test_storyboard_take_prompt_locks_character_identity() -> None:
    project = SimpleNamespace(style="写实都市夜戏", aspect_ratio="9:16")
    characters = [
        SimpleNamespace(
            name="林悦",
            role="protagonist",
            visual_brief="短发亚洲女性，冷色工装，锐利眉眼",
        )
    ]

    prompt = build_storyboard_take_prompt(
        project,  # type: ignore[arg-type]
        description="林悦站在夜色办公室窗边，冷色工装，神情警觉",
        dialogue="Stay one more day.",
        location="Night office",
        time_of_day="夜",
        shot_size="MS",
        camera_movement="TRACK",
        characters=characters,  # type: ignore[arg-type]
        delivery="DIALOGUE",
    )

    assert "单帧静帧规格" in prompt
    assert "角色身份锁定（硬约束）" in prompt
    assert "林悦" in prompt
    assert "短发亚洲女性" in prompt
    assert "禁止换脸" in prompt
    # 项目风格进入 Creative Bible，不进入 image prompt
    assert "写实都市夜戏" not in prompt
    assert "电影剧照" in prompt
    assert "塑料皮肤" in prompt
    assert "{" not in prompt
    # 台词文本不进画面；只保留可见口型约束，且不含运镜过程
    assert "Stay one more day." not in prompt
    assert "人物正在说" not in prompt
    assert "TRACK" not in prompt
    assert "运镜" not in prompt
    assert "口型" in prompt
    assert "固定机位单帧" in prompt


def test_voice_over_prompt_uses_offscreen_audio_not_lip_sync() -> None:
    project = SimpleNamespace(style="realistic_cinematic", aspect_ratio="9:16")
    characters = [
        SimpleNamespace(
            name="医生",
            role="规则执行者",
            visual_brief="全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏",
        )
    ]

    prompt = build_storyboard_take_prompt(
        project,  # type: ignore[arg-type]
        description="画外音覆盖于：极微距镜头拍摄胚胎舱表面",
        dialogue="这是人类最后一个胚胎。",
        location="废弃人类胚胎储存产房",
        time_of_day="午夜",
        shot_size="CU",
        camera_movement="STATIC",
        characters=characters,  # type: ignore[arg-type]
        delivery="VOICE_OVER",
    )

    assert "这是人类最后一个胚胎" not in prompt
    assert "画外音：" not in prompt
    assert "人物正在说" not in prompt
    assert "胚胎舱表面" in prompt
    # VO 不写角色视觉描述 / 身份模板
    assert "医生" not in prompt
    assert "角色身份锁定" not in prompt
    assert "唇形、发型核心特征" not in prompt
    assert "脸型、五官比例" not in prompt
    assert "画面中不出现任何人物" in prompt


def test_storyboard_prompt_keeps_creative_bible_out_of_image_prompt() -> None:
    from app.services.storyboards_v2 import assemble_shot_prompt

    idea = (
        "核心设定：\n"
        "在人类实现永生后，为了维持人口总量，《人口守恒法》规定："
        "每诞生一个孩子，就必须有一名永生者自愿交还永生。\n\n"
        "故事梗概：\n"
        "一对拥有永恒生命的夫妻，将生育的决定推迟了三百一十二年。"
        "当世界上最后一个人类胚胎即将被销毁，他们必须在六十秒内决定。\n\n"
        "整体视觉：\n"
        "前半段以冷青色、银灰色和深黑色为主，表现永生世界的冰冷、停滞与空洞。"
    )
    project = SimpleNamespace(
        style="realistic_cinematic",
        aspect_ratio="9:16",
        genre="sci_fi",
        idea=idea,
    )
    characters = [
        SimpleNamespace(
            name="医生",
            role="规则执行者",
            visual_brief="全程不露出面部，仅出现戴无菌白手套的双手",
        )
    ]

    assembled = assemble_shot_prompt(
        project,  # type: ignore[arg-type]
        description="极微距镜头拍摄胚胎舱表面，冰霜覆盖玻璃",
        dialogue="",
        location="废弃人类胚胎储存产房",
        time_of_day="午夜",
        shot_size="CU",
        camera_movement="STATIC",
        characters=characters,  # type: ignore[arg-type]
        delivery="ACTION",
    )
    prompt = assembled.image_prompt

    assert "时代与世界观" not in prompt
    assert "人口守恒法" not in prompt
    assert "三百一十二" not in prompt
    assert "人口守恒" not in prompt
    assert assembled.creative_bible.get("worldview")
    assert "人口守恒" in assembled.creative_bible.get("worldview", "")
    assert any(item.field == "global_creative_bible" for item in assembled.debug.removed)


def test_compile_single_frame_strips_audio_and_splits_pull_focus() -> None:
    source = (
        "开场抛出核心钩子，交代背景设定。黑场，三声间隔清晰的有力胎心响起，"
        "极微距镜头拍摄胚胎舱表面，一滴冷凝水随胎心轻轻震动，冰霜覆盖玻璃，"
        "内部可见微弱暖光的胚胎，镜头拉焦露出整间废弃产房，两侧排列蒙尘空婴儿床"
    )
    close = compile_single_frame_visual_brief(source, shot_size="CU")
    wide = compile_single_frame_visual_brief(source, shot_size="WS")

    assert "胎心" not in close
    assert "黑场" not in close
    assert "拉焦" not in close
    assert "胚胎舱" in close or "冰霜" in close or "冷凝水" in close
    assert "废弃产房" not in close

    assert "胎心" not in wide
    assert "拉焦" not in wide
    assert "废弃产房" in wide or "婴儿床" in wide
    assert "极微距" not in wide


def test_storyboard_shot_regenerate_request_schema_accepts_optional_note() -> None:
    from app.schemas import StoryboardShotRegenerateRequest

    payload = StoryboardShotRegenerateRequest(
        expected_version=3,
        actor="director",
        note="必须与锁定女主同一张脸",
    )
    assert payload.note == "必须与锁定女主同一张脸"
    assert StoryboardShotRegenerateRequest(expected_version=1).note is None
