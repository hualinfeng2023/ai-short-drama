import json
import stat
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.services.provider_settings import provider_settings_path

pytestmark = pytest.mark.anyio


def settings_payload(**ark_overrides: object) -> dict[str, object]:
    ark = {
        "api_key": "ark-secret-12345678",
        "clear_api_key": False,
        "responses_url": "https://ark.example.com/api/v3/responses",
        "prompt_model": "text-model-v2",
        "images_url": "https://ark.example.com/api/v3/images/generations",
        "image_model": "image-model-v5",
        "video_tasks_url": "https://ark.example.com/api/v3/contents/generations/tasks",
        "video_model": "video-model-v1",
        "request_timeout_seconds": 120,
        "video_poll_interval_seconds": 4,
        "video_timeout_seconds": 600,
        "source_url_fast_path_seconds": 480,
        "identity_qc_enabled": True,
        "identity_auto_pass_threshold": 0.9,
    }
    ark.update(ark_overrides)
    return {
        "ark": ark,
        "tos": {
            "enabled": False,
            "access_key": None,
            "clear_access_key": False,
            "secret_key": None,
            "clear_secret_key": False,
            "security_token": None,
            "clear_security_token": False,
            "endpoint": "tos-cn-beijing.volces.com",
            "region": "cn-beijing",
            "bucket": "",
            "presign_ttl_seconds": 7200,
            "object_prefix": "ai-short-drama/media-staging",
            "object_expires_days": 1,
            "cleanup_on_completion": True,
        },
    }


async def test_provider_settings_are_persisted_server_side_and_secrets_are_masked(
    client: AsyncClient,
) -> None:
    before = (await client.get("/api/v1/settings/providers")).json()["data"]
    assert before["ark"]["api_key_configured"] is False
    assert before["storage"]["secrets_returned"] is False

    saved = await client.patch("/api/v1/settings/providers", json=settings_payload())
    assert saved.status_code == 200
    body = saved.json()["data"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert "ark-secret-12345678" not in serialized
    assert body["ark"]["api_key_configured"] is True
    assert body["ark"]["api_key_hint"] == "••••5678"
    assert body["ark"]["api_key_source"] == "saved"
    assert body["ark"]["image_model"] == "image-model-v5"

    settings = get_settings()
    assert settings.ark_api_key == "ark-secret-12345678"
    assert settings.ark_prompt_model == "text-model-v2"
    path = provider_settings_path(settings.data_dir)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "ark-secret-12345678" in path.read_text(encoding="utf-8")

    capabilities = (await client.get("/meta/config")).json()["data"]["capabilities"]
    assert capabilities["provider_calls"] is True
    assert capabilities["image_model"] == "image-model-v5"


async def test_secret_can_be_preserved_or_explicitly_cleared(client: AsyncClient) -> None:
    assert (
        await client.patch("/api/v1/settings/providers", json=settings_payload())
    ).status_code == 200

    preserved = settings_payload(api_key=None, prompt_model="text-model-v3")
    response = await client.patch("/api/v1/settings/providers", json=preserved)
    assert response.status_code == 200
    assert get_settings().ark_api_key == "ark-secret-12345678"
    assert get_settings().ark_prompt_model == "text-model-v3"

    cleared = settings_payload(api_key=None, clear_api_key=True)
    response = await client.patch("/api/v1/settings/providers", json=cleared)
    assert response.status_code == 200
    assert response.json()["data"]["ark"]["api_key_configured"] is False
    assert get_settings().ark_api_key is None


async def test_incomplete_tos_configuration_is_rejected_without_enabling_staging(
    client: AsyncClient,
) -> None:
    payload = settings_payload()
    payload["tos"]["enabled"] = True  # type: ignore[index]

    response = await client.patch("/api/v1/settings/providers", json=payload)

    assert response.status_code == 422
    assert get_settings().feature_flags["provider_media_staging_v1"] is False


async def test_provider_connection_endpoint_uses_saved_configuration(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        await client.patch("/api/v1/settings/providers", json=settings_payload())
    ).status_code == 200
    observed: dict[str, str | None] = {}

    async def fake_test(settings) -> dict[str, object]:  # noqa: ANN001
        observed["api_key"] = settings.ark_api_key
        return {
            "provider": "volcengine-ark",
            "status": "connected",
            "message": "方舟 API 连接成功",
        }

    monkeypatch.setattr("app.api.v1.provider_settings.test_ark_provider", fake_test)
    response = await client.post(
        "/api/v1/settings/providers/test",
        json={"provider": "ark"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "connected"
    assert observed["api_key"] == "ark-secret-12345678"


async def test_provider_settings_reject_credentials_embedded_in_endpoint(
    client: AsyncClient,
) -> None:
    response = await client.patch(
        "/api/v1/settings/providers",
        json=settings_payload(responses_url="https://user:password@example.com/api/v3/responses"),
    )

    assert response.status_code == 422
    assert not provider_settings_path(Path(get_settings().data_dir)).exists()
