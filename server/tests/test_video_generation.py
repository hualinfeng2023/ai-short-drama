import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, Job, Shot, Take
from app.db.session import get_engine
from app.jobs.worker import PersistentJobWorker
from app.seed import PROJECT_ID, SHOT_IDS
from app.services.video_provider import GeneratedVideo, generate_video
from app.services.videos import _seedream_fast_path

pytestmark = pytest.mark.anyio


async def _append_async(values: list[str], value: str) -> None:
    values.append(value)


async def test_legacy_seedream_url_is_selected_only_before_its_explicit_expiry() -> None:
    settings = replace(get_settings(), seedream_source_url_fast_path_seconds=600)
    now = datetime.now(UTC)
    job = SimpleNamespace(
        output_json=json.dumps(
            {
                "take_version": 2,
                "source_url": "https://seedream.example/temporary.png",
                "source_url_fast_path_expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        ),
        completed_at=now,
        created_at=now,
    )

    class ScalarResult:
        def all(self):  # noqa: ANN201
            return [job]

    class FakeSession:
        def scalars(self, _query):  # noqa: ANN001, ANN201
            return ScalarResult()

    selected = _seedream_fast_path(FakeSession(), settings, "shot-1", 2)
    assert selected is not None
    assert selected.url == "https://seedream.example/temporary.png"

    job.output_json = json.dumps(
        {
            "take_version": 2,
            "source_url": "https://seedream.example/temporary.png",
            "source_url_fast_path_expires_at": (now - timedelta(seconds=1)).isoformat(),
        }
    )
    assert _seedream_fast_path(FakeSession(), settings, "shot-1", 2) is None


async def test_seedance_rest_contract_poll_and_download() -> None:
    requests: list[httpx.Request] = []
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        requests.append(request)
        if request.method == "POST":
            assert request.headers["Authorization"] == "Bearer test-key"
            assert json.loads(request.content) == {
                "model": "doubao-seedance-1-5-pro-251215",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "无人机快速穿越峡谷 --duration 5 --camerafixed false --watermark true"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://image.test/source.png"},
                    },
                ],
            }
            return httpx.Response(200, json={"id": "task-video-1"})
        if request.url.host == "video.test":
            return httpx.Response(
                200,
                content=b"generated-mp4",
                headers={"content-type": "video/mp4"},
            )
        poll_count += 1
        if poll_count == 1:
            return httpx.Response(200, json={"id": "task-video-1", "status": "queued"})
        return httpx.Response(
            200,
            headers={"x-request-id": "ark-video-request-1"},
            json={
                "id": "task-video-1",
                "status": "succeeded",
                "model": "doubao-seedance-1-5-pro-251215",
                "duration": 5,
                "content": {"video_url": "https://video.test/result.mp4"},
            },
        )

    created: list[str] = []
    statuses: list[str] = []
    settings = replace(
        get_settings(),
        ark_api_key="test-key",
        ark_video_poll_interval_seconds=0,
    )
    result = await generate_video(
        settings,
        prompt="无人机快速穿越峡谷 --duration 5 --camerafixed false --watermark true",
        image_url="https://image.test/source.png",
        on_task_created=lambda task_id: _append_async(created, task_id),
        on_poll=lambda status: _append_async(statuses, status),
        transport=httpx.MockTransport(handler),
    )

    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]
    assert created == ["task-video-1"]
    assert statuses == ["queued"]
    assert result.content == b"generated-mp4"
    assert result.duration_ms == 5000
    assert result.provider_task_id == "task-video-1"
    assert result.request_id == "ark-video-request-1"


