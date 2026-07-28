"""单帧拆镜、可见范围与冲突检测的验收测试。"""

from types import SimpleNamespace

from app.services.shot_frames import (
    RENDER_MODE_BLACK_FRAME,
    RENDER_MODE_IMAGE,
    SCALE_MACRO,
    SCALE_WIDE,
    VISIBILITY_FACE_VISIBLE,
    VISIBILITY_HANDS_ONLY,
    VISIBILITY_OFF_SCREEN,
    blocking_conflicts,
    compile_static_frame_brief,
    detect_frame_conflicts,
    resolve_visibility,
    split_visual_moments,
)
from app.services.storyboards_v2 import build_storyboard_take_prompt

ACCEPTANCE_TEXT = (
    "开场抛出核心钩子，交代背景设定。黑场，三声间隔清晰的有力胎心响起，"
    "极微距镜头拍摄胚胎舱表面，一滴冷凝水随胎心轻轻震动，冰霜覆盖玻璃，"
    "内部可见微弱暖光的胚胎，镜头拉焦露出整间废弃产房，两侧排列蒙尘空婴儿床。"
    "医生全程不露脸，仅有平静画外音。"
)


def test_acceptance_case_splits_into_black_macro_and_wide() -> None:
    split = split_visual_moments(ACCEPTANCE_TEXT)

    assert len(split.beats) >= 3
    assert "医生" in split.offscreen_names

    black = next(beat for beat in split.beats if beat.render_mode == RENDER_MODE_BLACK_FRAME)
    assert black.visual == "黑场" or "黑场" in black.visual
    assert any("胎心" in cue for cue in black.audio_cues)

    macro = next(beat for beat in split.beats if beat.scale == SCALE_MACRO)
    assert macro.render_mode == RENDER_MODE_IMAGE
    assert "胚胎舱" in macro.visual or "冷凝水" in macro.visual or "冰霜" in macro.visual
    assert "废弃产房" not in macro.visual
    assert "婴儿床" not in macro.visual
    assert "医生" not in macro.visual
    assert "拉焦" not in macro.visual
    assert "胎心响起" not in macro.visual

    wide = next(beat for beat in split.beats if beat.scale == SCALE_WIDE)
    assert "废弃产房" in wide.visual or "婴儿床" in wide.visual
    assert "极微距" not in wide.visual
    assert any("拉焦" in note or "露出" in note for note in wide.camera_notes)
    assert "拉焦" not in wide.visual


def test_compile_static_frame_brief_prefers_matching_scale() -> None:
    close = compile_static_frame_brief(ACCEPTANCE_TEXT, shot_size="CU")
    wide = compile_static_frame_brief(ACCEPTANCE_TEXT, shot_size="WS")

    assert "胎心" not in close
    assert "黑场" not in close
    assert "拉焦" not in close
    assert "胚胎舱" in close or "冰霜" in close or "冷凝水" in close
    assert "废弃产房" not in close

    assert "胎心" not in wide
    assert "拉焦" not in wide
    assert "废弃产房" in wide or "婴儿床" in wide
    assert "极微距" not in wide


def test_doctor_visibility_is_off_screen_for_acceptance_beats() -> None:
    split = split_visual_moments(ACCEPTANCE_TEXT)
    brief = "全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏"
    for beat in split.beats:
        visibility = resolve_visibility(
            character_name="医生",
            visual_brief=brief,
            frame_text=beat.visual,
            delivery="ACTION",
            offscreen_names=split.offscreen_names,
            face_hidden_names=split.face_hidden_names,
        )
        assert visibility == VISIBILITY_OFF_SCREEN


def test_hands_only_visibility_when_gloves_in_frame() -> None:
    visibility = resolve_visibility(
        character_name="医生",
        visual_brief="全程不露出面部，仅出现戴无菌白手套的双手",
        frame_text="戴无菌白手套的双手按在胚胎舱边缘",
        delivery="ACTION",
        face_hidden_names=("医生",),
    )
    assert visibility == VISIBILITY_HANDS_ONLY


