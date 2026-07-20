import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Asset, Job, Take
from app.db.session import get_engine
from app.jobs.contracts import JobExecutionContext, JobExecutionError
from app.jobs.handlers.production import _generate_character_image
from app.jobs.worker import PersistentJobWorker
from app.seed import PROJECT_ID, SHOT_IDS
from app.services.character_image_qc import evaluate_character_image_quality
from app.services.character_visuals import CHARACTER_CLEAN_FRAME_CONSTRAINT
from app.services.identity_consistency import evaluate_identity_consistency
from app.services.image_provider import GeneratedImage, generate_image

pytestmark = pytest.mark.anyio


async def test_character_reference_prompt_forbids_reproducing_text_or_marks() -> None:
    captured_prompt = ""

    async def generate_clean_image(
        _settings: object,
        prompt: str,
        **_kwargs: object,
    ) -> GeneratedImage:
        nonlocal captured_prompt
        captured_prompt = prompt
        return GeneratedImage(
            content=b"clean-character-image",
            mime="image/png",
            width=1024,
            height=1024,
            model="test-image-model",
            request_id=None,
            source_url=None,
        )

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    context = JobExecutionContext(
        settings=get_settings(),
        worker_id="clean-frame-test",
        checkpoint=no_op,
        record_diagnostics=no_op,
        heartbeat=lambda *_args: None,
        generate_image=generate_clean_image,
        evaluate_identity_consistency=generate_clean_image,
        generate_video=generate_clean_image,
        cancel_video_task=generate_clean_image,
    )

    await _generate_character_image(
        context,
        Session(),
        Job(id="clean-frame-job"),
        {"prompt": "严格保持参考人物身份", "seed": 42},
        reference_asset_ids=[],
    )

    assert CHARACTER_CLEAN_FRAME_CONSTRAINT in captured_prompt
    assert "尤其右下角水印" in captured_prompt
    assert "不能临摹、复制或迁移到结果图中" in captured_prompt