async def test_shot_video_job_persists_provider_task_and_asset(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate_video(
        _settings,  # noqa: ANN001
        *,
        prompt: str,
        image_url: str,
        provider_task_id: str | None = None,
        on_task_created=None,  # noqa: ANN001
        on_poll=None,  # noqa: ANN001
    ) -> GeneratedVideo:
        assert provider_task_id is None
        assert image_url == "https://image.test/source.png"
        assert prompt.endswith("--duration 5 --camerafixed false --watermark true")
        assert on_task_created is not None
        assert on_poll is not None
        await on_task_created("task-video-persisted")
        await on_poll("running")
        return GeneratedVideo(
            content=b"persisted-video-bytes",
            mime="video/mp4",
            duration_ms=5000,
            model="doubao-seedance-1-5-pro-251215",
            request_id="ark-video-request-2",
            provider_task_id="task-video-persisted",
            source_url="https://video.test/persisted.mp4",
        )

    monkeypatch.setattr("app.jobs.worker.generate_video", fake_generate_video)
    endpoint = f"/api/v1/shots/{SHOT_IDS[0]}/video-takes"
    created = await client.post(
        endpoint,
        json={
            "prompt": "无人机以极快速度穿越复杂障碍",
            "image_url": "https://image.test/source.png",
            "duration": 5,
            "camera_fixed": False,
            "watermark": True,
        },
        headers={"Idempotency-Key": "shot-video-test-v1"},
    )
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert job["job_type"] == "GENERATE_SHOT_VIDEO"

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    completed = (await client.get(f"/api/v1/jobs/{job['id']}")).json()["data"]
    assert completed["status"] == "SUCCEEDED"

    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/workspace")).json()["data"]
    shot = next(item for item in workspace["shots"] if item["id"] == SHOT_IDS[0])
    assert shot["current_video_url"].endswith("/content")
    video = await client.get(shot["current_video_url"])
    assert video.status_code == 200
    assert video.content == b"persisted-video-bytes"
    assert video.headers["content-type"] == "video/mp4"

    with Session(get_engine(get_settings().database_url)) as session:
        persisted_job = session.get(Job, job["id"])
        assert persisted_job is not None
        output = json.loads(persisted_job.output_json or "{}")
        assert output["provider_task_id"] == "task-video-persisted"
        take = session.scalar(
            select(Take).where(
                Take.shot_id == SHOT_IDS[0],
                Take.kind == "VIDEO",
                Take.version == 2,
            )
        )
        assert take is not None and take.is_current is False
        asset = session.get(Asset, take.asset_id)
        assert asset is not None
        assert asset.provider == "volcengine-ark"
        assert asset.duration_ms == 5000


async def test_shot_video_job_accepts_local_keyframe_when_private_tos_is_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_MEDIA_STAGING_V1", "1")
    monkeypatch.setenv("TOS_ACCESS_KEY", "tos-ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "tos-sk")
    monkeypatch.setenv("TOS_BUCKET", "private-media-bucket")
    settings = get_settings()
    image_bytes = b"approved-local-keyframe"
    digest = sha256(image_bytes).hexdigest()
    storage_key = f"assets/{digest[:2]}/{digest}.png"
    image_path = settings.data_dir / storage_key
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image_bytes)
    with Session(get_engine(settings.database_url)) as session:
        asset = Asset(
            id=str(uuid4()),
            project_id=PROJECT_ID,
            kind="SHOT_IMAGE",
            storage_key=storage_key,
            sha256=digest,
            mime="image/png",
            size_bytes=len(image_bytes),
            status="READY",
            provider="mock",
            is_temporary=False,
            source_entity_type="shot",
            source_entity_id=SHOT_IDS[0],
            created_at=datetime.now(UTC),
        )
        shot = session.get(Shot, SHOT_IDS[0])
        assert shot is not None
        take = Take(
            id=str(uuid4()),
            shot_id=SHOT_IDS[0],
            kind="STILL",
            version=shot.current_take,
            asset_id=asset.id,
            status="READY",
            approval="APPROVED",
            is_current=True,
            created_at=datetime.now(UTC),
        )
        shot.current_take_id = take.id
        session.add_all([asset, take])
        session.commit()
    endpoint = f"/api/v1/shots/{SHOT_IDS[0]}/video-takes"

    created = await client.post(
        endpoint,
        json={
            "prompt": "人物轻轻抬头，镜头缓慢推进",
            "duration": 5,
            "camera_fixed": False,
            "watermark": True,
        },
        headers={"Idempotency-Key": "shot-video-tos-staging-v1"},
    )

    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]
    with Session(get_engine(get_settings().database_url)) as session:
        job = session.get(Job, job_id)
        assert job is not None
        payload = json.loads(job.input_json)
        assert payload["image_url"] is None
        assert isinstance(payload["source_asset_id"], str)
        assert "X-Tos-Signature" not in job.input_json
