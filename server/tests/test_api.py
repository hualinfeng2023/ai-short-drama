import asyncio
from uuid import UUID

import pytest
from app.config import get_settings
from app.db.models import Asset, BriefVersion, IdempotencyKey
from app.db.session import get_engine
from app.main import app
from app.seed import EPISODE_ID, PROJECT_ID, SCENE_IDS, SHOT_IDS
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


async def test_health_and_capabilities(client: AsyncClient) -> None:
    live = await client.get("/health/live", headers={"X-Trace-ID": "test-trace"})
    assert live.status_code == 200
    assert live.json()["trace_id"] == "test-trace"
    assert live.headers["X-Trace-ID"] == "test-trace"

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["data"]["status"] == "ready"
    assert ready.json()["data"]["checks"]["job_worker"]["status"] == "disabled"

    config = (await client.get("/meta/config")).json()["data"]
    assert config["capabilities"] == {
        "read_only_api": False,
        "project_writes": True,
        "brief_versioning": True,
        "job_events_sse": True,
        "job_recovery": True,
        "job_worker": False,
        "media_pipeline": True,
        "mock_provider": True,
        "reference_uploads": True,
        "upload_ssrf_surface": False,
        "provider_calls": False,
        "image_provider": "mock",
        "image_model": "deterministic-image-v1",
        "image_models": [{"id": "deterministic-image-v1", "label": "确定性 Mock"}],
        "optional_image_provider": "volcengine-ark",
        "video_provider": "volcengine-ark",
        "video_model": "doubao-seedance-1-5-pro-251215",
        "seedream_source_url_fast_path_seconds": 600,
        "media_staging_provider": "volcengine-tos",
        "media_staging_configured": False,
        "feature_flags": {
            "creative_text_v2": False,
            "brief_targeting_v2": False,
            "workflow_dag_v1": False,
            "preproduction_v2": False,
            "storyboard_animatic_v2": False,
            "generation_qc_v2": False,
            "audio_pipeline_v1": False,
            "multitrack_timeline_v1": False,
            "export_profiles_v2": False,
            "provider_media_staging_v1": False,
        },
    }


