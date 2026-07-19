import json
from datetime import UTC, datetime

import pytest
from app.config import get_settings
from app.db.models import (
    Asset,
    ChangeSet,
    Character,
    CharacterCandidateBatch,
    CharacterFamilyResemblanceConstraint,
    CharacterIdentityAsset,
    CharacterIdentityVersion,
    EpisodeOutlineVersion,
    Job,
    Project,
    RelationshipGraphVersion,
    ReviewRecord,
    ScriptScene,
    ScriptVersion,
    StoryBibleVersion,
    StoryVersion,
)
from app.db.session import get_engine
from app.jobs.worker import PersistentJobWorker
from app.seed import PROJECT_ID
from app.services.character_visuals import (
    CANDIDATE_VARIANTS,
    _extract_age,
    _profile_audit,
    is_biological_kinship,
    kinship_similarity_level,
    materialize_identity_asset,
    materialize_visual_candidate,
)
from app.services.image_provider import GeneratedImage
from app.services.projects import canonical_json, content_hash
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

STORY_ID = "91000000-0000-4000-8000-000000000001"
STORY_BIBLE_ID = "91000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize(
    ("explicit_age", "visual_notes", "expected"),
    [
        ("31岁", "26岁，留齐肩碎发", "31岁"),
        (None, "26岁，留齐肩碎发", "26岁"),
        (None, "六十岁左右，身材发福", "六十岁左右"),
        (None, "留齐肩碎发，穿通勤休闲装", "年龄待明确"),
    ],
)
def test_character_age_extraction_prefers_typed_data_then_visual_notes(
    explicit_age: str | None,
    visual_notes: str,
    expected: str,
) -> None:
    assert _extract_age(explicit_age, visual_notes) == expected


def test_character_profile_audit_blocks_conflicting_ages() -> None:
    issues = _profile_audit(
        {
            "identity_fields": {"age": "31岁", "occupation": "调查记者", "era": "当代"},
            "appearance_fields": {"identifying_features": "26岁，留齐肩碎发"},
            "styling_fields": {"wardrobe": "通勤休闲装", "forbidden_elements": []},
            "project_style": {"region_era": "当代中国城市"},
        }
    )

    assert any(issue["code"] == "AGE_CONFLICT" for issue in issues)


def relationship_state(
    *,
    surface: str,
    truth: str,
    trust: int,
    power: int,
    conflict: int,
) -> dict[str, object]:
    return {
        "surface_relationship": surface,
        "true_relationship": truth,
        "trust_level": trust,
        "emotional_temperature": trust,
        "power_balance": power,
        "conflict_intensity": conflict,
    }


def valid_graph() -> dict[str, object]:
    return {
        "schema_version": "relationship-graph-v1",
        "edges": [
            {
                "relationship_key": "protagonist-witness",
                "source_character_key": "protagonist",
                "target_character_key": "witness",
                "directionality": "BIDIRECTIONAL",
                "relationship_types": ["RIVAL", "SECRET"],
                "surface_relationship": "互相审视的嫌疑人与证人",
                "true_relationship": "共享旧案秘密的对立知情者",
                "source_view": {
                    "perceived_relationship": "可能隐瞒真相的证人",
                    "belief": "周启掌握照片原件但没有说出全部事实",
                },
                "target_view": {
                    "perceived_relationship": "试图逃避过去的嫌疑人",
                    "belief": "林岚仍在控制证据和现场叙事",
                },
                "trust_level": -2,
                "emotional_temperature": -1,
                "power_balance": 1,
                "conflict_intensity": 3,
                "story_function": "通过旧案秘密制造误判，并在认证后完成权力重排",
                "secret": "两人共同认识照片中被删除的人",
                "is_core": True,
                "locked": False,
                "ordinal": 1,
            }
        ],
        "beats": [
            {
                "relationship_key": "protagonist-witness",
                "episode_ordinal": 1,
                "sequence": 1,
                "scene_ordinal": 2,
                "trigger_type": "AUTHENTICATION",
                "trigger_ref": "authentication:2",
                "before_state": relationship_state(
                    surface="互相审视的嫌疑人与证人",
                    truth="共享旧案秘密的对立知情者",
                    trust=-2,
                    power=1,
                    conflict=3,
                ),
                "after_state": relationship_state(
                    surface="受约束的临时合作方",
                    truth="共同面对旧案的有条件盟友",
                    trust=0,
                    power=0,
                    conflict=1,
                ),
                "evidence": "周启交出照片原件，验证林岚的证据链",
                "emotional_consequence": "羞耻与防御转为有限信任",
                "audience_visibility": "REVEALED",
                "ordinal": 1,
            }
        ],
        "core_relationship_keys": ["protagonist-witness"],
        "generation_notes": [],
    }


def family_graph(
    relation_type: str,
    *,
    shared_upbringing: str = "SAME_HOUSEHOLD",
) -> dict[str, object]:
    graph = valid_graph()
    edge = graph["edges"][0]  # type: ignore[index]
    edge["relationship_types"] = ["FAMILY"]
    edge["family_kinship"] = {
        "relation_type": relation_type,
        "shared_upbringing": shared_upbringing,
        "upbringing_context": "长期共同生活，但旧案冲突使两人的情绪表达出现分化。",
    }
    return graph


