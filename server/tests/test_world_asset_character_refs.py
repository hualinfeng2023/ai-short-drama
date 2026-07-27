"""世界资产参考图：角色关联与提示词预览/覆盖。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import (
    WorldAssetImageGenerateRequest,
    WorldAssetImagePromptPreviewRequest,
)
from app.services.preproduction import build_world_asset_image_prompts


def test_world_asset_prompt_preview_request_rejects_more_than_three_characters() -> None:
    with pytest.raises(ValidationError):
        WorldAssetImagePromptPreviewRequest(
            expected_version=1,
            character_ids=[
                "00000000-0000-4000-8000-000000000001",
                "00000000-0000-4000-8000-000000000002",
                "00000000-0000-4000-8000-000000000003",
                "00000000-0000-4000-8000-000000000004",
            ],
        )


def test_world_asset_generate_request_dedupes_character_ids() -> None:
    payload = WorldAssetImageGenerateRequest(
        expected_version=1,
        character_ids=[
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
        ],
    )
    assert payload.character_ids == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]


def test_build_world_asset_prompts_keeps_no_person_when_unlinked() -> None:
    record = SimpleNamespace(
        name="旧照片",
        payload_json='{"material":"纸质照片"}',
    )
    built = build_world_asset_image_prompts(
        asset_type="prop",
        record=record,  # type: ignore[arg-type]
        count=2,
    )
    assert "无人物" in str(built["base_prompt"])
    assert "角色形象锁定" not in str(built["base_prompt"])
    assert len(built["variants"]) == 2
    assert built["variants"][0]["style_id"] == "studio-realism"
    assert str(built["base_prompt"]) in str(built["variants"][0]["prompt"])


def test_build_world_asset_prompts_links_characters_and_custom_base() -> None:
    record = SimpleNamespace(
        name="旧照片",
        payload_json='{"material":"纸质照片"}',
    )
    characters = [
        SimpleNamespace(
            name="林晚",
            role="主角",
            visual_brief="短发，冷静",
        )
    ]
    custom = "自定义关键道具提示词，覆盖默认组装并保留至少二十个汉字。"
    built = build_world_asset_image_prompts(
        asset_type="prop",
        record=record,  # type: ignore[arg-type]
        count=1,
        characters=characters,  # type: ignore[arg-type]
        custom_base_prompt=custom,
    )
    assert built["base_prompt"] == custom
    assert "无人物" not in str(built["default_base_prompt"])
    assert "角色形象锁定" in str(built["default_base_prompt"])
    assert "允许道具上呈现关联角色" in str(built["default_base_prompt"])
    assert custom in str(built["variants"][0]["prompt"])


def test_build_location_prompts_softens_empty_scene_when_characters_linked() -> None:
    record = SimpleNamespace(
        name="暴雨天台",
        payload_json='{"mood":"压抑"}',
    )
    characters = [
        SimpleNamespace(name="林晚", role="主角", visual_brief=""),
    ]
    built = build_world_asset_image_prompts(
        asset_type="location",
        record=record,  # type: ignore[arg-type]
        count=1,
        characters=characters,  # type: ignore[arg-type]
    )
    assert "无人" not in str(built["base_prompt"])
    assert "角色形象锁定" in str(built["base_prompt"])
    assert "不强制必须入镜" in str(built["base_prompt"])
