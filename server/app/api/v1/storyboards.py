from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import get_settings
from app.db.models import (
    Asset,
    Episode,
    Job,
    Project,
    Scene,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    TimelineVersion,
)
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    ShotActionRewriteRequest,
    ShotDurationRecommendationRequest,
    ShotEndStateRewriteRequest,
    ShotLockUpdateRequest,
    ShotSpecCompileRequest,
    ShotSpecUpdateRequest,
    StoryboardShotRegenerateRequest,
    StoryPackageGenerateRequest,
)
from app.services.domain_commands import dispatch_domain_command
from app.services.projects import content_hash, version_conflict
from app.services.shot_action_rewrite import (
    merge_rewritten_actions,
    rewrite_shot_action,
)
from app.services.shot_duration_recommendation import recommend_shot_duration
from app.services.shot_end_state_rewrite import (
    merge_rewritten_end_state,
    rewrite_shot_end_state,
)
from app.services.shot_specs import (
    compile_shot_spec,
    load_shot_spec_contract,
    remove_shot_constraint_lock,
    upsert_shot_constraint_lock,
    validate_storyboard_shot_specs,
    write_shot_spec,
)
from app.services.storyboards_v2 import (
    list_workflow_runs,
    storyboard_workspace,
)

router = APIRouter(prefix="/api/v1", tags=["storyboards"])


def _workspace_shot(session: Session, project_id: str, shot_spec_id: str) -> dict[str, object]:
    workspace = storyboard_workspace(session, project_id)
    return next(
        (
            item
            for item in workspace["shots"]
            if isinstance(item, dict) and item.get("shot_spec_id") == shot_spec_id
        ),
        {},
    )


@router.get("/projects/{project_id}/storyboard-workspace")
def get_storyboard_workspace(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(storyboard_workspace(session, project_id))


@router.get("/projects/{project_id}/workflow-runs")
def workflow_runs(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_workflow_runs(session, project_id))


@router.patch("/shot-specs/{shot_spec_id}")
def update_structured_shot_spec(
    shot_spec_id: str,
    payload: ShotSpecUpdateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    shot = session.get(Shot, spec.shot_id) if spec is not None else None
    storyboard = (
        session.get(StoryboardVersion, spec.storyboard_version_id)
        if spec is not None
        else None
    )
    if spec is None or shot is None or storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{storyboard.project_id}:domain-command:{idempotency_key}")
    )
    changes = payload.model_dump(
        mode="json",
        exclude={"expected_version", "actor"},
        exclude_none=True,
    )
    execution = dispatch_domain_command(
        session,
        project_id=storyboard.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="UPDATE_SHOT_SPEC",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=shot.id,
            target_version_id=spec.id,
            expected_version=ExpectedVersion(
                object_lock_version=payload.expected_version,
                target_version_id=spec.id,
            ),
            payload={**changes, "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"structured-shot-spec-update:{spec.id}",
                "expected_version": payload.expected_version,
                "changes": changes,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed
    ).lower()
    return success(
        {
            "shot": _workspace_shot(session, storyboard.project_id, spec.id),
            "mutation": execution.result,
        }
    )


@router.post("/shot-specs/{shot_spec_id}/compile")
def compile_structured_shot_spec(
    shot_spec_id: str,
    payload: ShotSpecCompileRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    _resolved, report, compiled, snapshot = compile_shot_spec(
        session,
        spec,
        adapter_name=payload.adapter,
        store=True,
    )
    session.commit()
    return success(
        {
            "prompt_compiled": compiled.prompt,
            "prompt_adapter": compiled.adapter,
            "compiler_version": compiled.compiler_version,
            "compiler_input_hash": compiled.compiler_input_hash,
            "prompt_compiled_hash": compiled.prompt_hash,
            "validation_report": report.model_dump(mode="json"),
            "lock_snapshot": snapshot,
        }
    )


@router.post("/shot-specs/{shot_spec_id}/duration-recommendation")
async def recommend_structured_shot_duration(
    shot_spec_id: str,
    payload: ShotDurationRecommendationRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    result = await recommend_shot_duration(get_settings(), payload.shot_spec)
    return success(result.model_dump(mode="json"))


@router.post("/shot-specs/{shot_spec_id}/action-rewrite")
async def rewrite_structured_shot_action(
    shot_spec_id: str,
    payload: ShotActionRewriteRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    storyboard = session.get(StoryboardVersion, spec.storyboard_version_id)
    shot = session.get(Shot, spec.shot_id)
    project = (
        session.get(Project, storyboard.project_id)
        if storyboard is not None
        else None
    )
    if storyboard is None or shot is None or project is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_SPEC_PROJECT_MISSING",
                "message": "镜头规格未关联到有效项目",
            },
        )

    result = await rewrite_shot_action(get_settings(), payload.shot_spec)
    persisted = load_shot_spec_contract(session, spec)
    revised = merge_rewritten_actions(persisted, result.shot_spec)
    spec, _report, _compiled = write_shot_spec(
        session,
        spec,
        revised,
        actor="创作者",
        change_reason="用户采用 AI 动作精简建议并重新运行镜头检查",
        adapter_name=spec.prompt_adapter,
    )

    takes = list(session.scalars(select(Take).where(Take.shot_id == shot.id)).all())
    asset_ids = {take.asset_id for take in takes}
    for take in takes:
        take.status = "SUSPECT"
        if take.approval == "APPROVED":
            take.approval = "SUPERSEDED"
    if asset_ids:
        for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids))):
            asset.status = "SUSPECT"
    for timeline in session.scalars(
        select(TimelineVersion).where(TimelineVersion.project_id == project.id)
    ):
        if timeline.status != "SUPERSEDED":
            timeline.status = "SUSPECT"
    project.preview_approved = False
    project.updated_at = datetime.now(UTC)
    session.commit()

    applied_result = result.model_copy(
        update={"shot_spec": load_shot_spec_contract(session, spec)}
    )
    return success(applied_result.model_dump(mode="json"))


