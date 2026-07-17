import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from urllib.parse import quote
from uuid import uuid4

import httpx

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


@dataclass(frozen=True)
class SmokeResult:
    project_id: str
    timeline_id: str
    export_id: str
    mp4_sha256: str
    srt_sha256: str
    vtt_sha256: str


def _data(response: httpx.Response) -> object:
    response.raise_for_status()
    return response.json()["data"]


async def _wait_job(client: httpx.AsyncClient, job_id: str) -> dict[str, object]:
    for _ in range(240):
        job = _data(await client.get(f"/api/v1/jobs/{job_id}"))
        assert isinstance(job, dict)
        if job["status"] in TERMINAL:
            if job["status"] != "SUCCEEDED":
                raise RuntimeError(
                    f"Job {job_id} ended as {job['status']}: {job.get('error_message')}"
                )
            return job
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Job {job_id} did not finish within 60 seconds")


async def _wait_project_status(
    client: httpx.AsyncClient, project_id: str, expected: str
) -> dict[str, object]:
    for _ in range(240):
        project = _data(await client.get(f"/api/v1/projects/{project_id}"))
        assert isinstance(project, dict)
        if project["status"] == expected:
            return project
        await asyncio.sleep(0.25)
    raise TimeoutError(f"Project {project_id} did not reach {expected}")


def _probe_mp4(content: bytes) -> dict[str, object]:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as output:
        output.write(content)
        output.flush()
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate",
                "-of",
                "json",
                output.name,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    return json.loads(result.stdout)