async def test_runtime_config_lists_selectable_seedream_models(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    capabilities = (await client.get("/meta/config")).json()["data"]["capabilities"]
    assert capabilities["image_model"] == "doubao-seedream-5-0-260128"
    assert capabilities["image_models"] == [
        {"id": "doubao-seedream-5-0-260128", "label": "Seedream 5.0 Pro"},
        {"id": "doubao-seedream-5-0-lite-260128", "label": "Seedream 5.0 Lite"},
        {"id": "doubao-seedream-4-5-251128", "label": "Seedream 4.5"},
        {"id": "doubao-seedream-4-0-250828", "label": "Seedream 4.0"},
    ]

    with get_engine(get_settings().database_url).connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
    assert isinstance(get_engine(get_settings().database_url).pool, NullPool)


async def test_readiness_rejects_enabled_but_incomplete_tos_staging(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_MEDIA_STAGING_V1", "1")
    monkeypatch.setenv("TOS_ACCESS_KEY", "test-ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "test-sk")
    monkeypatch.delenv("TOS_BUCKET", raising=False)

    ready = await client.get("/health/ready")

    assert ready.status_code == 503
    staging = ready.json()["data"]["checks"]["media_staging"]
    assert staging == {
        "status": "error",
        "provider": "volcengine-tos",
        "bucket_configured": False,
        "signed_url_ttl_seconds": 7200,
        "reason": "incomplete_tos_configuration",
    }


async def test_workspace_and_entity_reads(client: AsyncClient) -> None:
    projects = await client.get("/api/v1/projects")
    assert projects.status_code == 200
    assert projects.json()["data"][0]["shot_count"] == 8

    workspace = await client.get(f"/api/v1/projects/{PROJECT_ID}/workspace")
    assert workspace.status_code == 200
    data = workspace.json()["data"]
    assert data["project"]["name"] == "她的第二人生"
    assert data["episode"]["id"] == EPISODE_ID
    assert len(data["scenes"]) == 3
    assert len(data["shots"]) == 8
    assert len(data["jobs"]) == 2
    sister_shot = next(item for item in data["shots"] if item["code"] == "S03")
    assert [item["name"] for item in sister_shot["character_bindings"]] == ["林悦", "林溪"]
    customer_shot = next(item for item in data["shots"] if item["code"] == "S06")
    assert [item["name"] for item in customer_shot["character_bindings"]] == [
        "林悦",
        "第一位客人",
    ]

    assert (await client.get(f"/api/v1/episodes/{EPISODE_ID}")).status_code == 200
    scenes = await client.get(f"/api/v1/episodes/{EPISODE_ID}/scenes")
    assert len(scenes.json()["data"]) == 3
    scene = await client.get(f"/api/v1/scenes/{SCENE_IDS[1]}")
    assert len(scene.json()["data"]["shots"]) == 3
    shot = await client.get(f"/api/v1/shots/{SHOT_IDS[0]}")
    assert shot.json()["data"]["code"] == "S01"


async def test_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/shots/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"]["id"] == "does-not-exist"
    assert body["trace_id"]


CREATE_PAYLOAD = {
    "name": "雨停以后",
    "idea": "暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。",
    "genre": "urban_suspense",
    "style": "realistic_cinematic",
    "target_duration_sec": 60,
    "aspect_ratio": "9:16",
    "target_platform": "douyin",
    "reference_asset_ids": [],
    "assumptions": ["故事发生在当代城市"],
}


async def test_project_create_is_idempotent(client: AsyncClient) -> None:
    missing_key = await client.post("/api/v1/projects", json=CREATE_PAYLOAD)
    assert missing_key.status_code == 422

    headers = {"Idempotency-Key": "create-rain-store-v1"}
    created = await client.post("/api/v1/projects", json=CREATE_PAYLOAD, headers=headers)
    assert created.status_code == 201
    assert created.headers["Idempotency-Replayed"] == "false"
    result = created.json()["data"]
    project_id = result["project"]["id"]
    assert UUID(project_id).version == 4
    assert result["project"]["status"] == "DRAFT"
    assert result["project"]["lock_version"] == 1
    assert result["brief_version"] == 1

    replay = await client.post("/api/v1/projects", json=CREATE_PAYLOAD, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["data"]["project"]["id"] == project_id

    changed_payload = {**CREATE_PAYLOAD, "idea": "同一个幂等键不能创建完全不同的新故事项目。"}
    conflict = await client.post("/api/v1/projects", json=changed_payload, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    projects = (await client.get("/api/v1/projects")).json()["data"]
    assert len(projects) == 2
    with Session(get_engine(get_settings().database_url)) as session:
        assert session.scalar(select(func.count(BriefVersion.id))) == 2
        assert session.scalar(select(func.count(IdempotencyKey.id))) == 1


async def test_project_delete_removes_full_owned_graph(client: AsyncClient) -> None:
    projects = (await client.get("/api/v1/projects")).json()["data"]
    project_id = projects[0]["id"]
    with Session(get_engine(get_settings().database_url)) as session:
        storage_keys = session.scalars(
            select(Asset.storage_key).where(Asset.project_id == project_id)
        ).all()
    asset_paths = [get_settings().data_dir / key for key in storage_keys]
    assert asset_paths and all(path.is_file() for path in asset_paths)

    deleted = await client.delete(f"/api/v1/projects/{project_id}")

    assert deleted.status_code == 200
    assert deleted.json()["data"]["project_id"] == project_id
    assert deleted.json()["data"]["deleted"] is True
    assert deleted.json()["data"]["deleted_rows"] > 1
    assert deleted.json()["data"]["deleted_files"] == len(asset_paths)
    assert all(not path.exists() for path in asset_paths)
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
    assert (await client.get("/api/v1/projects")).json()["data"] == []

    repeated = await client.delete(f"/api/v1/projects/{project_id}")
    assert repeated.status_code == 404


async def test_project_name_is_generated_from_brief_and_can_be_suggested_again(
    client: AsyncClient,
) -> None:
    payload = {
        **CREATE_PAYLOAD,
        "idea": "一对姐妹同时得到两颗神药，她们必须在亲情和欲望之间做出选择。",
    }
    payload.pop("name")
    created = await client.post(
        "/api/v1/projects",
        json=payload,
        headers={"Idempotency-Key": "create-smart-project-name-v1"},
    )

    assert created.status_code == 201
    project = created.json()["data"]["project"]
    assert project["name"] == "双生神药"

    suggestion = await client.post(
        f"/api/v1/projects/{project['id']}/name-suggestions",
        json={
            "current_name": project["name"],
            "idea": "一个总替别人做决定的母亲，在女儿婚礼前第一次选择沉默。",
            "genre": "family_drama",
            "style": "realistic_cinematic",
            "primary_audience": "urban_women_25_34",
            "primary_market": "CN",
            "canonical_language": "zh-CN",
        },
    )

    assert suggestion.status_code == 503
    error = suggestion.json()["error"]
    assert error["code"] == "PROJECT_NAMING_UNAVAILABLE"
    assert error["retryable"] is True
    assert "原名称不会被修改" in error["user_action"]


async def test_brief_requirements_can_be_drafted_without_mutating_project(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-brief-requirements-v1"},
    )
    project = created.json()["data"]["project"]

    suggestion = await client.post(
        f"/api/v1/projects/{project['id']}/brief-requirement-suggestions",
        json={
            "idea": project["idea"],
            "genre": project["genre"],
            "style": project["style"],
            "target_duration_sec": project["target_duration_sec"],
            "aspect_ratio": project["aspect_ratio"],
            "target_platform": project["target_platform"],
            "primary_audience": "urban_women_25_34",
            "primary_market": "CN",
            "canonical_language": "zh-CN",
            "existing_requirements": ["前三秒建立明确危机、异常事件或人物目标"],
            "content_avoidances": ["未授权品牌露出"],
        },
    )

    assert suggestion.status_code == 200
    result = suggestion.json()["data"]
    assert result["provider"] == "local-fallback"
    assert len(result["items"]) >= 3
    assert "前三秒建立明确危机、异常事件或人物目标" not in result["items"]
    unchanged = (await client.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert unchanged["lock_version"] == 1


async def test_brief_avoidances_can_be_suggested_without_mutating_project(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-brief-avoidances-v1"},
    )
    project = created.json()["data"]["project"]

    suggestion = await client.post(
        f"/api/v1/projects/{project['id']}/brief-avoidance-suggestions",
        json={
            "idea": project["idea"],
            "genre": project["genre"],
            "style": project["style"],
            "target_duration_sec": project["target_duration_sec"],
            "aspect_ratio": project["aspect_ratio"],
            "target_platform": project["target_platform"],
            "primary_audience": "urban_women_25_34",
            "primary_market": "CN",
            "canonical_language": "zh-CN",
            "content_requirements": ["前三秒建立明确危机"],
            "existing_avoidances": ["避免未经授权的品牌、音乐、肖像或素材露出"],
        },
    )

    assert suggestion.status_code == 200
    result = suggestion.json()["data"]
    assert result["provider"] == "local-fallback"
    assert len(result["items"]) >= 3
    assert "避免未经授权的品牌、音乐、肖像或素材露出" not in result["items"]
    unchanged = (await client.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert unchanged["lock_version"] == 1


async def test_brief_blocking_questions_can_be_drafted_without_mutating_project(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-brief-blocking-questions-v1"},
    )
    project = created.json()["data"]["project"]

    suggestion = await client.post(
        f"/api/v1/projects/{project['id']}/brief-blocking-question-suggestions",
        json={
            "idea": project["idea"],
            "genre": project["genre"],
            "style": project["style"],
            "target_duration_sec": project["target_duration_sec"],
            "aspect_ratio": project["aspect_ratio"],
            "target_platform": project["target_platform"],
            "narrative_protagonist": "unspecified",
            "emotional_rewards": [],
            "primary_market": "CN",
            "canonical_language": "zh-CN",
            "content_requirements": ["前三秒建立危机", "合伙人的秘密内容待确认"],
            "content_avoidances": ["避免未授权品牌露出"],
            "existing_questions": [],
        },
    )

    assert suggestion.status_code == 200
    result = suggestion.json()["data"]
    assert result["provider"] == "local-fallback"
    assert result["items"] == ["合伙人的秘密内容最终如何确定？"]
    unchanged = (await client.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert unchanged["lock_version"] == 1


async def test_story_rewrite_requires_seed_and_does_not_mutate_project(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-story-rewrite-v1"},
    )
    project = created.json()["data"]["project"]

    rewrite = await client.post(
        f"/api/v1/projects/{project['id']}/story-rewrites",
        json={
            "idea": project["idea"],
            "genre": project["genre"],
            "style": project["style"],
            "target_duration_sec": project["target_duration_sec"],
            "aspect_ratio": project["aspect_ratio"],
            "target_platform": project["target_platform"],
            "secondary_platforms": [],
            "primary_audience": "general",
            "secondary_audiences": [],
            "primary_market": "CN",
            "secondary_markets": [],
            "canonical_language": "zh-CN",
            "localization_targets": [],
            "content_requirements": [],
            "content_avoidances": [],
        },
    )

    assert rewrite.status_code == 503
    assert rewrite.json()["error"]["code"] == "SEED_TEXT_UNAVAILABLE"
    unchanged = (await client.get(f"/api/v1/projects/{project['id']}")).json()["data"]
    assert unchanged["lock_version"] == 1
    assert unchanged["idea"] == project["idea"]


async def test_project_patch_uses_optimistic_lock_and_versions_brief(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-editable-draft-v1"},
    )
    project_id = created.json()["data"]["project"]["id"]
    updated = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "expected_version": 1,
            "name": "便利店停电夜",
            "target_duration_sec": 90,
            "platform_targets": [
                {
                    "platform": "douyin",
                    "priority": "PRIMARY",
                    "aspect_ratio": "9:16",
                    "target_duration_sec": 90,
                    "caption_mode": "BOTH",
                }
            ],
        },
    )
    assert updated.status_code == 200
    result = updated.json()["data"]
    assert result["project"]["lock_version"] == 2
    assert result["project"]["name"] == "便利店停电夜"
    assert result["project"]["target_duration_sec"] == 90
    assert result["brief_version"] == 2

    stale = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"expected_version": 1, "name": "过期修改"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["latest_version"] == 2

    locked = await client.patch(
        f"/api/v1/projects/{PROJECT_ID}",
        json={"expected_version": 1, "name": "不应覆盖已制作项目"},
    )
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "PROJECT_LOCKED"

    with Session(get_engine(get_settings().database_url)) as session:
        brief_count = session.scalar(
            select(func.count(BriefVersion.id)).where(BriefVersion.project_id == project_id)
        )
        assert brief_count == 2


async def test_brief_v3_persists_independent_narrative_targeting(client: AsyncClient) -> None:
    payload = {
        **CREATE_PAYLOAD,
        "narrative_protagonist": "ensemble",
        "target_audience": "female_frequency",
        "emotional_rewards": ["identity", "public_mission"],
        "audience_profile": "25—40岁女性",
        "production_format": "live_action",
        "primary_audience": "urban_women_25_34",
        "secondary_audiences": ["suspense_fans", "mobile_first_viewers"],
        "primary_market": "CN",
        "secondary_markets": ["SG", "MY"],
        "canonical_language": "zh-CN",
        "localization_targets": ["en-SG", "ms-MY"],
        "platform_targets": [
            {
                "platform": "douyin",
                "priority": "PRIMARY",
                "aspect_ratio": "9:16",
                "target_duration_sec": 60,
                "caption_mode": "BURNED_IN",
            },
            {
                "platform": "youtube_shorts",
                "priority": "SECONDARY",
                "aspect_ratio": "9:16",
                "target_duration_sec": 60,
                "caption_mode": "BOTH",
            },
        ],
        "content_requirements": ["前三秒出现危机", "结尾保留反转钩子"],
        "content_avoidances": ["血腥特写", "未授权品牌露出"],
        "creative_defaults": {"pace": "fast", "max_scenes": 4},
    }
    created = await client.post(
        "/api/v1/projects",
        json=payload,
        headers={"Idempotency-Key": "brief-v2-multi-target-v1"},
    )
    assert created.status_code == 201
    project = created.json()["data"]["project"]
    assert project["target_platform"] == "douyin"

    response = await client.get(f"/api/v1/projects/{project['id']}/brief-versions")
    assert response.status_code == 200
    briefs = response.json()["data"]
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief["payload_schema_version"] == "brief-v3"
    assert brief["narrative_protagonist"] == "ensemble"
    assert brief["target_audience"] == "female_frequency"
    assert brief["emotional_rewards"] == ["identity", "public_mission"]
    assert brief["audience_profile"] == "25—40岁女性"
    assert brief["production_format"] == "live_action"
    assert brief["primary_audience"] == "urban_women_25_34"
    assert brief["secondary_audiences"] == ["suspense_fans", "mobile_first_viewers"]
    assert brief["secondary_markets"] == ["SG", "MY"]
    assert brief["localization_targets"] == ["en-SG", "ms-MY"]
    assert [item["platform"] for item in brief["platform_targets"]] == [
        "douyin",
        "youtube_shorts",
    ]
    assert brief["content_requirements"] == ["前三秒出现危机", "结尾保留反转钩子"]
    assert len(brief["content_hash"]) == 64


async def test_brief_v2_rejects_conflicting_targets(client: AsyncClient) -> None:
    duplicate_audience = await client.post(
        "/api/v1/projects",
        json={
            **CREATE_PAYLOAD,
            "primary_audience": "young_adults",
            "secondary_audiences": ["young_adults"],
        },
        headers={"Idempotency-Key": "brief-v2-conflict-audience-v1"},
    )
    assert duplicate_audience.status_code == 422

    multiple_primary_platforms = await client.post(
        "/api/v1/projects",
        json={
            **CREATE_PAYLOAD,
            "platform_targets": [
                {
                    "platform": "douyin",
                    "priority": "PRIMARY",
                    "aspect_ratio": "9:16",
                    "target_duration_sec": 60,
                },
                {
                    "platform": "youtube_shorts",
                    "priority": "PRIMARY",
                    "aspect_ratio": "9:16",
                    "target_duration_sec": 60,
                },
            ],
        },
        headers={"Idempotency-Key": "brief-v2-conflict-platform-v1"},
    )
    assert multiple_primary_platforms.status_code == 422


async def test_brief_v2_patch_inherits_and_updates_primary_platform(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/projects",
        json={
            **CREATE_PAYLOAD,
            "primary_audience": "urban_women_25_34",
            "secondary_markets": ["SG"],
            "platform_targets": [
                {
                    "platform": "douyin",
                    "priority": "PRIMARY",
                    "aspect_ratio": "9:16",
                    "target_duration_sec": 60,
                },
                {
                    "platform": "youtube_shorts",
                    "priority": "SECONDARY",
                    "aspect_ratio": "9:16",
                    "target_duration_sec": 60,
                },
            ],
        },
        headers={"Idempotency-Key": "brief-v2-patch-inherit-v1"},
    )
    project_id = created.json()["data"]["project"]["id"]
    updated = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={
            "expected_version": 1,
            "target_platform": "youtube_shorts",
            "target_duration_sec": 45,
            "content_requirements": ["45 秒内完成完整反转"],
        },
    )
    assert updated.status_code == 200
    project = updated.json()["data"]["project"]
    assert project["target_platform"] == "youtube_shorts"
    assert project["target_duration_sec"] == 45

    briefs = (await client.get(f"/api/v1/projects/{project_id}/brief-versions")).json()["data"]
    assert [item["version"] for item in briefs] == [2, 1]
    latest = briefs[0]
    assert latest["primary_audience"] == "urban_women_25_34"
    assert latest["secondary_markets"] == ["SG"]
    assert latest["content_requirements"] == ["45 秒内完成完整反转"]
    primary = [item for item in latest["platform_targets"] if item["priority"] == "PRIMARY"]
    assert primary == [
        {
            "platform": "youtube_shorts",
            "priority": "PRIMARY",
            "aspect_ratio": "9:16",
            "target_duration_sec": 45,
            "caption_mode": "BOTH",
        }
    ]


async def test_concurrent_project_patch_has_one_winner(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/projects",
        json=CREATE_PAYLOAD,
        headers={"Idempotency-Key": "create-concurrent-draft-v1"},
    )
    project_id = created.json()["data"]["project"]["id"]
    first, second = await asyncio.gather(
        client.patch(
            f"/api/v1/projects/{project_id}",
            json={"expected_version": 1, "name": "并发版本 A"},
        ),
        client.patch(
            f"/api/v1/projects/{project_id}",
            json={"expected_version": 1, "name": "并发版本 B"},
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    current = (await client.get(f"/api/v1/projects/{project_id}")).json()["data"]
    assert current["lock_version"] == 2
    with Session(get_engine(get_settings().database_url)) as session:
        brief_count = session.scalar(
            select(func.count(BriefVersion.id)).where(BriefVersion.project_id == project_id)
        )
    assert brief_count == 2


async def test_readiness_is_503_before_migration(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "unmigrated"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data_dir / 'empty.db'}")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        response = await test_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["data"]["status"] == "not_ready"
