import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.contracts import JobExecutionContext, JobExecutionError
from app.jobs.registry import register_job_handler
from app.services.creative_story import (
    materialize_script_package,
    materialize_story_directions,
    materialize_story_package,
    materialize_story_structure,
    script_package_generation_context,
)
from app.services.proposals import materialize_mock_proposal
from app.services.text_provider import (
    RoutedTextProvider,
    StoryDirection,
    TextGenerationResult,
    TextProviderError,
    assemble_story_package,
)


async def _await_with_progress[T](
    context: JobExecutionContext,
    session: Session,
    job: Job,
    operation: Awaitable[T],
    *,
    initial_progress: float,
    ceiling: float,
    stage: str | Callable[[], str],
    interval_seconds: float = 2.0,
) -> T:
    task = asyncio.ensure_future(operation)
    progress = initial_progress
    elapsed_seconds = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval_seconds)
            if task in done:
                return task.result()
            elapsed_seconds += interval_seconds
            progress = min(ceiling, progress + 5)
            elapsed_bucket = int(elapsed_seconds // 10) * 10
            attempt = getattr(job, "attempt", 1)
            max_attempts = getattr(job, "max_attempts", 1)
            current_stage = stage() if callable(stage) else stage
            waiting_stage = (
                f"{current_stage} · 已等待 {elapsed_bucket} 秒 · "
                f"任务尝试 {attempt}/{max_attempts}"
            )
            await context.checkpoint(session, job, progress, waiting_stage)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _cached_story_direction_results(job: Job) -> dict[str, TextGenerationResult]:
    output_json = getattr(job, "output_json", None)
    if not output_json:
        return {}
    try:
        output = json.loads(output_json)
    except json.JSONDecodeError:
        return {}
    routes = output.get("story_direction_routes") if isinstance(output, dict) else None
    if not isinstance(routes, dict):
        return {}

    results: dict[str, TextGenerationResult] = {}
    for direction_key, raw in routes.items():
        if not isinstance(direction_key, str) or not isinstance(raw, dict):
            continue
        try:
            direction = StoryDirection.model_validate(raw.get("payload"))
            if direction.direction_key != direction_key:
                continue
            results[direction_key] = TextGenerationResult(
                payload=direction.model_dump(mode="json"),
                provider=str(raw["provider"]),
                model=str(raw["model"]),
                request_id=(
                    str(raw["request_id"])
                    if raw.get("request_id") is not None
                    else None
                ),
                repair_attempts=int(raw.get("repair_attempts", 0)),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            continue
    return results


def _story_direction_result_record(result: TextGenerationResult) -> dict[str, object]:
    return {
        "payload": result.payload,
        "provider": result.provider,
        "model": result.model,
        "request_id": result.request_id,
        "repair_attempts": result.repair_attempts,
    }


@register_job_handler("GENERATE_PROPOSAL")
async def generate_proposal(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "解析项目简报快照")
    fail_until_attempt = int(payload.get("fail_until_attempt", 0))
    if job.attempt <= fail_until_attempt:
        raise JobExecutionError(
            "MOCK_PROVIDER_TEMPORARY_FAILURE",
            "模拟服务按测试输入触发了临时失败",
            retryable=True,
        )
    await context.checkpoint(session, job, 55, "生成三幕八镜导演方案")
    await context.checkpoint(session, job, 85, "校验时长与结构")
    proposal = materialize_mock_proposal(session, job)
    return {
        "proposal_id": proposal.id,
        "proposal_version": proposal.version,
        "provider": "mock",
    }


@register_job_handler("GENERATE_STORY_DIRECTIONS")
async def generate_story_directions(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "校验项目简报第 2 版与目标优先级")
    provider = RoutedTextProvider()
    cached_results = _cached_story_direction_results(job)
    cached_records = {
        direction_key: _story_direction_result_record(result)
        for direction_key, result in cached_results.items()
    }
    completed_routes = set(cached_results)

    def current_stage() -> str:
        completed = len(completed_routes)
        if completed:
            return f"已完成 {completed}/3 个故事方向，正在生成其余方向"
        return "正在分别生成 3 个差异化故事方向"

    async def on_route_complete(
        direction_key: str,
        result: TextGenerationResult,
    ) -> None:
        completed_routes.add(direction_key)
        cached_records[direction_key] = _story_direction_result_record(result)
        save_intermediate = getattr(context, "save_intermediate_output", None)
        if save_intermediate is not None:
            await save_intermediate(
                session,
                job,
                {"story_direction_routes": dict(cached_records)},
            )
        completed = len(completed_routes)
        progress = {1: 35, 2: 50, 3: 65}[completed]
        stage = (
            "3 个故事方向均已生成，正在执行结构校验"
            if completed == 3
            else f"已完成 {completed}/3 个故事方向，继续生成其余 {3 - completed} 个"
        )
        await context.checkpoint(session, job, progress, stage)

    try:
        result = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_directions(
                context.settings,
                dict(payload["brief"]),
                existing_results=cached_results,
                on_route_complete=on_route_complete,
            ),
            initial_progress=15,
            ceiling=40,
            stage=current_stage,
        )
    except TextProviderError as exc:
        raise JobExecutionError(
            exc.code, str(exc), retryable=exc.retryable, details=exc.details
        ) from exc
    await context.checkpoint(session, job, 75, "校验 3 个方向的结构与时长")
    await context.checkpoint(session, job, 88, "写入故事方向与版本事实")
    directions = materialize_story_directions(session, job, result)
    return {
        "batch_size": len(directions),
        "proposal_ids": [item.id for item in directions],
        "provider": result.provider,
        "model": result.model,
    }


@register_job_handler("GENERATE_STORY_PACKAGE")
async def generate_story_package(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "加载已选故事内核")
    provider = RoutedTextProvider()
    try:
        foundation = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_story_foundation(
                context.settings,
                dict(payload["brief"]),
                dict(payload["direction"]),
            ),
            initial_progress=15,
            ceiling=35,
            stage="正在生成故事设定集与分集大纲",
        )
        script = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_episode_script(
                context.settings,
                dict(payload["brief"]),
                dict(payload["direction"]),
                foundation.payload,
            ),
            initial_progress=35,
            ceiling=55,
            stage="正在生成首集结构化剧本",
        )
        review = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_narrative_review(
                context.settings,
                dict(payload["brief"]),
                dict(payload["direction"]),
                script.payload,
            ),
            initial_progress=55,
            ceiling=75,
            stage="正在生成叙事引擎并执行结构质检",
        )
        result = assemble_story_package(foundation, script, review)
    except TextProviderError as exc:
        raise JobExecutionError(
            exc.code, str(exc), retryable=exc.retryable, details=exc.details
        ) from exc
    except ValidationError as exc:
        raise JobExecutionError(
            "STORY_PACKAGE_ASSEMBLY_INVALID",
            "分阶段结果合并后仍未通过创作合同校验",
            retryable=False,
            details={"validation_error": str(exc)[:8000]},
        ) from exc
    await context.checkpoint(session, job, 82, "校验故事设定集、分集大纲与剧本")
    await context.checkpoint(session, job, 90, "写入创作包与剧本版本事实")
    script = materialize_story_package(session, job, result)
    return {
        "script_id": script.id,
        "episode_ordinal": script.episode_ordinal,
        "provider": result.provider,
        "model": result.model,
    }