def prepare_relationship_project() -> None:
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    now = datetime.now(UTC)
    bible_payload = {
        "characters": [
            {
                "key": "protagonist",
                "name": "林岚",
                "role": "PROTAGONIST",
                "visual_notes": "26岁，留齐肩碎发，背着洗得发白的帆布包",
            },
            {"key": "witness", "name": "周启", "role": "SUPPORTING"},
        ],
        "relationships": ["林岚与周启共享一段被删除的过去"],
    }
    with factory() as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.status = "RELATIONSHIP_READY"
        project.lock_version = 1
        project.updated_at = now
        story = StoryVersion(
            id=STORY_ID,
            project_id=PROJECT_ID,
            version=1,
            proposal_version=1,
            source_proposal_ids_json="[]",
            parent_version_id=None,
            schema_version="story-dna-v1",
            provider="test",
            model="test",
            config_version="test",
            title="旧案",
            logline="两名知情者在互相误判后被迫合作。",
            payload_json="{}",
            content_hash=content_hash({}),
            status="APPROVED",
            approved_at=now,
            approved_by="test",
            created_at=now,
        )
        session.add(story)
        session.flush()
        session.add(
            StoryBibleVersion(
                id=STORY_BIBLE_ID,
                project_id=PROJECT_ID,
                story_version_id=STORY_ID,
                version=1,
                status="READY_FOR_REVIEW",
                payload_json=canonical_json(bible_payload),
                critic_json="{}",
                content_hash=content_hash(bible_payload),
                parent_version_id=None,
                schema_version="story-bible-v1",
                provider="test",
                model="test",
                config_version="test",
                approved_at=None,
                approved_by=None,
                created_at=now,
            )
        )
        session.commit()


async def create_graph(client: AsyncClient, graph: dict[str, object] | None = None) -> dict:
    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-graphs",
        json={
            "expected_project_version": 1,
            "story_bible_version_id": STORY_BIBLE_ID,
            "graph": graph or valid_graph(),
            "actor": "test-author",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def lock_character_for_test(
    client: AsyncClient,
    character_data: dict,
    *,
    appearance_fields: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    character_id = character_data["id"]
    if appearance_fields:
        updated = await client.patch(
            f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-profile",
            json={
                "expected_version": character_data["lock_version"],
                "appearance_fields": appearance_fields,
                "actor": "family-test",
            },
        )
        assert updated.status_code == 200, updated.text
        workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
            "data"
        ]
        character_data = next(
            item for item in workspace["characters"] if item["id"] == character_id
        )

    confirmed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-profile/confirm",
        json={
            "expected_version": character_data["lock_version"],
            "profile_version_id": character_data["profile"]["id"],
            "actor": "family-test",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    character_data = next(item for item in workspace["characters"] if item["id"] == character_id)
    generated = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-candidates",
        json={
            "expected_version": character_data["lock_version"],
            "profile_version_id": character_data["profile"]["id"],
            "count": 3,
            "actor": "family-test",
        },
    )
    assert generated.status_code == 202, generated.text
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
                    Job.entity_id == character_id,
                )
                .order_by(Job.created_at)
            ).all()
        )
        assert len(jobs) == 3
        for index, job in enumerate(jobs):
            assert 0 <= json.loads(job.input_json)["seed"] < 2**31
            materialize_visual_candidate(
                session,
                get_settings(),
                job,
                GeneratedImage(
                    content=f"family-candidate-{character_id}-{index}".encode(),
                    mime="image/png",
                    width=480,
                    height=640,
                    model="mock-image-v1",
                    request_id=None,
                ),
            )
        session.commit()
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    character_data = next(item for item in workspace["characters"] if item["id"] == character_id)
    selected = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-candidates/select",
        json={
            "expected_version": character_data["lock_version"],
            "candidate_id": character_data["candidates"][0]["id"],
            "actor": "family-test",
        },
    )
    assert selected.status_code == 202, selected.text
    identity_id = selected.json()["data"]["identity"]["id"]
    with factory() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.job_type == "GENERATE_CHARACTER_IDENTITY_DOSSIER",
                    Job.entity_id == identity_id,
                )
                .order_by(Job.created_at)
            ).all()
        )
        assert len(jobs) == 5
        for index, job in enumerate(jobs):
            assert 0 <= json.loads(job.input_json)["seed"] < 2**31
            materialize_identity_asset(
                session,
                get_settings(),
                job,
                GeneratedImage(
                    content=f"family-identity-{character_id}-{index}".encode(),
                    mime="image/png",
                    width=480,
                    height=640,
                    model="mock-image-v1",
                    request_id=None,
                ),
            )
        session.commit()
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    character_data = next(item for item in workspace["characters"] if item["id"] == character_id)
    locked = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/identity/lock",
        json={
            "expected_version": character_data["lock_version"],
            "identity_version_id": identity_id,
            "actor": "family-test",
        },
    )
    assert locked.status_code == 200, locked.text
    final_workspace = (
        await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")
    ).json()["data"]
    final_character = next(
        item for item in final_workspace["characters"] if item["id"] == character_id
    )
    return locked.json()["data"], final_character


