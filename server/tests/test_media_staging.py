import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import tos

from app.config import get_settings
from app.db.models import Asset, Project
from app.jobs.handlers.video import _prepare_seedance_source, generate_video_take_v2
from app.services.media_staging import (
    MediaStagingError,
    StagedMedia,
    delete_staged_media,
    seedream_fast_path_expires_at,
    seedream_fast_path_usable,
    stage_asset_for_seedance,
)
from app.services.video_provider import GeneratedVideo


def _settings(tmp_path: Path):  # noqa: ANN202
    settings = get_settings()
    return replace(
        settings,
        data_dir=tmp_path,
        ark_api_key="ark-test-key",
        tos_access_key="tos-ak",
        tos_secret_key="tos-sk",
        tos_security_token=None,
        tos_endpoint="tos-cn-beijing.volces.com",
        tos_region="cn-beijing",
        tos_bucket="private-media-bucket",
        tos_presign_ttl_seconds=7200,
        tos_object_prefix="ai-short-drama/media-staging",
        tos_object_expires_days=1,
        tos_cleanup_on_completion=True,
        feature_flags={**settings.feature_flags, "provider_media_staging_v1": True},
    )


def _asset(storage_key: str = "assets/aa/source.png") -> Asset:
    return Asset(
        id="asset-source-1",
        project_id="project-1",
        kind="SHOT_KEYFRAME",
        storage_key=storage_key,
        sha256="a" * 64,
        mime="image/png",
        size_bytes=11,
        status="READY",
        provider="mock",
        is_temporary=False,
        source_entity_type="take",
        source_entity_id="take-source-1",
        created_at=datetime.now(UTC),
    )


class FakeTosClient:
    def __init__(self) -> None:
        self.upload: dict[str, object] | None = None
        self.deleted: tuple[str, str] | None = None

    def put_object(self, bucket: str, key: str, **kwargs):  # noqa: ANN003, ANN201
        self.upload = {
            "bucket": bucket,
            "key": key,
            **kwargs,
            "content": kwargs["content"].read(),
        }
        return SimpleNamespace(request_id="tos-upload-request-1")

    def pre_signed_url(
        self,
        method: tos.HttpMethodType,
        bucket: str,
        key: str,
        *,
        expires: int,
    ):  # noqa: ANN201
        assert method is tos.HttpMethodType.Http_Method_Get
        assert bucket == "private-media-bucket"
        assert expires == 7200
        return SimpleNamespace(
            signed_url=f"https://{bucket}.tos-cn-beijing.volces.com/{key}?X-Tos-Signature=secret"
        )

    def delete_object(self, bucket: str, key: str) -> None:
        self.deleted = (bucket, key)


def test_private_tos_stage_presign_audit_and_cleanup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "source.png"
    source.write_bytes(b"image-bytes")
    asset = _asset()
    client = FakeTosClient()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    staged = stage_asset_for_seedance(
        settings,
        asset=asset,
        source=source,
        job_id="job-1",
        client=client,
        now=now,
    )

    assert client.upload is not None
    assert client.upload["bucket"] == "private-media-bucket"
    assert client.upload["acl"] is tos.ACLType.ACL_Private
    assert client.upload["content_type"] == "image/png"
    assert client.upload["content"] == b"image-bytes"
    assert client.upload["object_expires"] == 1
    assert staged.signed_url.startswith("https://")
    assert staged.expires_at == now + timedelta(hours=2)
    audit_json = json.dumps(staged.audit_metadata())
    assert "X-Tos-Signature" not in audit_json
    assert "signed_url" not in audit_json

    assert delete_staged_media(settings, staged, client=client) is True
    assert client.deleted == (staged.bucket, staged.object_key)


def test_tos_stage_rejects_incomplete_configuration(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), tos_bucket=None)
    source = tmp_path / "source.png"
    source.write_bytes(b"image-bytes")

    with pytest.raises(MediaStagingError) as raised:
        stage_asset_for_seedance(
            settings,
            asset=_asset(),
            source=source,
            job_id="job-1",
            client=FakeTosClient(),
        )

    assert raised.value.code == "TOS_MEDIA_STAGING_CONFIG_MISSING"
    assert raised.value.retryable is False