@router.post("/shot-specs/{shot_spec_id}/end-state-rewrite")
async def rewrite_structured_shot_end_state(
    shot_spec_id: str,
    payload: ShotEndStateRewriteRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    storyboard = session.get(StoryboardVersion, spec.storyboard_version_id)
    shot = session.get(Shot, spec.shot_id)
    project = (
        session.get(Project, storyboard.project_id)
        if storyboard is not None
        else None
    )
    if storyboard is None or shot is None or project is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_SPEC_PROJECT_MISSING",
                "message": "镜头规格未关联到有效项目",
            },
        )
    result = await rewrite_shot_end_state(get_settings(), payload.shot_spec)
    persisted = load_shot_spec_contract(session, spec)
    revised = merge_rewritten_end_state(persisted, result.shot_spec)
    spec, _report, _compiled = write_shot_spec(
        session,
        spec,
        revised,
        actor="创作者",
        change_reason="用户采用 AI 结尾状态优化建议并重新运行镜头检查",
        adapter_name=spec.prompt_adapter,
    )
    takes = list(session.scalars(select(Take).where(Take.shot_id == shot.id)).all())
    asset_ids = {take.asset_id for take in takes}
    for take in takes:
        take.status = "SUSPECT"
        if take.approval == "APPROVED":
            take.approval = "SUPERSEDED"
    if asset_ids:
        for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids))):
            asset.status = "SUSPECT"
    for timeline in session.scalars(
        select(TimelineVersion).where(TimelineVersion.project_id == project.id)
    ):
        if timeline.status != "SUPERSEDED":
            timeline.status = "SUSPECT"
    project.preview_approved = False
    project.updated_at = datetime.now(UTC)
    session.commit()
    applied_result = result.model_copy(
        update={"shot_spec": load_shot_spec_contract(session, spec)}
    )
    return success(applied_result.model_dump(mode="json"))