@register_job_handler("GENERATE_STORY_STRUCTURE")
async def generate_story_structure(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "加载已选故事内核与项目简报")
    provider = RoutedTextProvider()
    generation_stage = "正在生成故事设定集与角色关系草案"
    validation_diagnostics: list[dict[str, object]] = []

    async def mark_relationship_validation(output_attempt: int) -> None:
        nonlocal generation_stage
        progress = min(78, 70 + (output_attempt - 1) * 4)
        generation_stage = (
            "模型输出完成，正在校验角色关系"
            if output_attempt == 1
            else f"模型修复输出完成，正在校验角色关系（{output_attempt - 1}/2）"
        )
        await context.checkpoint(session, job, progress, generation_stage)

    async def record_validation_failure(
        model_attempt: int,
        diagnostic: dict[str, object],
    ) -> None:
        nonlocal generation_stage
        validation_diagnostics.append(diagnostic)
        generation_stage = (
            f"第 {model_attempt}/3 次模型输出未通过校验，正在生成修复版"
        )
        recorder = getattr(context, "record_diagnostics", None)
        if recorder is not None:
            await recorder(
                session,
                job,
                {
                    "phase": "story_structure_validation",
                    "model_attempt": model_attempt,
                    "max_model_attempts": 3,
                    "attempts": validation_diagnostics,
                },
            )
        await context.checkpoint(
            session,
            job,
            min(78, 70 + model_attempt * 2),
            generation_stage,
        )

    try:
        result = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_story_structure(
                context.settings,
                dict(payload["brief"]),
                dict(payload["direction"]),
                on_model_output=mark_relationship_validation,
                on_validation_failure=record_validation_failure,
            ),
            initial_progress=15,
            ceiling=70,
            stage=lambda: generation_stage,
        )
    except TextProviderError as exc:
        raise JobExecutionError(
            exc.code, str(exc), retryable=exc.retryable, details=exc.details
        ) from exc
    await context.checkpoint(session, job, 82, "角色关系校验通过")
    await context.checkpoint(session, job, 90, "写入故事结构与关系版本事实")
    graph = materialize_story_structure(session, job, result)
    return {
        "relationship_graph_id": graph.id,
        "story_bible_version_id": graph.story_bible_version_id,
        "provider": result.provider,
        "model": result.model,
    }


@register_job_handler("GENERATE_SCRIPT_PACKAGE")
async def generate_script_package(
    context: JobExecutionContext,
    session: Session,
    job: Job,
    payload: dict[str, object],
) -> dict[str, object]:
    await context.checkpoint(session, job, 15, "加载已批准故事设定与角色关系基线")
    brief, direction, story_bible, relationship_graph = script_package_generation_context(
        session, job
    )
    provider = RoutedTextProvider()
    try:
        result = await _await_with_progress(
            context,
            session,
            job,
            provider.generate_script_package(
                context.settings,
                brief,
                direction,
                story_bible,
                relationship_graph,
            ),
            initial_progress=15,
            ceiling=70,
            stage="正在生成关系驱动的分集大纲与首集剧本",
        )
    except TextProviderError as exc:
        raise JobExecutionError(
            exc.code, str(exc), retryable=exc.retryable, details=exc.details
        ) from exc
    await context.checkpoint(session, job, 78, "校验关系重排与认证变化强引用")
    await context.checkpoint(session, job, 90, "写入分集大纲与剧本版本事实")
    script = materialize_script_package(session, job, result)
    return {
        "script_id": script.id,
        "episode_ordinal": script.episode_ordinal,
        "relationship_graph_id": script.relationship_graph_version_id,
        "provider": result.provider,
        "model": result.model,
    }
