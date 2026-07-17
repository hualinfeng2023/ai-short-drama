import pytest

from app.seed import PROJECT_ID


@pytest.mark.anyio
async def test_seeded_classic_project_has_one_canonical_workflow(client) -> None:  # noqa: ANN001
    response = await client.get(f"/api/v1/projects/{PROJECT_ID}/readiness")

    assert response.status_code == 200
    readiness = response.json()["data"]
    assert readiness["workflow_mode"] == "CLASSIC"
    assert [stage["key"] for stage in readiness["stages"]] == [
        "BRIEF",
        "EPISODE",
        "SHOTS",
        "PREVIEW",
    ]
    assert readiness["active_stage_key"] == "SHOTS"
    assert readiness["active_job_count"] == 0
    assert readiness["summary_status"] == "ACTION_REQUIRED"
    assert "/scenes/" in readiness["next_action_href"]


@pytest.mark.anyio
async def test_new_draft_starts_in_pipeline_brief_without_requiring_an_episode(client) -> None:  # noqa: ANN001
    created = await client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "readiness-new-project"},
        json={
            "name": "状态聚合测试",
            "idea": "一位剪辑师发现每次删掉一帧，现实里就会少掉一段记忆。",
            "genre": "urban_suspense",
            "style": "realistic_cinematic",
            "target_duration_sec": 60,
            "aspect_ratio": "9:16",
            "target_platform": "douyin",
            "reference_asset_ids": [],
            "assumptions": [],
        },
    )
    assert created.status_code == 201
    project_id = created.json()["data"]["project"]["id"]

    response = await client.get(f"/api/v1/projects/{project_id}/readiness")

    assert response.status_code == 200
    readiness = response.json()["data"]
    assert readiness["workflow_mode"] == "PIPELINE"
    assert readiness["active_stage_key"] == "BRIEF"
    assert readiness["stages"][0]["status"] == "CURRENT"
    assert all(stage["status"] == "LOCKED" for stage in readiness["stages"][1:])