@router.put("/projects/{project_id}/shot-locks")
def update_shot_lock(
    project_id: str,
    payload: ShotLockUpdateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "项目不存在"},
        )
    if project.lock_version != payload.expected_version:
        raise version_conflict(project, payload.expected_version)
    target_specs: list[ShotSpec]
    if payload.scope == "PROJECT":
        if payload.target_id != project.id:
            raise HTTPException(
                status_code=422,
                detail={"code": "SHOT_LOCK_TARGET_INVALID", "message": "Project Lock 目标不正确"},
            )
        target_specs = list(
            session.scalars(
                select(ShotSpec)
                .join(
                    StoryboardVersion,
                    StoryboardVersion.id == ShotSpec.storyboard_version_id,
                )
                .where(StoryboardVersion.project_id == project.id)
            ).all()
        )
    elif payload.scope == "SCENE":
        scene = session.scalar(
            select(Scene)
            .join(Episode, Episode.id == Scene.episode_id)
            .where(Scene.id == payload.target_id, Episode.project_id == project.id)
        )
        if scene is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "SHOT_LOCK_TARGET_INVALID", "message": "Scene Lock 目标不正确"},
            )
        target_specs = list(
            session.scalars(
                select(ShotSpec)
                .join(Shot, Shot.id == ShotSpec.shot_id)
                .where(Shot.scene_id == scene.id)
            ).all()
        )
    else:
        spec = session.get(ShotSpec, payload.target_id)
        storyboard = (
            session.get(StoryboardVersion, spec.storyboard_version_id)
            if spec is not None
            else None
        )
        if spec is None or storyboard is None or storyboard.project_id != project.id:
            raise HTTPException(
                status_code=422,
                detail={"code": "SHOT_LOCK_TARGET_INVALID", "message": "Field Lock 目标不正确"},
            )
        target_specs = [spec]

    if payload.locked:
        lock = upsert_shot_constraint_lock(
            session,
            project_id=project.id,
            scope=payload.scope,
            target_id=payload.target_id,
            field_path=payload.field_path,
            value=payload.value,
            owner=payload.actor,
        )
        lock_id: str | None = lock.id
    else:
        remove_shot_constraint_lock(
            session,
            project_id=project.id,
            scope=payload.scope,
            target_id=payload.target_id,
            field_path=payload.field_path,
        )
        lock_id = None
    session.flush()
    for spec in target_specs:
        compile_shot_spec(session, spec, store=True)
    for storyboard_id in {spec.storyboard_version_id for spec in target_specs}:
        validate_storyboard_shot_specs(session, storyboard_id)
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    session.commit()
    return success(
        {
            "locked": payload.locked,
            "lock_id": lock_id,
            "scope": payload.scope,
            "target_id": payload.target_id,
            "field_path": payload.field_path,
            "project_lock_version": project.lock_version,
            "affected_shot_spec_ids": [item.id for item in target_specs],
        }
    )


@router.post(
    "/shot-specs/{shot_spec_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_shot_spec(
    shot_spec_id: str,
    payload: StoryboardShotRegenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    spec = session.get(ShotSpec, shot_spec_id)
    storyboard = (
        session.get(StoryboardVersion, spec.storyboard_version_id)
        if spec is not None
        else None
    )
    if spec is None or storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜镜头不存在"},
        )
    active_before = session.scalar(
        select(Job.id).where(
            Job.job_type == "GENERATE_STORYBOARD_TAKE",
            Job.entity_id == spec.id,
            Job.status.in_({"PENDING", "RETRY_WAIT", "RUNNING", "CANCEL_REQUESTED"}),
        )
    )
    command_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{storyboard.project_id}:domain-command:{idempotency_key}",
        )
    )
    execution = dispatch_domain_command(
        session,
        project_id=storyboard.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="REQUEST_STORYBOARD_SHOT_REGENERATION",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=spec.id,
            target_version_id=storyboard.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=storyboard.id,
                target_hash=storyboard.content_hash,
            ),
            payload={"note": payload.note, "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"storyboard-shot-regeneration:{spec.id}",
                "storyboard_version_id": storyboard.id,
                "note": payload.note,
                "actor": payload.actor,
                "confirmed": True,
            }
        ),
    )
    replayed = execution.idempotency_replayed or active_before is not None
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(execution.result)


@router.post(
    "/storyboards/{storyboard_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_storyboard_version(
    storyboard_id: str,
    payload: StoryPackageGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    storyboard = session.get(StoryboardVersion, storyboard_id)
    if storyboard is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "分镜版本不存在"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{storyboard.project_id}:domain-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=storyboard.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="APPROVE_STORYBOARD",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=storyboard.id,
            target_version_id=storyboard.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=storyboard.id,
                target_hash=storyboard.content_hash,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"storyboard-approval:{storyboard.id}",
                "expected_version": payload.expected_version,
                "actor": payload.actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(
        execution.idempotency_replayed
    ).lower()
    return success(execution.result)