async def run_once(client: httpx.AsyncClient, ordinal: int) -> SmokeResult:
    request_id = str(uuid4())
    created = _data(
        await client.post(
            "/api/v1/projects",
            headers={"Idempotency-Key": f"smoke-create-{request_id}"},
            json={
                "name": "雨停以后 · Smoke",
                "idea": "暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。",
                "genre": "urban_suspense",
                "style": "realistic_cinematic",
                "target_duration_sec": 60,
                "aspect_ratio": "9:16",
                "target_platform": "douyin",
                "reference_asset_ids": [],
                "assumptions": [],
            },
        )
    )
    assert isinstance(created, dict)
    project = created["project"]
    project_id = str(project["id"])

    reference = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/assets",
            content="人物小传：林悦在停电夜决定重新选择自己。".encode(),
            headers={
                "Content-Type": "text/plain",
                "X-Filename": quote("人物小传.txt", safe=""),
                "X-Rights-Confirmed": "true",
            },
        )
    )
    assert isinstance(reference, dict)
    linked = _data(
        await client.patch(
            f"/api/v1/projects/{project_id}",
            json={
                "expected_version": project["lock_version"],
                "reference_asset_ids": [reference["id"]],
            },
        )
    )
    assert isinstance(linked, dict)
    project = linked["project"]

    proposal_job = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/director-proposals",
            headers={"Idempotency-Key": f"smoke-proposal-{request_id}"},
            json={"expected_version": project["lock_version"]},
        )
    )
    assert isinstance(proposal_job, dict)
    await _wait_job(client, str(proposal_job["id"]))
    proposals = _data(await client.get(f"/api/v1/projects/{project_id}/director-proposals"))
    assert isinstance(proposals, list) and len(proposals) == 1
    payload = proposals[0]["payload"]
    assert len(payload["scenes"]) == 3
    assert sum(len(scene["shots"]) for scene in payload["scenes"]) == 8
    assert sum(scene["duration_sec"] for scene in payload["scenes"]) == 60

    project = _data(await client.get(f"/api/v1/projects/{project_id}"))
    approved = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/director-proposals/1/approve",
            headers={"Idempotency-Key": f"smoke-approve-story-{request_id}"},
            json={
                "expected_version": project["lock_version"],
                "assumptions_confirmed": True,
                "actor": "smoke",
            },
        )
    )
    await _wait_job(client, str(approved["job"]["id"]))
    characters = _data(await client.get(f"/api/v1/projects/{project_id}/characters/candidates"))
    assert isinstance(characters, list) and len(characters[0]["candidates"]) >= 2
    project = _data(await client.get(f"/api/v1/projects/{project_id}"))
    locked = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/characters/{characters[0]['id']}/lock",
            headers={"Idempotency-Key": f"smoke-lock-{request_id}"},
            json={
                "expected_version": project["lock_version"],
                "candidate_id": characters[0]["candidates"][0]["id"],
            },
        )
    )
    await _wait_job(client, str(locked["job"]["id"]))
    project = await _wait_project_status(client, project_id, "PREVIEW_READY")
    workspace = _data(await client.get(f"/api/v1/projects/{project_id}/workspace"))
    assert len(workspace["scenes"]) == 3 and len(workspace["shots"]) == 8
    assert sum(shot["duration_sec"] for shot in workspace["shots"]) == 60

    previews = _data(await client.get(f"/api/v1/projects/{project_id}/previews"))
    baseline = previews[0]
    mp4 = await client.get(baseline["assets"]["mp4"], headers={"Range": "bytes=0-"})
    assert mp4.status_code in {200, 206}
    probe = _probe_mp4(mp4.content)
    assert abs(float(probe["format"]["duration"]) - 60) < 0.5
    codecs = {(stream["codec_type"], stream["codec_name"]) for stream in probe["streams"]}
    assert {("video", "h264"), ("audio", "aac")} <= codecs

    shot_id = workspace["shots"][2]["id"]
    instruction = "妹妹只说半句，把威胁放在动作里"
    impact = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/revision-impact",
            json={
                "expected_version": project["lock_version"],
                "scope": {"type": "SHOT", "ids": [shot_id]},
                "instruction": instruction,
            },
        )
    )
    assert impact["requires_confirmation"] is True
    revision = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/revisions",
            headers={"Idempotency-Key": f"smoke-revision-{request_id}"},
            json={
                "expected_version": project["lock_version"],
                "scope": {"type": "SHOT", "ids": [shot_id]},
                "instruction": instruction,
                "confirmed": True,
            },
        )
    )
    await _wait_job(client, str(revision["job"]["id"]))
    change_set = _data(await client.get(f"/api/v1/revisions/{revision['revision']['id']}"))
    result_timeline_id = str(change_set["result_timeline_id"])
    comparison = _data(
        await client.get(f"/api/v1/previews/{baseline['id']}/compare/{result_timeline_id}")
    )
    assert {"srt", "vtt"} <= set(comparison["changed_assets"])
    assert comparison["changed_shot_ids"] == []

    project = _data(await client.get(f"/api/v1/projects/{project_id}"))
    _data(
        await client.post(
            f"/api/v1/previews/{result_timeline_id}/approve",
            headers={"Idempotency-Key": f"smoke-approve-preview-{request_id}"},
            json={"expected_version": project["lock_version"], "actor": "smoke"},
        )
    )
    project = _data(await client.get(f"/api/v1/projects/{project_id}"))
    blocked = await client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers={"Idempotency-Key": f"smoke-blocked-export-{request_id}"},
        json={
            "expected_version": project["lock_version"],
            "profile": "hybrid_720p",
            "rights_confirmed": False,
            "actor": "smoke",
        },
    )
    assert blocked.status_code == 423
    exported = _data(
        await client.post(
            f"/api/v1/projects/{project_id}/exports",
            headers={"Idempotency-Key": f"smoke-export-{request_id}"},
            json={
                "expected_version": project["lock_version"],
                "profile": "hybrid_720p",
                "rights_confirmed": True,
                "actor": "smoke",
            },
        )
    )
    await _wait_job(client, str(exported["job"]["id"]))
    export = _data(await client.get(f"/api/v1/exports/{exported['export']['id']}"))
    assert export["status"] == "READY" and len(export["assets"]) == 4
    outputs = {name: (await client.get(url)).content for name, url in export["assets"].items()}
    manifest = json.loads(outputs["manifest"])
    assert manifest["timeline"]["id"] == result_timeline_id
    assert manifest["rights"]["status"] == "RESTRICTED_DEMO"
    print(f"smoke {ordinal}: project={project_id} export={export['id']} ok")
    return SmokeResult(
        project_id=project_id,
        timeline_id=result_timeline_id,
        export_id=str(export["id"]),
        mp4_sha256=hashlib.sha256(outputs["mp4"]).hexdigest(),
        srt_sha256=hashlib.sha256(outputs["srt"]).hexdigest(),
        vtt_sha256=hashlib.sha256(outputs["vtt"]).hexdigest(),
    )


async def main_async(base_url: str, runs: int) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        ready = _data(await client.get("/health/ready"))
        assert ready["status"] == "ready"
        results = [await run_once(client, index + 1) for index in range(runs)]
    if runs > 1:
        media_hashes = {(item.mp4_sha256, item.srt_sha256, item.vtt_sha256) for item in results}
        if len(media_hashes) != 1:
            raise RuntimeError("相同 Mock 输入未产生相同媒体哈希")
    print(json.dumps({"status": "passed", "runs": runs}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Short Drama complete product-loop smoke")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main_async(args.base_url, args.runs))


if __name__ == "__main__":
    main()