def test_blocking_conflict_when_macro_and_wide_in_one_frame() -> None:
    conflicts = detect_frame_conflicts(
        frame_text="极微距拍摄舱面，同时展现整间废弃产房广角全景",
        shot_size="CU",
        camera_movement="STATIC",
        visible_character_count=0,
        face_visible_count=0,
        identity_reference_count=0,
    )
    codes = {item.code for item in conflicts}
    assert "SCALE_MACRO_AND_WIDE" in codes
    assert blocking_conflicts(conflicts)


def test_acceptance_prompts_exclude_doctor_identity_and_non_visual() -> None:
    split = split_visual_moments(ACCEPTANCE_TEXT)
    project = SimpleNamespace(
        style="realistic_cinematic",
        aspect_ratio="9:16",
        genre="sci_fi",
        idea="",
    )
    doctor = SimpleNamespace(
        name="医生",
        role="规则执行者",
        visual_brief="全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏",
    )

    macro = next(beat for beat in split.beats if beat.scale == SCALE_MACRO)
    prompt = build_storyboard_take_prompt(
        project,  # type: ignore[arg-type]
        description=macro.visual,
        dialogue="",
        location="废弃人类胚胎储存产房",
        time_of_day="午夜",
        shot_size="CU",
        camera_movement="STATIC",
        characters=[doctor],  # type: ignore[arg-type]
        delivery="ACTION",
        frame=macro,
        visibility_by_name={"医生": VISIBILITY_OFF_SCREEN},
        absent_character_names=("医生",),
    )

    assert "单帧静帧规格" in prompt
    assert "冷凝水" in prompt or "冰霜" in prompt or "胚胎舱" in prompt
    assert "胎心响起" not in prompt
    assert "拉焦" not in prompt
    assert "废弃产房" not in prompt
    assert "婴儿床" not in prompt
    assert "唇形" not in prompt
    assert "毛孔" not in prompt
    # 不可见角色不写身份约束，只保留无人出镜的画面规则
    assert "医生" not in prompt
    assert "角色身份锁定" not in prompt
    assert "画面中不出现任何人物" in prompt


def test_leak_case_splits_01b_macro_and_01c_wide() -> None:
    """使用案例：Story Context 不得泄漏进 Shot 01B / 01C。"""
    from app.services.storyboards_v2 import assemble_shot_prompt

    source = "永生312年、人口守恒法、医生画外音、废弃产房、冷凝水微距、随后拉远"
    idea = (
        "核心设定：人口守恒法。故事梗概：永生三百一十二年。"
        "整体视觉：冷青色、银灰色。"
    )
    project = SimpleNamespace(
        style="realistic_cinematic",
        aspect_ratio="9:16",
        genre="sci_fi",
        idea=idea,
    )
    doctor = SimpleNamespace(
        name="医生",
        role="规则执行者",
        visual_brief="平静画外音",
    )
    split = split_visual_moments(source)
    assert "医生" in split.offscreen_names
    assert any("人口守恒" in item or "永生312" in item for item in split.story_context_removed)

    macro = next(beat for beat in split.beats if beat.scale == SCALE_MACRO)
    wide = next(beat for beat in split.beats if beat.scale == SCALE_WIDE)
    assert "冷凝水" in macro.visual
    assert "废弃产房" not in macro.visual
    assert "废弃产房" in wide.visual
    assert "冷凝水" not in wide.visual

    shot_01b = assemble_shot_prompt(
        project,  # type: ignore[arg-type]
        description=source,
        dialogue="",
        location="废弃未来医疗储存房",
        time_of_day="午夜",
        shot_size="CU",
        camera_movement="STATIC",
        characters=[doctor],  # type: ignore[arg-type]
        delivery="VOICE_OVER",
        frame=macro,
        visibility_by_name={"医生": VISIBILITY_OFF_SCREEN},
        absent_character_names=("医生",),
        conflicts=[],
    )
    shot_01c = assemble_shot_prompt(
        project,  # type: ignore[arg-type]
        description=source,
        dialogue="",
        location="废弃未来医疗储存房",
        time_of_day="午夜",
        shot_size="WS",
        camera_movement="STATIC",
        characters=[doctor],  # type: ignore[arg-type]
        delivery="VOICE_OVER",
        frame=wide,
        visibility_by_name={"医生": VISIBILITY_OFF_SCREEN},
        absent_character_names=("医生",),
        conflicts=[],
    )

    for label, prompt in (("01B", shot_01b.image_prompt), ("01C", shot_01c.image_prompt)):
        assert "人口守恒法" not in prompt, label
        assert "312年" not in prompt and "永生312" not in prompt, label
        assert "医生" not in prompt, label
        assert "表情" not in prompt, label
        assert "胎心" not in prompt, label
        assert "拉焦" not in prompt and "拉远" not in prompt, label

    assert "冷凝水" in shot_01b.image_prompt
    assert "100mm macro" in shot_01b.image_prompt or "微距" in shot_01b.image_prompt
    assert "废弃产房" not in shot_01b.image_prompt

    assert "废弃" in shot_01c.image_prompt
    assert "广角" in shot_01c.image_prompt
    assert "冷凝水" not in shot_01c.image_prompt
    assert shot_01b.debug.included
    assert any(item.field == "global_creative_bible" for item in shot_01b.debug.removed)