def test_seedream_original_url_has_a_bounded_fast_path(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), seedream_source_url_fast_path_seconds=600)
    issued_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    expires_at = seedream_fast_path_expires_at(settings, issued_at=issued_at)

    assert expires_at == issued_at + timedelta(minutes=10)
    assert seedream_fast_path_usable(
        "https://seedream.example/temporary.png",
        expires_at.isoformat(),
        now=expires_at - timedelta(seconds=1),
    )
    assert not seedream_fast_path_usable(
        "https://seedream.example/temporary.png",
        expires_at.isoformat(),
        now=expires_at,
    )
    assert not seedream_fast_path_usable(
        "https://seedream.example/temporary.png",
        None,
        now=issued_at,
    )


@pytest.mark.anyio
async def test_fresh_seedream_url_skips_tos_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    asset = _asset()
    job = SimpleNamespace(id="job-fast-path", output_json=None)

    class FakeSession:
        def commit(self) -> None:
            return None

    checkpoints: list[str] = []

    async def checkpoint(_session, _job, _progress, stage: str) -> None:  # noqa: ANN001
        checkpoints.append(stage)

    monkeypatch.setattr(
        "app.jobs.handlers.video.stage_asset_for_seedance",
        lambda *_args, **_kwargs: pytest.fail("fresh Seedream URL must not be staged"),
    )
    source_url = "https://seedream.example/temporary.png"
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    context = SimpleNamespace(settings=settings, checkpoint=checkpoint)

    effective_url, staged = await _prepare_seedance_source(
        context,
        FakeSession(),
        job,
        asset,
        source_url,
        "seedream-original",
        expires_at.isoformat(),
    )

    assert effective_url == source_url
    assert staged is None
    assert checkpoints == ["使用短时有效的 Seedream 原始 URL"]


@pytest.mark.anyio
async def test_video_worker_uses_ephemeral_tos_url_without_persisting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    asset = _asset()
    source = tmp_path / asset.storage_key
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-bytes")
    asset.size_bytes = source.stat().st_size
    project = Project(id="project-1", aspect_ratio="9:16")
    job = SimpleNamespace(id="job-1", project_id="project-1", output_json=None)

    class FakeSession:
        def get(self, model, entity_id):  # noqa: ANN001, ANN201
            if model is Asset and entity_id == asset.id:
                return asset
            if model is Project and entity_id == project.id:
                return project
            return None

        def commit(self) -> None:
            return None

    staged = StagedMedia(
        provider="volcengine-tos",
        bucket="private-media-bucket",
        object_key="media/project-1/job-1/asset-source-1.png",
        signed_url="https://private.example/source.png?X-Tos-Signature=secret",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        source_asset_id=asset.id,
        source_sha256=asset.sha256,
        upload_request_id="tos-request-1",
    )
    monkeypatch.setattr(
        "app.jobs.handlers.video.stage_asset_for_seedance",
        lambda *_args, **_kwargs: staged,
    )
    monkeypatch.setattr(
        "app.jobs.handlers.video.delete_staged_media",
        lambda *_args, **_kwargs: True,
    )

    requested_urls: list[str] = []

    async def checkpoint(*_args) -> None:  # noqa: ANN002
        return None

    async def generate_video(_settings, **kwargs):  # noqa: ANN001, ANN003, ANN201
        requested_urls.append(kwargs["image_url"])
        await kwargs["on_task_created"]("seedance-task-1")
        return GeneratedVideo(
            content=b"video",
            mime="video/mp4",
            duration_ms=5000,
            model="seedance-test",
            request_id="ark-request-1",
            provider_task_id="seedance-task-1",
            source_url="https://video.example/result.mp4",
        )

    captured: dict[str, object] = {}

    def materialize(*_args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        captured.update(kwargs)
        return SimpleNamespace(id="video-asset"), SimpleNamespace(id="video-take"), None

    monkeypatch.setattr("app.jobs.handlers.video.materialize_video_v2", materialize)
    context = SimpleNamespace(
        settings=settings,
        checkpoint=checkpoint,
        heartbeat=lambda *_args: None,
        generate_video=generate_video,
        cancel_video_task=lambda *_args, **_kwargs: None,
    )
    payload = {
        "source_asset_id": asset.id,
        "source_url": "https://seedream.example/temporary.png",
        "prompt": "轻微运镜",
        "duration": 5,
    }

    result = await generate_video_take_v2(context, FakeSession(), job, payload)

    assert requested_urls == [staged.signed_url]
    assert staged.signed_url not in (job.output_json or "")
    assert result["provider"] == "volcengine-ark"
    assert result["media_staging"]["cleanup_status"] == "DELETED"
    assert captured["media_staging_metadata"]["cleanup_status"] == "DELETED"