@pytest.mark.anyio
async def test_relationship_graph_edit_review_lock_and_revision_flow(client: AsyncClient) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    graph_id = graph["id"]

    assert graph["project_lock_version"] == 2
    assert graph["lock_version"] == 1
    assert graph["editability"]["semantic_editable"] is True

    locked = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/relationships/protagonist-witness/lock",
        json={"expected_project_version": 2, "expected_graph_version": 1},
    )
    assert locked.status_code == 200
    locked_graph = locked.json()["data"]
    assert locked_graph["graph"]["edges"][0]["locked"] is True

    changed = locked_graph["graph"]
    changed["edges"][0]["story_function"] = "试图绕过关系锁修改剧情功能"
    rejected_save = await client.patch(
        f"/api/v1/relationship-graphs/{graph_id}",
        json={
            "expected_project_version": 3,
            "expected_graph_version": 2,
            **changed,
        },
    )
    assert rejected_save.status_code == 409
    assert rejected_save.json()["error"]["code"] == "RELATIONSHIP_LOCKED"

    unlocked = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/relationships/protagonist-witness/unlock",
        json={"expected_project_version": 3, "expected_graph_version": 2},
    )
    assert unlocked.status_code == 200
    unlocked_graph = unlocked.json()["data"]
    assert unlocked_graph["graph"]["edges"][0]["locked"] is False

    updated_payload = unlocked_graph["graph"]
    updated_payload["edges"][0]["story_function"] = "认证前制造误判，认证后建立有限同盟"
    updated = await client.patch(
        f"/api/v1/relationship-graphs/{graph_id}",
        json={
            "expected_project_version": 4,
            "expected_graph_version": 3,
            **updated_payload,
        },
    )
    assert updated.status_code == 200

    submitted = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/submit",
        json={"expected_project_version": 5, "expected_graph_version": 4},
    )
    assert submitted.status_code == 200
    submitted_graph = submitted.json()["data"]
    assert submitted_graph["status"] == "READY_FOR_REVIEW"
    assert submitted_graph["editability"]["semantic_editable"] is False
    assert submitted_graph["editability"]["reason_code"] == "GRAPH_SUBMITTED"

    read_only_save = await client.patch(
        f"/api/v1/relationship-graphs/{graph_id}",
        json={
            "expected_project_version": 6,
            "expected_graph_version": 5,
            **updated_payload,
        },
    )
    assert read_only_save.status_code == 409
    assert read_only_save.json()["error"]["code"] == "GRAPH_SUBMITTED"

    withdrawn = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/withdraw",
        json={"expected_project_version": 6, "expected_graph_version": 5},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["data"]["status"] == "DRAFT"

    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/approve",
        json={"expected_project_version": 7, "expected_graph_version": 6, "actor": "reviewer"},
    )
    assert approved.status_code == 200
    approved_graph = approved.json()["data"]
    assert approved_graph["status"] == "APPROVED"
    assert approved_graph["editability"]["reason_code"] == "GRAPH_APPROVED"
    assert approved_graph["character_visuals"]["character_count"] == 2

    revision = await client.post(
        f"/api/v1/relationship-graphs/{graph_id}/revisions",
        json={"expected_project_version": 8, "actor": "test-author"},
    )
    assert revision.status_code == 409
    assert revision.json()["error"]["code"] == "DOWNSTREAM_IMPACT_CONFIRMATION_REQUIRED"


