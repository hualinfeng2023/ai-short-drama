from types import SimpleNamespace

from app.services.storyboards_v2 import _line_character_keys, _scene_character_keys


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