async def test_character_identity_image_generation_has_a_hard_timeout() -> None:
    async def never_complete(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    context = JobExecutionContext(
        settings=get_settings(),
        worker_id="timeout-test",
        checkpoint=no_op,
        record_diagnostics=no_op,
        heartbeat=lambda *_args: None,
        generate_image=never_complete,
        evaluate_identity_consistency=never_complete,
        generate_video=never_complete,
        cancel_video_task=never_complete,
    )

    with pytest.raises(JobExecutionError) as raised:
        await _generate_character_image(
            context,
            Session(),
            Job(id="timeout-job"),
            {"prompt": "角色身份全身照", "seed": 42},
            reference_asset_ids=[],
            max_wait_seconds=0,
        )

    assert raised.value.code == "CHARACTER_IMAGE_TIMEOUT"
    assert raised.value.retryable is False


async def test_missing_key_uses_deterministic_mock_image() -> None:
    settings = replace(get_settings(), ark_api_key=None)
    first = await generate_image(settings, "同一个镜头提示")
    replay = await generate_image(settings, "同一个镜头提示")
    changed = await generate_image(settings, "另一个镜头提示")

    assert first.model == "deterministic-image-v1"
    assert first.mime == "image/png"
    assert (first.width, first.height) == (360, 640)
    assert first.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert replay.content == first.content
    assert changed.content != first.content


async def test_seedream_rest_request_and_image_download() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/images/generations"):
            assert request.headers["Authorization"] == "Bearer test-key"
            payload = json.loads(request.content)
            assert payload == {
                "model": "doubao-seedream-4-5-251128",
                "prompt": "测试电影镜头",
                "sequential_image_generation": "disabled",
                "response_format": "url",
                "size": "4K",
                "stream": False,
                "watermark": False,
                "image": ["data:image/png;base64,cmVmZXJlbmNl"],
                "seed": 24680,
            }
            return httpx.Response(
                200,
                headers={"x-request-id": "ark-request-1"},
                json={
                    "model": "doubao-seedream-4-5-251128",
                    "data": [
                        {
                            "url": "https://image.test/result.png",
                            "width": 1440,
                            "height": 2560,
                        }
                    ],
                },
            )
        return httpx.Response(200, content=b"generated-png", headers={"content-type": "image/png"})

    settings = replace(get_settings(), ark_api_key="test-key")
    result = await generate_image(
        settings,
        "测试电影镜头",
        model="doubao-seedream-4-5-251128",
        size="4K",
        reference_images=["data:image/png;base64,cmVmZXJlbmNl"],
        seed=24680,
        transport=httpx.MockTransport(handler),
    )
    assert len(requests) == 2
    assert result.content == b"generated-png"
    assert (result.width, result.height) == (1440, 2560)
    assert result.request_id == "ark-request-1"


async def test_seedream_rest_normalizes_seed_to_ark_int32_range() -> None:
    oversized_seed = 4_258_230_020

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/images/generations"):
            payload = json.loads(request.content)
            assert payload["model"] == "doubao-seedream-5-0-260128"
            assert payload["watermark"] is False
            assert payload["seed"] == oversized_seed % (2**31)
            return httpx.Response(
                200,
                json={
                    "model": "doubao-seedream-5-0-260128",
                    "data": [{"url": "https://image.test/result.png"}],
                },
            )
        return httpx.Response(200, content=b"generated-png", headers={"content-type": "image/png"})

    settings = replace(get_settings(), ark_api_key="test-key")
    result = await generate_image(
        settings,
        "测试角色候选图",
        model="doubao-seedream-5-0-260128",
        seed=oversized_seed,
        transport=httpx.MockTransport(handler),
    )

    assert result.model == "doubao-seedream-5-0-260128"
    assert result.content == b"generated-png"


async def test_identity_qc_auto_passes_only_above_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "doubao-seed-2-0-lite-260215"
        content = payload["input"][0]["content"]
        assert [item["type"] for item in content] == [
            "input_text",
            "input_image",
            "input_image",
        ]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "same_identity": True,
                                        "confidence": 0.93,
                                        "reason": "脸型与核心五官一致",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    settings = replace(
        get_settings(),
        ark_api_key="test-key",
        ark_identity_qc_enabled=True,
        ark_identity_auto_pass_threshold=0.88,
    )
    result = await evaluate_identity_consistency(
        settings,
        reference_images=["data:image/png;base64,cmVmZXJlbmNl"],
        generated_image=GeneratedImage(
            content=b"generated",
            mime="image/png",
            width=100,
            height=100,
            model="seedream-test",
            request_id=None,
        ),
        character_labels=["林悦"],
        transport=httpx.MockTransport(handler),
    )
    assert result.status == "PASSED"
    assert result.score == 0.93
    assert result.provider == "volcengine-ark"


async def test_character_generation_qc_records_four_independent_visual_checks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "doubao-seed-2-0-lite-260215"
        content = payload["input"][0]["content"]
        assert [item["type"] for item in content] == ["input_text", "input_image"]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "watermark": {
                                            "passed": True,
                                            "confidence": 0.98,
                                            "reason": "未发现文字或标记",
                                        },
                                        "foreground_occlusion": {
                                            "passed": False,
                                            "confidence": 0.91,
                                            "reason": "前景物体遮挡右手",
                                        },
                                        "body_continuity": {
                                            "passed": True,
                                            "confidence": 0.95,
                                            "reason": "身体结构连续",
                                        },
                                        "pure_white_background": {
                                            "passed": False,
                                            "confidence": 0.89,
                                            "reason": "背景存在灰色渐变",
                                        },
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    settings = replace(
        get_settings(),
        ark_api_key="test-key",
        ark_prompt_model="doubao-seed-2-0-lite-260215",
    )
    result = await evaluate_character_image_quality(
        settings,
        GeneratedImage(
            content=b"generated-character",
            mime="image/png",
            width=1024,
            height=1024,
            model="seedream-test",
            request_id=None,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "FAILED"
    assert [item.check_type for item in result.checks] == [
        "WATERMARK_FREE",
        "FOREGROUND_CLEAR",
        "BODY_CONTINUITY",
        "PURE_WHITE_BACKGROUND",
    ]
    assert [item.status for item in result.checks] == [
        "PASSED",
        "FAILED",
        "PASSED",
        "FAILED",
    ]


async def test_shot_generation_job_persists_and_applies_take(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(
        _settings,
        prompt: str,
        *,
        model: str | None = None,  # noqa: ANN001
        size: str = "2K",
        reference_images: list[str] | None = None,
        seed: int | None = None,
    ) -> GeneratedImage:
        assert "电影大片质感" in prompt
        assert "当前造型版本：Look V2" in prompt
        assert "严格使用 21:9 画面比例" in prompt
        assert model == "doubao-seedream-4-5-251128"
        assert size == "4K"
        assert reference_images and reference_images[0].startswith("data:image/png;base64,")
        assert isinstance(seed, int) and seed >= 0
        return GeneratedImage(
            content=b"generated-image-bytes",
            mime="image/png",
            width=1440,
            height=2560,
            model="doubao-seedream-4-5-251128",
            request_id="ark-request-2",
            source_url="https://image.test/source.png",
        )

    monkeypatch.setattr("app.jobs.worker.generate_image", fake_generate)
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    endpoint = f"/api/v1/shots/{SHOT_IDS[0]}/takes"
    initial_workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/workspace")).json()[
        "data"
    ]
    initial_shot = next(item for item in initial_workspace["shots"] if item["id"] == SHOT_IDS[0])
    binding_update = await client.put(
        f"/api/v1/shots/{SHOT_IDS[0]}/character-bindings",
        json={
            "expected_version": initial_shot["lock_version"],
            "character_ids": initial_shot["character_ids"],
            "look_version": "Look V2",
        },
    )
    assert binding_update.status_code == 200
    assert binding_update.json()["data"]["character_look_version"] == "Look V2"

    missing_key = await client.post(endpoint, json={})
    assert missing_key.status_code == 422

    invalid_model = await client.post(
        endpoint,
        json={"model": "not-a-real-image-model"},
        headers={"Idempotency-Key": "shot-image-invalid-model"},
    )
    assert invalid_model.status_code == 422
    assert invalid_model.json()["error"]["code"] == "UNSUPPORTED_IMAGE_MODEL"

    invalid_resolution = await client.post(
        endpoint,
        json={"model": "doubao-seedream-5-0-260128", "resolution": "4K"},
        headers={"Idempotency-Key": "shot-image-invalid-resolution"},
    )
    assert invalid_resolution.status_code == 422
    assert invalid_resolution.json()["error"]["code"] == "UNSUPPORTED_IMAGE_RESOLUTION"

    created = await client.post(
        endpoint,
        json={
            "model": "doubao-seedream-4-5-251128",
            "resolution": "4K",
            "aspect_ratio": "21:9",
        },
        headers={"Idempotency-Key": "shot-image-test-v1"},
    )
    assert created.status_code == 202
    job = created.json()["data"]
    assert job["job_type"] == "GENERATE_SHOT_IMAGE"
    assert job["status"] == "PENDING"

    replay = await client.post(
        endpoint,
        json={},
        headers={"Idempotency-Key": "shot-image-test-replay"},
    )
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["data"]["id"] == job["id"]

    with Session(get_engine(get_settings().database_url)) as session:
        persisted_job = session.get(Job, job["id"])
        assert persisted_job is not None
        job_input = json.loads(persisted_job.input_json)
        assert job_input["character_ids"]
        assert job_input["reference_asset_ids"]
        assert isinstance(job_input["seed"], int)
        assert "角色身份锁定（硬约束）" in job_input["prompt"]

    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True
    completed = (await client.get(f"/api/v1/jobs/{job['id']}")).json()["data"]
    assert completed["status"] == "SUCCEEDED"

    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/workspace")).json()["data"]
    shot = next(item for item in workspace["shots"] if item["id"] == SHOT_IDS[0])
    assert shot["status"] == "PENDING_REVIEW"
    assert shot["candidate_take"] == 3
    assert shot["candidate_image_model"] == "doubao-seedream-4-5-251128"
    assert shot["character_bindings"][0]["name"] == "林悦"
    assert shot["candidate_identity_status"] == "REVIEW_REQUIRED"
    assert shot["candidate_image_url"].endswith("/content")
    image = await client.get(shot["candidate_image_url"])
    assert image.status_code == 200
    assert image.content == b"generated-image-bytes"
    assert image.headers["content-type"] == "image/png"

    blocked = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes/candidate/apply",
        headers={"Idempotency-Key": "apply-shot-image-v2"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "IDENTITY_REVIEW_REQUIRED"

    missing_override_reason = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes/candidate/review",
        json={
            "decision": "OVERRIDE_AND_APPLY",
            "issues": [],
            "expected_version": shot["lock_version"],
        },
        headers={"Idempotency-Key": "review-missing-reason"},
    )
    assert missing_override_reason.status_code == 422

    reviewed = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes/candidate/review",
        json={
            "decision": "APPROVE_AND_APPLY",
            "issues": [],
            "note": "与锁定参考图一致",
            "expected_version": shot["lock_version"],
            "actor": "test-reviewer",
        },
        headers={"Idempotency-Key": "review-and-apply-v3"},
    )
    assert reviewed.status_code == 200
    applied = reviewed.json()["data"]["shot"]
    assert applied["current_take"] == 3
    assert applied["candidate_take"] is None
    assert applied["current_image_url"].endswith("/content")
    assert applied["current_identity_review"]["decision"] == "APPROVE_AND_APPLY"
    assert applied["current_identity_review"]["actor"] == "test-reviewer"

    with Session(get_engine(get_settings().database_url)) as session:
        take = session.scalar(select(Take).where(Take.shot_id == SHOT_IDS[0]))
        assert take is not None and take.is_current is True
        assert take.identity_review_decision == "APPROVE_AND_APPLY"
        assert take.identity_reviewed_at is not None
        asset = session.get(Asset, take.asset_id)
        assert asset is not None and asset.provider == "volcengine-ark"


async def test_identity_review_regenerates_with_selected_issue_guidance(
    client: AsyncClient,
) -> None:
    created = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes",
        json={"resolution": "2K"},
        headers={"Idempotency-Key": "review-regenerate-source"},
    )
    assert created.status_code == 202
    worker = PersistentJobWorker(get_settings())
    assert await worker.run_once() is True

    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/workspace")).json()["data"]
    shot = next(item for item in workspace["shots"] if item["id"] == SHOT_IDS[0])
    rejected_version = shot["candidate_take"]
    assert shot["status"] == "PENDING_REVIEW"

    no_issues = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes/candidate/review",
        json={
            "decision": "REGENERATE",
            "issues": [],
            "expected_version": shot["lock_version"],
        },
        headers={"Idempotency-Key": "review-regenerate-no-issues"},
    )
    assert no_issues.status_code == 422

    regenerated = await client.post(
        f"/api/v1/shots/{SHOT_IDS[0]}/takes/candidate/review",
        json={
            "decision": "REGENERATE",
            "issues": ["HAIR", "WARDROBE"],
            "note": "刘海和外套需要更贴近参考图",
            "expected_version": shot["lock_version"],
            "actor": "test-reviewer",
        },
        headers={"Idempotency-Key": "review-regenerate-with-guidance"},
    )
    assert regenerated.status_code == 200
    result = regenerated.json()["data"]
    assert result["action"] == "REGENERATE"
    assert result["job"]["status"] == "PENDING"
    assert result["shot"]["status"] == "GENERATING"
    assert result["shot"]["candidate_take"] == rejected_version + 1
    assert result["shot"]["latest_identity_review"]["issues"] == ["HAIR", "WARDROBE"]

    with Session(get_engine(get_settings().database_url)) as session:
        rejected = session.scalar(
            select(Take).where(Take.shot_id == SHOT_IDS[0], Take.version == rejected_version)
        )
        assert rejected is not None
        assert rejected.approval == "REJECTED"
        assert json.loads(rejected.identity_review_issues_json) == ["HAIR", "WARDROBE"]
        job = session.get(Job, result["job"]["id"])
        assert job is not None
        prompt = json.loads(job.input_json)["prompt"]
        assert "发型、发际线和发色需要与参考角色一致" in prompt
        assert "服装造型需要符合当前 Look 版本" in prompt