@pytest.mark.anyio
async def test_character_visual_flow_requires_manual_generation_and_lock(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["character_visuals"]["character_count"] == 2

    workspace_response = await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()["data"]
    assert workspace["project_status"] == "CHARACTER_VISUAL_READY"
    assert all(not item["candidates"] for item in workspace["characters"])
    protagonist = next(item for item in workspace["characters"] if item["name"] == "林岚")
    assert protagonist["profile"]["identity_fields"]["age"] == "26岁"
    assert protagonist["profile"]["summary"].startswith(
        "26岁 · 待明确职业 · 留齐肩碎发"
    )
    assert "30岁左右" not in protagonist["profile"]["summary"]

    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    script_job = None
    for character_data in workspace["characters"]:
        character_id = character_data["id"]
        profile_id = character_data["profile"]["id"]
        confirmed = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-profile/confirm",
            json={
                "expected_version": character_data["lock_version"],
                "profile_version_id": profile_id,
                "actor": "tester",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        with factory() as session:
            character = session.get(Character, character_id)
            assert character is not None
            expected_version = character.lock_version
        generated = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-candidates",
            json={
                "expected_version": expected_version,
                "profile_version_id": profile_id,
                "count": 3,
                "actor": "tester",
            },
        )
        assert generated.status_code == 202, generated.text
        batch_id = generated.json()["data"]["batch"]["id"]
        with factory() as session:
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(
                        Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
                        Job.entity_id == character_id,
                    )
                    .order_by(Job.created_at)
                ).all()
            )
            assert len(jobs) == 3
            for index, job in enumerate(jobs):
                materialize_visual_candidate(
                    session,
                    get_settings(),
                    job,
                    GeneratedImage(
                        content=f"candidate-{character_id}-{index}".encode(),
                        mime="image/png",
                        width=480,
                        height=640,
                        model="mock-image-v1",
                        request_id=None,
                    ),
                )
            session.commit()
            batch = session.get(CharacterCandidateBatch, batch_id)
            character = session.get(Character, character_id)
            assert batch is not None and batch.status == "READY"
            assert character is not None and character.status == "PENDING_SELECTION"
            expected_version = character.lock_version

        refreshed = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
            "data"
        ]
        current = next(item for item in refreshed["characters"] if item["id"] == character_id)
        assert len(current["candidates"]) == 3
        candidate_id = current["candidates"][0]["id"]
        selected = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/visual-candidates/select",
            json={
                "expected_version": expected_version,
                "candidate_id": candidate_id,
                "actor": "tester",
            },
        )
        assert selected.status_code == 202, selected.text
        identity_id = selected.json()["data"]["identity"]["id"]
        with factory() as session:
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(
                        Job.job_type == "GENERATE_CHARACTER_IDENTITY_DOSSIER",
                        Job.entity_id == identity_id,
                    )
                    .order_by(Job.created_at)
                ).all()
            )
            assert len(jobs) == 5
            failed_job_id = jobs[0].id
            jobs[0].status = "FAILED"
            jobs[0].completed_at = datetime.now(UTC)
            jobs[0].error_code = "TEST_IMAGE_FAILURE"
            character = session.get(Character, character_id)
            assert character is not None
            character.status = "GENERATION_FAILED"
            session.commit()

        failed_workspace = (
            await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")
        ).json()["data"]
        failed_character = next(
            item for item in failed_workspace["characters"] if item["id"] == character_id
        )
        failed_identity = next(
            item for item in failed_character["identities"] if item["id"] == identity_id
        )
        assert len(failed_identity["view_jobs"]) == 5
        failed_view_job = next(
            item for item in failed_identity["view_jobs"] if item["id"] == failed_job_id
        )
        assert failed_view_job["status"] == "FAILED"
        assert failed_view_job["retryable"] is True
        assert failed_view_job["max_wait_seconds"] == 120

        retried = await client.post(
            f"/api/v1/jobs/{failed_job_id}/retry",
            headers={"Idempotency-Key": f"retry-dossier-{character_id}"},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["data"]["status"] == "RETRY_WAIT"

        with factory() as session:
            character = session.get(Character, character_id)
            identity = session.get(CharacterIdentityVersion, identity_id)
            assert character is not None and character.status == "PENDING_REVIEW"
            assert identity is not None and identity.status == "GENERATING_DOSSIER"
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(
                        Job.job_type == "GENERATE_CHARACTER_IDENTITY_DOSSIER",
                        Job.entity_id == identity_id,
                    )
                    .order_by(Job.created_at)
                ).all()
            )
            for index, job in enumerate(jobs):
                materialize_identity_asset(
                    session,
                    get_settings(),
                    job,
                    GeneratedImage(
                        content=f"identity-{character_id}-{index}".encode(),
                        mime="image/png",
                        width=480,
                        height=640,
                        model="mock-image-v1",
                        request_id=None,
                    ),
                )
                job.status = "SUCCEEDED"
                job.completed_at = datetime.now(UTC)
                job.updated_at = datetime.now(UTC)
            session.commit()
            character = session.get(Character, character_id)
            assert character is not None and character.status == "REVIEW_REQUIRED"
            expected_version = character.lock_version

        if character_data["name"] == "林岚":
            current_workspace = (
                await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")
            ).json()["data"]
            current_character = next(
                item for item in current_workspace["characters"] if item["id"] == character_id
            )
            current_identity = next(
                item for item in current_character["identities"] if item["id"] == identity_id
            )
            current_view = next(
                item for item in current_identity["assets"]
                if item["view_type"] == "THREE_QUARTER"
            )
            adjustment = await client.post(
                (
                    f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}"
                    f"/identity/{identity_id}/views"
                ),
                json={
                    "expected_version": expected_version,
                    "view_type": "THREE_QUARTER",
                    "refinement_note": "保留五官，让视线更坚定",
                    "actor": "tester",
                },
            )
            assert adjustment.status_code == 202, adjustment.text
            adjustment_job_id = adjustment.json()["data"]["job"]["id"]

            with factory() as session:
                adjustment_job = session.get(Job, adjustment_job_id)
                assert adjustment_job is not None
                adjustment_payload = json.loads(adjustment_job.input_json)
                assert adjustment_payload["generation_mode"] == "REFINE"
                assert adjustment_payload["reference_asset_id"] == current_view["asset_id"]
                assert "保留五官，让视线更坚定" in adjustment_payload["prompt"]
                replacement = materialize_identity_asset(
                    session,
                    get_settings(),
                    adjustment_job,
                    GeneratedImage(
                        content=b"identity-adjusted-three-quarter",
                        mime="image/png",
                        width=480,
                        height=640,
                        model="mock-image-v1",
                        request_id=None,
                    ),
                )
                adjustment_job.status = "SUCCEEDED"
                adjustment_job.completed_at = datetime.now(UTC)
                adjustment_job.updated_at = datetime.now(UTC)
                session.commit()
                replacement_record = session.get(
                    CharacterIdentityAsset,
                    current_view["id"],
                )
                assert replacement_record is not None
                assert replacement_record.id == current_view["id"]
                assert replacement_record.asset_id == replacement[0].id
                assert replacement_record.asset_id != current_view["asset_id"]
                assert session.get(Asset, current_view["asset_id"]) is not None
                character = session.get(Character, character_id)
                assert character is not None and character.status == "REVIEW_REQUIRED"
                expected_version = character.lock_version

        locked = await client.post(
            f"/api/v1/projects/{PROJECT_ID}/characters/{character_id}/identity/lock",
            json={
                "expected_version": expected_version,
                "identity_version_id": identity_id,
                "actor": "tester",
            },
        )
        assert locked.status_code == 200, locked.text
        assert locked.json()["data"]["identity"]["status"] == "LOCKED"
        script_job = locked.json()["data"]["script_job"] or script_job

    assert script_job is not None
    assert script_job["job_type"] == "GENERATE_SCRIPT_PACKAGE"
    final_workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    assert final_workspace["project_status"] == "SCRIPT_PACKAGE_RUNNING"
    assert all(item["status"] == "LOCKED" for item in final_workspace["characters"])
    assert all(item["locked_identity_version_id"] for item in final_workspace["characters"])
    assert all(item["active_look_version_id"] for item in final_workspace["characters"])
    assert all(item["active_story_state_version_id"] for item in final_workspace["characters"])

    first = final_workspace["characters"][0]
    text_only = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{first['id']}/changes",
        json={
            "expected_version": first["lock_version"],
            "change_type": "TEXT_ONLY",
            "payload": {"dialogue": "只修改台词"},
            "actor": "tester",
        },
    )
    assert text_only.status_code == 200
    assert text_only.json()["data"]["action"] == "NO_IMAGE_GENERATION"

    decision_required = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{first['id']}/changes",
        json={
            "expected_version": first["lock_version"],
            "change_type": "IDENTITY_MAJOR",
            "payload": {"age": "60岁"},
            "actor": "tester",
        },
    )
    assert decision_required.status_code == 409
    assert decision_required.json()["error"]["code"] == "IDENTITY_DECISION_REQUIRED"

    state_changed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{first['id']}/changes",
        json={
            "expected_version": first["lock_version"],
            "change_type": "STORY_STATE",
            "payload": {"label": "雨夜受伤", "injury": "额角轻伤", "wetness": "湿发"},
            "actor": "tester",
        },
    )
    assert state_changed.status_code == 200
    assert state_changed.json()["data"]["action"] == "STORY_STATE_VERSION_CREATED"
    refreshed = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    first = next(item for item in refreshed["characters"] if item["id"] == first["id"])
    look_changed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{first['id']}/changes",
        json={
            "expected_version": first["lock_version"],
            "change_type": "LOOK",
            "payload": {"label": "雨夜造型", "wardrobe": "深色防水外套"},
            "actor": "tester",
        },
    )
    assert look_changed.status_code == 200
    assert look_changed.json()["data"]["action"] == "LOOK_VERSION_CREATED"


