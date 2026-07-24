from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.services.storyboards_v2 import (
    _line_character_keys,
    _scene_character_keys,
    build_storyboard_take_prompt,
    mask_character_reference_watermark,
)


def _characters() -> dict[str, SimpleNamespace]:
    return {
        "protagonist": SimpleNamespace(name="林悦"),
        "witness": SimpleNamespace(name="周启"),
    }


def _line(speaker: str, text: str, line_type: str) -> SimpleNamespace:
    return SimpleNamespace(speaker_key=speaker, text=text, line_type=line_type)


def test_action_line_binds_every_character_explicitly_mentioned() -> None:
    characters = _characters()
    line = _line("NARRATOR", "林悦把照片推给周启。", "ACTION")

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["protagonist", "witness"],
    ) == ["protagonist", "witness"]


def test_narration_without_names_inherits_the_scene_cast() -> None:
    characters = _characters()
    lines = [
        _line("NARRATOR", "灯灭了。", "VOICE_OVER"),
        _line("protagonist", "把门锁上。", "DIALOGUE"),
        _line("witness", "已经晚了。", "DIALOGUE"),
    ]
    scene_character_keys = _scene_character_keys(lines, characters)  # type: ignore[arg-type]

    assert scene_character_keys == ["protagonist", "witness"]
    assert _line_character_keys(
        lines[0],
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=scene_character_keys,
    ) == ["protagonist", "witness"]


def test_dialogue_line_keeps_the_speaking_character_when_no_one_else_is_named() -> None:
    characters = _characters()
    line = _line("witness", "已经晚了。", "DIALOGUE")

    assert _line_character_keys(
        line,
        characters_by_key=characters,  # type: ignore[arg-type]
        scene_character_keys=["protagonist", "witness"],
    ) == ["witness"]


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
        description="林悦以警觉状态完成台词，保持与锁定身份参考图为同一人",
        dialogue="Stay one more day.",
        location="Night office",
        time_of_day="夜",
        shot_size="MS",
        camera_movement="TRACK",
        characters=characters,  # type: ignore[arg-type]
    )

    assert "角色身份锁定（硬约束）" in prompt
    assert "林悦" in prompt
    assert "短发亚洲女性" in prompt
    assert "禁止换脸" in prompt
    assert "写实都市夜戏" in prompt
    assert "电影剧照" in prompt
    assert "严禁：居中证件照构图" in prompt
    assert "塑料皮肤" in prompt
    assert "{" not in prompt
    assert "Stay one more day." in prompt


def test_storyboard_shot_regenerate_request_schema_accepts_optional_note() -> None:
    from app.schemas import StoryboardShotRegenerateRequest

    payload = StoryboardShotRegenerateRequest(
        expected_version=3,
        actor="director",
        note="必须与锁定女主同一张脸",
    )
    assert payload.note == "必须与锁定女主同一张脸"
    assert StoryboardShotRegenerateRequest(expected_version=1).note is None
