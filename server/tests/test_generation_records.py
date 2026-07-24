import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import GenerationRecord
from app.db.session import get_engine
from app.seed import PROJECT_ID
from app.services.generation_records import ensure_generation_record
from app.services.jobs import enqueue_job

pytestmark = pytest.mark.anyio


async def test_generation_record_is_idempotent_and_carries_job_trace(
    client: AsyncClient,
) -> None:
    del client
    with Session(get_engine(get_settings().database_url)) as session:
        job, _ = enqueue_job(
            session,
            project_id=PROJECT_ID,
            job_type="GENERATE_TEST_MEDIA",
            entity_type="shot",
            entity_id="test-shot",
            idempotency_key="test:generation-record-observability",
            input_payload={
                "prompt": "生成测试画面",
                "command_id": "test-command-id",
            },
            label="生成记录测试",
            stage="等待测试",
            trace_id="generation-record-trace",
        )
        session.flush()

        first = ensure_generation_record(
            session,
            job=job,
            capability="TEST_IMAGE",
            provider="mock",
            model="deterministic-image-v1",
            config_version="test-v1",
            prompt="生成测试画面",
            seed=42,
            reference_asset_ids=["reference-1"],
            output_asset_id="output-asset",
            entity_type="take",
            entity_id="test-take",
            latency_ms=12,
            estimated_cost_usd=0.0,
            metadata={"quality_status": "PASSED"},
        )
        replayed = ensure_generation_record(
            session,
            job=job,
            capability="TEST_IMAGE",
            provider="mock",
            model="deterministic-image-v1",
            config_version="test-v1",
            prompt="生成测试画面",
            seed=42,
            reference_asset_ids=["reference-1"],
            output_asset_id="output-asset",
            entity_type="take",
            entity_id="test-take",
            latency_ms=12,
            estimated_cost_usd=0.0,
        )

        assert replayed.id == first.id
        assert (
            session.scalar(
                select(func.count(GenerationRecord.id)).where(
                    GenerationRecord.job_id == job.id
                )
            )
            == 1
        )
        metadata = json.loads(first.metadata_json)
        assert metadata == {
            "command_id": "test-command-id",
            "job_idempotency_key": "test:generation-record-observability",
            "job_request_hash": job.request_hash,
            "job_type": "GENERATE_TEST_MEDIA",
            "quality_status": "PASSED",
            "trace_id": "generation-record-trace",
        }
        assert first.status == "SUCCEEDED"
        assert first.completed_at is not None