@pytest.mark.anyio
async def test_character_generation_auto_confirms_summary_and_creates_distinct_refinements(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    character = workspace["characters"][0]
    assert character["profile"]["status"] == "READY_FOR_REVIEW"
    assert character["profile"]["summary"]

    generated = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character['id']}/visual-candidates",
        json={
            "expected_version": character["lock_version"],
            "profile_version_id": character["profile"]["id"],
            "count": 3,
            "actor": "tester",
        },
    )
    assert generated.status_code == 202, generated.text
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
                    Job.entity_id == character["id"],
                )
                .order_by(Job.created_at)
            ).all()
        )
        payloads = [json.loads(job.input_json) for job in jobs]
        batch = session.get(CharacterCandidateBatch, payloads[0]["batch_id"])
        assert batch is not None
        batch_prompt = json.loads(batch.prompt_json)
    variant_keys = {item["variant_key"] for item in payloads}
    variant_labels = {
        variant["key"]: variant["label"]
        for variant in CANDIDATE_VARIANTS
    }
    assert len(variant_keys) == 3
    assert variant_keys <= set(variant_labels)
    assert {
        item["key"]
        for item in batch_prompt["candidate_variants"]
    } == variant_keys
    assert len({item["prompt"] for item in payloads}) == 3

    worker = PersistentJobWorker(get_settings())
    for _ in range(3):
        assert await worker.run_once() is True
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    character = next(item for item in workspace["characters"] if item["id"] == character["id"])
    assert character["profile"]["status"] == "CONFIRMED"
    assert {item["variant_label"] for item in character["candidates"]} == {
        variant_labels[key] for key in variant_keys
    }

    source = character["candidates"][0]
    refined = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character['id']}/visual-candidates",
        json={
            "expected_version": character["lock_version"],
            "profile_version_id": character["profile"]["id"],
            "count": 3,
            "source_candidate_id": source["id"],
            "refinement_note": "保留五官，只让眼神更警觉，发型更利落",
            "actor": "tester",
        },
    )
    assert refined.status_code == 202, refined.text
    with factory() as session:
        refined_jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
                    Job.entity_id == character["id"],
                )
                .order_by(Job.created_at.desc())
                .limit(3)
            ).all()
        )
        refined_payloads = [json.loads(job.input_json) for job in refined_jobs]
    refined_variant_keys = {item["variant_key"] for item in refined_payloads}
    assert len(refined_variant_keys) == 3
    assert refined_variant_keys.isdisjoint(variant_keys)
    assert all(item["source_candidate_id"] == source["id"] for item in refined_payloads)
    assert all(source["asset_id"] in item["reference_asset_ids"] for item in refined_payloads)
    assert all("眼神更警觉" in item["prompt"] for item in refined_payloads)