def test_face_hidden_brief_does_not_force_hands_on_empty_wide_shot() -> None:
    """隐面角色 brief 含「双手」时，空产房广角不得因此入镜。"""
    visibility = resolve_visibility(
        character_name="医生",
        visual_brief="全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏",
        frame_text="整间废弃产房，两侧排列蒙尘空婴儿床",
        delivery="ACTION",
    )
    assert visibility == VISIBILITY_OFF_SCREEN

    project = SimpleNamespace(
        style="realistic_cinematic",
        aspect_ratio="9:16",
        genre="sci_fi",
        idea="核心设定：人口守恒法。故事梗概：永生三百一十二年。",
    )
    doctor = SimpleNamespace(
        name="医生",
        role="规则执行者",
        visual_brief="全程不露出面部，仅出现戴无菌白手套的双手，声音平静无起伏",
    )
    prompt = build_storyboard_take_prompt(
        project,  # type: ignore[arg-type]
        description="整间废弃产房，两侧排列蒙尘空婴儿床",
        dialogue="",
        location="废弃人类胚胎储存产房",
        time_of_day="午夜",
        shot_size="WS",
        camera_movement="STATIC",
        characters=[doctor],  # type: ignore[arg-type]
        delivery="ACTION",
    )
    assert "时代与世界观" not in prompt
    assert "人口守恒法" not in prompt
    assert "医生" not in prompt
    assert "角色身份锁定" not in prompt
    assert "脸型" not in prompt
    assert "皮肤保留毛孔" not in prompt
    assert "表情克制" not in prompt
    assert "整体风格延续" not in prompt
    assert "废弃产房" in prompt or "婴儿床" in prompt
    assert "画面中不出现任何人物" in prompt


def test_voice_over_off_screen_without_named_frame() -> None:
    visibility = resolve_visibility(
        character_name="医生",
        visual_brief="平静画外音",
        frame_text="整间废弃产房，两侧排列蒙尘空婴儿床",
        delivery="VOICE_OVER",
    )
    assert visibility == VISIBILITY_OFF_SCREEN


def test_face_visible_default() -> None:
    visibility = resolve_visibility(
        character_name="林悦",
        visual_brief="短发亚洲女性",
        frame_text="林悦站在窗边",
        delivery="DIALOGUE",
    )
    assert visibility == VISIBILITY_FACE_VISIBLE


def test_blocking_conflicts_helper_filters() -> None:
    from app.services.shot_frames import CONFLICT_BLOCKING, CONFLICT_NORMALIZED, FrameConflict

    items = (
        FrameConflict("A", CONFLICT_BLOCKING, "must split"),
        FrameConflict("B", CONFLICT_NORMALIZED, "auto fix"),
    )
    blocked = blocking_conflicts(items)
    assert len(blocked) == 1
    assert blocked[0].code == "A"
