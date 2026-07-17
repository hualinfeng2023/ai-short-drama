import json
from dataclasses import replace

import httpx
import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.db.models import Project, Shot
from app.seed import SHOT_IDS
from app.services.prompt_enhancer import _call_ark

pytestmark = pytest.mark.anyio


async def test_ark_responses_prompt_enhancement_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/responses"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "doubao-seed-2-0-lite-260215"
        assert payload["thinking"] == {"type": "disabled"}
        assert "原画面描述：她抱着纸箱走出办公楼" in payload["input"]
        assert "只输出最终改写结果" in payload["input"]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "她抱着纸箱走出办公楼，冷雨勾勒出克制而真实的电影光影。",
                            }
                        ],
                    }
                ]
            },
        )

    project = Project(style="现实电影感", aspect_ratio="9:16")
    shot = Shot(
        shot_size="MS",
        camera_movement="DOLLY_IN",
        location="写字楼门口",
        time_of_day="雨夜",
        dialogue="",
    )
    settings = replace(get_settings(), ark_api_key="test-key")
    result = await _call_ark(
        settings,
        project,
        shot,
        "她抱着纸箱走出办公楼",
        transport=httpx.MockTransport(handler),
    )
    assert result.provider == "volcengine-ark"
    assert result.model == "doubao-seed-2-0-lite-260215"
    assert "电影光影" in result.text


async def test_prompt_enhance_endpoint_has_local_fallback(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/prompt-enhance",
        json={"description": "她抱着纸箱走出办公楼"},
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["provider"] == "local-fallback"
    assert result["model"] == "cinematic-prompt-enhancer-v1"
    assert "她抱着纸箱走出办公楼" in result["enhanced"]
    assert "前景" in result["enhanced"]
    assert "避免多余人物" in result["enhanced"]
    assert result["warning"] == "ARK_API_KEY 未配置"