@pytest.mark.anyio
async def test_character_identity_can_restore_a_previous_locked_baseline(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    _, character = await lock_character_for_test(client, workspace["characters"][0])
    first_identity_id = character["locked_identity_version_id"]

    second_identity_id = "88000000-0000-4000-8000-000000000002"
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        first_identity = session.get(CharacterIdentityVersion, first_identity_id)
        stored_character = session.get(Character, character["id"])
        assert first_identity is not None and stored_character is not None
        session.add(
            CharacterIdentityVersion(
                id=second_identity_id,
                project_id=PROJECT_ID,
                character_id=stored_character.id,
                version=first_identity.version + 1,
                source_candidate_id=first_identity.source_candidate_id,
                profile_version_id=first_identity.profile_version_id,
                stable_traits_json=first_identity.stable_traits_json,
                prompt_snapshot_json=first_identity.prompt_snapshot_json,
                content_hash=content_hash({"identity": second_identity_id}),
                status="READY_FOR_REVIEW",
                locked_at=None,
                locked_by=None,
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    second_lock = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character['id']}/identity/lock",
        json={
            "expected_version": character["lock_version"],
            "identity_version_id": second_identity_id,
            "actor": "tester",
        },
    )
    assert second_lock.status_code == 200, second_lock.text
    current = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()["data"]
    character = next(item for item in current["characters"] if item["id"] == character["id"])
    assert character["locked_identity_version_id"] == second_identity_id

    restored = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{character['id']}/identity/restore",
        json={
            "expected_version": character["lock_version"],
            "identity_version_id": first_identity_id,
            "actor": "tester",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["existing_shots_preserved"] is True
    current = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()["data"]
    character = next(item for item in current["characters"] if item["id"] == character["id"])
    assert character["locked_identity_version_id"] == first_identity_id
    statuses = {item["id"]: item["status"] for item in character["identities"]}
    assert statuses[first_identity_id] == "LOCKED"
    assert statuses[second_identity_id] == "SUPERSEDED"
    assert len(character["looks"]) == 3


@pytest.mark.parametrize(
    ("relation_type", "biological", "level"),
    [
        ("BIOLOGICAL_PARENT_CHILD", True, "MEDIUM"),
        ("FULL_SIBLINGS", True, "MEDIUM"),
        ("PATERNAL_HALF_SIBLINGS", True, "LOW"),
        ("MATERNAL_HALF_SIBLINGS", True, "LOW"),
        ("FRATERNAL_TWINS", True, "HIGH"),
        ("IDENTICAL_TWINS", True, "VERY_HIGH"),
        ("ADOPTIVE_PARENT_CHILD", False, None),
        ("STEP_PARENT_CHILD", False, None),
        ("IN_LAW", False, None),
    ],
)
def test_family_kinship_similarity_policy(
    relation_type: str,
    biological: bool,
    level: str | None,
) -> None:
    assert is_biological_kinship(relation_type) is biological
    assert kinship_similarity_level(relation_type) == level


@pytest.mark.anyio
@pytest.mark.parametrize(
    "relation_type",
    ["ADOPTIVE_PARENT_CHILD", "STEP_PARENT_CHILD", "IN_LAW"],
)
async def test_non_biological_family_relationship_does_not_create_resemblance_constraint(
    client: AsyncClient,
    relation_type: str,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client, family_graph(relation_type))
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    assert all(item["family_resemblance_constraint"] is None for item in workspace["characters"])


@pytest.mark.anyio
async def test_locked_biological_relative_creates_versioned_family_constraint_and_prompt(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client, family_graph("BIOLOGICAL_PARENT_CHILD"))
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    workspace = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    assert all(
        item["family_resemblance_constraint"]["status"] == "WAITING_FOR_LOCKED_RELATIVE"
        for item in workspace["characters"]
    )
    source = workspace["characters"][0]
    target = workspace["characters"][1]
    await lock_character_for_test(
        client,
        source,
        appearance_fields={
            "brow_eye_shape": "平直眉配窄长眼裂，眼尾轻微下垂",
            "nose_shape": "鼻梁偏直，鼻翼收窄",
            "mouth_corner": "静止时左侧嘴角略高",
            "face_shape": "偏长的鹅蛋形脸部轮廓",
            "skin_tone": "自然偏暖肤色",
            "hair_texture": "偏硬的自然直发",
        },
    )
    refreshed = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    source = next(item for item in refreshed["characters"] if item["id"] == source["id"])
    target = next(item for item in refreshed["characters"] if item["id"] == target["id"])
    constraint = target["family_resemblance_constraint"]
    assert source["status"] == "LOCKED"
    assert constraint["status"] == "ACTIVE"
    assert constraint["similarity_level"] == "MEDIUM"
    assert 1 <= len(constraint["inherited_features"]) <= 3
    assert len(constraint["inherited_features"]) == 2
    assert {item["label"] for item in constraint["inherited_features"]} == {"眉眼", "鼻型"}
    assert constraint["source_character_ids"] == [source["id"]]
    assert "后天环境" in constraint["temperament_affinity"]["instruction"]
    assert any("不复制参考角色整张脸" in item for item in constraint["independence_constraints"])

    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        stored = session.get(CharacterFamilyResemblanceConstraint, constraint["id"])
        assert stored is not None and stored.version == constraint["version"]
        child_jobs = session.scalars(
            select(Job).where(
                Job.entity_id == target["id"],
                Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
            )
        ).all()
        assert child_jobs == []

    confirmed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{target['id']}/visual-profile/confirm",
        json={
            "expected_version": target["lock_version"],
            "profile_version_id": target["profile"]["id"],
            "actor": "family-test",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    refreshed = (await client.get(f"/api/v1/projects/{PROJECT_ID}/character-visuals")).json()[
        "data"
    ]
    target = next(item for item in refreshed["characters"] if item["id"] == target["id"])
    generated = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/characters/{target['id']}/visual-candidates",
        json={
            "expected_version": target["lock_version"],
            "profile_version_id": target["profile"]["id"],
            "count": 3,
            "actor": "family-test",
        },
    )
    assert generated.status_code == 202, generated.text
    assert generated.json()["data"]["batch"]["family_constraint_version_id"] == constraint["id"]
    with factory() as session:
        child_jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.entity_id == target["id"],
                    Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
                )
                .order_by(Job.created_at)
            ).all()
        )
        assert len(child_jobs) == 3
        payload = json.loads(child_jobs[0].input_json)
        assert payload["family_constraint_version_id"] == constraint["id"]
        assert len(payload["reference_asset_ids"]) == 1
        assert "Family Resemblance Constraint" in payload["prompt"]
        assert "不得仅通过改变年龄或性别制造亲属" in payload["prompt"]
        assert "保持目标角色独立" in payload["prompt"]


@pytest.mark.anyio
async def test_relationship_revision_impact_diff_and_script_staleness(client: AsyncClient) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    approved = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1, "actor": "reviewer"},
    )
    assert approved.status_code == 200, approved.text
    approved_graph = approved.json()["data"]
    now = datetime.now(UTC)
    outline_id = "92000000-0000-4000-8000-000000000001"
    script_id = "92000000-0000-4000-8000-000000000002"
    scene_id = "92000000-0000-4000-8000-000000000003"
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.status = "SCRIPT_READY"
        payload = {"title": "第一集"}
        session.add(
            EpisodeOutlineVersion(
                id=outline_id,
                project_id=PROJECT_ID,
                story_bible_version_id=STORY_BIBLE_ID,
                relationship_graph_version_id=graph["id"],
                episode_ordinal=1,
                version=1,
                status="READY_FOR_REVIEW",
                payload_json=canonical_json(payload),
                critic_json="{}",
                content_hash=content_hash(payload),
                parent_version_id=None,
                schema_version="episode-outline-v1",
                provider="test",
                model="test",
                config_version="test",
                approved_at=None,
                approved_by=None,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ScriptVersion(
                id=script_id,
                project_id=PROJECT_ID,
                outline_version_id=outline_id,
                relationship_graph_version_id=graph["id"],
                episode_ordinal=1,
                version=1,
                status="READY_FOR_REVIEW",
                payload_json=canonical_json(payload),
                critic_json="{}",
                content_hash=content_hash(payload),
                parent_version_id=None,
                schema_version="script-v4-relationship-driven",
                canonical_language="zh-CN",
                provider="test",
                model="test",
                config_version="test",
                estimated_duration_ms=60_000,
                approved_at=None,
                approved_by=None,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            ScriptScene(
                id=scene_id,
                script_version_id=script_id,
                ordinal=2,
                heading="旧照冲突",
                location="暗房",
                time_of_day="夜",
                purpose="验证关系",
                emotion="紧张",
                duration_ms=20_000,
                bgm_intent="克制",
                sfx_intent_json="[]",
            )
        )
        session.commit()

    immutable = await client.patch(
        f"/api/v1/relationship-graphs/{graph['id']}",
        json={
            "expected_project_version": 3,
            "expected_graph_version": 2,
            **approved_graph["graph"],
        },
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "GRAPH_APPROVED"

    impact_payload = {
        "base_relationship_graph_id": graph["id"],
        "relationship_keys": ["protagonist-witness"],
        "intent": "将两人的真实关系调整为相互利用",
        "expected_version": 3,
    }
    impact_response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-revision-impact",
        json=impact_payload,
    )
    assert impact_response.status_code == 200, impact_response.text
    impact = impact_response.json()["data"]
    assert impact["affected"]["episode_ordinals"] == [1]
    assert impact["affected"]["scenes"][0]["id"] == scene_id

    stale_confirmation = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-revisions",
        json={**impact_payload, "confirmed": True, "impact_hash": "0" * 64},
    )
    assert stale_confirmation.status_code == 409
    assert stale_confirmation.json()["error"]["code"] == "RELATIONSHIP_REVISION_IMPACT_STALE"

    created = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-revisions",
        json={**impact_payload, "confirmed": True, "impact_hash": impact["impact_hash"]},
    )
    assert created.status_code == 201, created.text
    revision = created.json()["data"]["revision_graph"]
    assert revision["status"] == "DRAFT"
    assert revision["editability"]["semantic_editable"] is True

    repeated_impact = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-revision-impact",
        json={**impact_payload, "expected_version": 4},
    )
    repeated = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-revisions",
        json={
            **impact_payload,
            "expected_version": 4,
            "confirmed": True,
            "impact_hash": repeated_impact.json()["data"]["impact_hash"],
        },
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "RELATIONSHIP_REVISION_ALREADY_OPEN"
    with factory() as session:
        change_set = session.scalar(
            select(ChangeSet).where(ChangeSet.result_relationship_graph_id == revision["id"])
        )
        assert change_set is not None
        assert change_set.base_timeline_id is None
        assert change_set.status == "CONFIRMED"

    identical = await client.get(f"/api/v1/relationship-graphs/{graph['id']}/diff/{revision['id']}")
    assert identical.status_code == 200
    assert identical.json()["data"]["changes"] == []

    revised_payload = revision["graph"]
    revised_payload["edges"][0]["true_relationship"] = "彼此掌握把柄的相互利用者"
    saved = await client.patch(
        f"/api/v1/relationship-graphs/{revision['id']}",
        json={
            "expected_project_version": 4,
            "expected_graph_version": 1,
            **revised_payload,
        },
    )
    assert saved.status_code == 200, saved.text
    compared = await client.get(f"/api/v1/relationship-graphs/{graph['id']}/diff/{revision['id']}")
    change = compared.json()["data"]["changes"][0]
    assert change["priority"] == "P1"
    assert "true_relationship" in change["fields"]

    workspace = await client.get(f"/api/v1/projects/{PROJECT_ID}/story-workspace")
    assert workspace.json()["data"]["relationship_graph_stale"] is True
    blocked = await client.post(
        f"/api/v1/scripts/{script_id}/approve",
        headers={"Idempotency-Key": "stale-script"},
        json={"expected_version": 5, "actor": "reviewer"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SCRIPT_RELATIONSHIP_GRAPH_OUTDATED"


@pytest.mark.anyio
async def test_relationship_graph_reject_preserves_reviewed_version_and_creates_draft(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    submitted = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/submit",
        json={"expected_project_version": 2, "expected_graph_version": 1},
    )
    assert submitted.status_code == 200

    rejected = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/reject",
        json={
            "expected_project_version": 3,
            "expected_graph_version": 2,
            "actor": "reviewer",
            "note": "补强两人结盟前的利益冲突。",
            "issues": ["CORE_CONFLICT_WEAK"],
        },
    )
    assert rejected.status_code == 200, rejected.text
    result = rejected.json()["data"]
    assert result["rejected_graph"]["status"] == "SUPERSEDED"
    assert result["revision_graph"]["status"] == "DRAFT"
    assert result["revision_graph"]["parent_version_id"] == graph["id"]

    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        review = session.scalar(
            select(ReviewRecord).where(
                ReviewRecord.entity_type == "relationship_graph",
                ReviewRecord.entity_id == graph["id"],
                ReviewRecord.decision == "REJECT",
            )
        )
        assert review is not None
        assert review.status == "REJECTED"
        assert json.loads(review.issues_json) == ["CORE_CONFLICT_WEAK"]


@pytest.mark.anyio
async def test_relationship_graph_approval_rejects_blockers_and_version_conflicts(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    invalid = valid_graph()
    invalid["edges"][0]["is_core"] = False  # type: ignore[index]
    invalid["edges"][0]["conflict_intensity"] = 0  # type: ignore[index]
    invalid["edges"][0]["relationship_types"] = ["OTHER"]  # type: ignore[index]
    invalid["beats"] = []
    invalid["core_relationship_keys"] = []
    graph = await create_graph(client, invalid)
    assert graph["editability"]["can_approve"] is False

    graph_conflict = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/submit",
        json={"expected_project_version": 2, "expected_graph_version": 99},
    )
    assert graph_conflict.status_code == 409
    assert graph_conflict.json()["error"]["code"] == "RELATIONSHIP_VERSION_CONFLICT"

    approval = await client.post(
        f"/api/v1/relationship-graphs/{graph['id']}/approve",
        json={"expected_project_version": 2, "expected_graph_version": 1},
    )
    assert approval.status_code == 422
    error = approval.json()["error"]
    assert error["code"] == "RELATIONSHIP_GRAPH_VALIDATION_FAILED"
    issue_codes = {item["code"] for item in error["details"]["issues"]}
    assert {
        "MISSING_CORE_RELATIONSHIP",
        "MISSING_PRIMARY_CONFLICT",
        "MISSING_RELATIONSHIP_BEAT",
    } <= (issue_codes)


@pytest.mark.anyio
async def test_relationship_graph_rejects_unknown_character_without_writing(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    invalid = valid_graph()
    invalid["edges"][0]["target_character_key"] = "ghost"  # type: ignore[index]

    response = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/relationship-graphs",
        json={
            "expected_project_version": 1,
            "story_bible_version_id": STORY_BIBLE_ID,
            "graph": invalid,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CHARACTER_REFERENCE"
    listed = await client.get(f"/api/v1/projects/{PROJECT_ID}/relationship-graphs")
    assert listed.json()["data"] == []


@pytest.mark.anyio
async def test_relationship_graph_editability_matrix_is_computed_by_server(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    graph_id = graph["id"]
    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)

    graph_states = {
        "DRAFT": (True, None),
        "GENERATING": (False, "GRAPH_GENERATING"),
        "READY_FOR_REVIEW": (False, "GRAPH_SUBMITTED"),
        "APPROVED": (False, "GRAPH_APPROVED"),
        "SUPERSEDED": (False, "GRAPH_SUPERSEDED"),
        "FAILED": (False, "GRAPH_FAILED"),
    }
    for graph_status, expected in graph_states.items():
        with factory() as session:
            relationship_graph = session.get(RelationshipGraphVersion, graph_id)
            project = session.get(Project, PROJECT_ID)
            assert relationship_graph is not None and project is not None
            relationship_graph.status = graph_status
            project.status = "RELATIONSHIP_READY"
            session.commit()
        response = await client.get(f"/api/v1/relationship-graphs/{graph_id}")
        assert response.status_code == 200
        editability = response.json()["data"]["editability"]
        assert (editability["semantic_editable"], editability["reason_code"]) == expected

    for project_status, reason_code in {
        "BLOCKED": "PROJECT_BLOCKED",
        "ARCHIVED": "PROJECT_ARCHIVED",
        "PRODUCING": "PROJECT_EDIT_WINDOW_CLOSED",
    }.items():
        with factory() as session:
            relationship_graph = session.get(RelationshipGraphVersion, graph_id)
            project = session.get(Project, PROJECT_ID)
            assert relationship_graph is not None and project is not None
            relationship_graph.status = "DRAFT"
            project.status = project_status
            session.commit()
        response = await client.get(f"/api/v1/relationship-graphs/{graph_id}")
        editability = response.json()["data"]["editability"]
        assert editability["semantic_editable"] is False
        assert editability["reason_code"] == reason_code
        if project_status == "ARCHIVED":
            assert editability["layout_editable"] is False


@pytest.mark.anyio
async def test_character_revision_requires_review_and_creates_synchronized_drafts(
    client: AsyncClient,
) -> None:
    prepare_relationship_project()
    graph = await create_graph(client)
    request = {
        "base_story_bible_id": STORY_BIBLE_ID,
        "base_relationship_graph_id": graph["id"],
        "character_key": "protagonist",
        "changes": {
            "role": "母女旧案的共同追查者",
            "age": "31岁",
            "occupation": "调查记者",
            "personality": ["敏锐", "克制"],
        },
        "expected_version": 2,
    }

    reviewed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/character-revision-review",
        json=request,
    )
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()["data"]
    assert review["review"]["verdict"] == "CONFLICT"
    assert review["provider"] == "rules"
    assert review["affected"]["relationship_count"] == 1
    assert "role" in review["changed_fields"]

    stale = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/character-revisions",
        json={
            **request,
            "confirmed": True,
            "impact_hash": "0" * 64,
            "actor": "test-author",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CHARACTER_REVISION_IMPACT_STALE"

    confirmed = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/character-revisions",
        json={
            **request,
            "confirmed": True,
            "impact_hash": review["impact_hash"],
            "actor": "test-author",
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    revision = confirmed.json()["data"]
    assert revision["story_bible"]["status"] == "DRAFT"
    assert revision["story_bible"]["payload"]["characters"][0]["role"] == "母女旧案的共同追查者"
    assert revision["relationship_graph"]["status"] == "DRAFT"
    assert revision["relationship_graph"]["project_lock_version"] == 3
    assert revision["relationship_graph"]["graph"]["edges"][0]["locked"] is False
    assert "请重新核对相关关系" in revision["relationship_graph"]["graph"]["generation_notes"][-1]

    factory = sessionmaker(bind=get_engine(get_settings().database_url), expire_on_commit=False)
    with factory() as session:
        source_bible = session.get(StoryBibleVersion, STORY_BIBLE_ID)
        source_graph = session.get(RelationshipGraphVersion, graph["id"])
        project = session.get(Project, PROJECT_ID)
        change_set = session.scalar(
            select(ChangeSet).where(
                ChangeSet.result_relationship_graph_id == revision["relationship_graph"]["id"]
            )
        )
        assert source_bible is not None and source_bible.status == "SUPERSEDED"
        assert source_graph is not None and source_graph.status == "SUPERSEDED"
        assert project is not None and project.lock_version == 3
        assert change_set is not None and change_set.status == "CONFIRMED"
