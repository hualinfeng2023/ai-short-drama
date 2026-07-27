from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import BriefVersion, Project, Scene, Shot, ShotSpec
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    BriefAvoidancesSuggestionRead,
    BriefAvoidancesSuggestionRequest,
    BriefBlockingQuestionsSuggestionRead,
    BriefBlockingQuestionsSuggestionRequest,
    BriefEmotionalRewardSuggestionRead,
    BriefEmotionalRewardSuggestionRequest,
    BriefRequirementsSuggestionRead,
    BriefRequirementsSuggestionRequest,
    BriefStoryRewriteRead,
    BriefStoryRewriteRequest,
    BriefVersionRead,
    EpisodeRead,
    ProjectCreate,
    ProjectNameSuggestionRead,
    ProjectNameSuggestionRequest,
    ProjectRead,
    ProjectReadinessRead,
    ProjectUpdate,
    SceneRead,
    SceneShotOrderRequest,
    ShotSpecUpdateRequest,
)
from app.services.brief_assistant import (
    suggest_brief_avoidances,
    suggest_brief_blocking_questions,
    suggest_brief_requirements,
)
from app.services.domain_commands import (
    dispatch_domain_command,
    dispatch_project_create_command,
    dispatch_project_delete_command,
)
from app.services.emotional_reward_suggestion import suggest_emotional_reward
from app.services.project_naming import ProjectNamingError, suggest_project_name
from app.services.project_readiness import get_project_readiness
from app.services.projects import (
    content_hash,
    list_brief_versions,
)
from app.services.story_rewriter import StoryRewriteError, rewrite_story_idea
from app.services.workspace import (
    episode_or_404,
    get_workspace,
    list_projects,
    project_or_404,
    scene_or_404,
    shot_or_404,
    shot_to_read,
)

router = APIRouter(prefix="/api/v1", tags=["workspace"])


@router.get("/projects")
def projects(session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_projects(session))


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create(
    payload: ProjectCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    fingerprint = content_hash(
        {
            "route": "projects:create",
            "project": payload.model_dump(mode="json"),
            "actor": "demo-user",
        }
    )
    command_id = str(uuid5(NAMESPACE_URL, f"CREATE_PROJECT:{idempotency_key}"))
    execution = await dispatch_project_create_command(
        session,
        command=DirectorCommand(
            command_id=command_id,
            command_type="CREATE_PROJECT",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=command_id,
            target_version_id=command_id,
            expected_version=ExpectedVersion(
                project_lock_version=1,
                target_version_id=command_id,
            ),
            payload={"project": payload.model_dump(mode="json"), "confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.post("/projects/{project_id}/name-suggestions")
async def project_name_suggestion(
    project_id: str,
    payload: ProjectNameSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    try:
        result: ProjectNameSuggestionRead = await suggest_project_name(
            payload.model_dump(mode="json"),
            allow_fallback=False,
        )
    except ProjectNamingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROJECT_NAMING_UNAVAILABLE",
                "message": str(exc),
                "user_action": "检查文本生成服务配置后重试；原名称不会被修改",
                "retryable": True,
            },
        ) from exc
    return success(result)


@router.post("/projects/{project_id}/brief-requirement-suggestions")
async def brief_requirement_suggestion(
    project_id: str,
    payload: BriefRequirementsSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    result: BriefRequirementsSuggestionRead = await suggest_brief_requirements(
        payload.model_dump(mode="json")
    )
    return success(result)


@router.post("/projects/{project_id}/brief-emotional-reward-suggestions")
async def brief_emotional_reward_suggestion(
    project_id: str,
    payload: BriefEmotionalRewardSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    result: BriefEmotionalRewardSuggestionRead = await suggest_emotional_reward(
        payload.model_dump(mode="json")
    )
    return success(result)


@router.post("/projects/{project_id}/brief-avoidance-suggestions")
async def brief_avoidance_suggestion(
    project_id: str,
    payload: BriefAvoidancesSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    result: BriefAvoidancesSuggestionRead = await suggest_brief_avoidances(
        payload.model_dump(mode="json")
    )
    return success(result)


@router.post("/projects/{project_id}/brief-blocking-question-suggestions")
async def brief_blocking_question_suggestion(
    project_id: str,
    payload: BriefBlockingQuestionsSuggestionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    result: BriefBlockingQuestionsSuggestionRead = await suggest_brief_blocking_questions(
        payload.model_dump(mode="json")
    )
    return success(result)


@router.post("/projects/{project_id}/story-rewrites")
async def story_rewrite(
    project_id: str,
    payload: BriefStoryRewriteRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_or_404(session, project_id)
    try:
        result: BriefStoryRewriteRead = await rewrite_story_idea(payload.model_dump(mode="json"))
    except StoryRewriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SEED_TEXT_UNAVAILABLE",
                "message": str(exc),
                "user_action": "检查 ARK_API_KEY 与 Doubao Seed 模型配置后重试",
                "retryable": True,
            },
        ) from exc
    return success(result)


@router.get("/projects/{project_id}")
def project(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(ProjectRead.model_validate(project_or_404(session, project_id)))


@router.delete("/projects/{project_id}")
def remove_project(
    project_id: str,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_model = session.get(Project, project_id)
    fingerprint = content_hash(
        {
            "route": f"projects:delete:{project_id}",
            "actor": "demo-user",
        }
    )
    effective_key = idempotency_key or f"project-delete-{project_id}"
    execution = dispatch_project_delete_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(uuid5(NAMESPACE_URL, f"{project_id}:DELETE_PROJECT:{effective_key}")),
            command_type="DELETE_PROJECT",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=project_id,
            target_version_id=project_id,
            expected_version=ExpectedVersion(
                project_lock_version=project_model.lock_version if project_model else 1,
                target_version_id=project_id,
            ),
            payload={"confirmed": True},
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/brief-versions")
def brief_versions(
    project_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    versions: list[BriefVersionRead] = list_brief_versions(session, project_id)
    return success(versions)


@router.patch("/projects/{project_id}")
def edit_project(
    project_id: str,
    payload: ProjectUpdate,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=160
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project_model = project_or_404(session, project_id)
    latest_brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    target_version_id = latest_brief.id if latest_brief is not None else project_model.id
    changes = payload.model_dump(mode="json", exclude={"expected_version"}, exclude_none=True)
    fingerprint = content_hash(
        {
            "route": f"projects:update:{project_id}",
            "expected_version": payload.expected_version,
            "changes": changes,
            "actor": "demo-user",
        }
    )
    effective_key = idempotency_key or f"project-update-{fingerprint}"
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(NAMESPACE_URL, f"{project_id}:UPDATE_PROJECT_BRIEF:{effective_key}")
            ),
            command_type="UPDATE_PROJECT_BRIEF",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=project_id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={"changes": changes, "confirmed": True},
            idempotency_key=effective_key,
        ),
        request_fingerprint=fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/workspace")
def workspace(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(get_workspace(session, project_id))


@router.get("/projects/{project_id}/readiness")
def readiness(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    result: ProjectReadinessRead = get_project_readiness(session, project_id)
    return success(result)


@router.get("/episodes/{episode_id}")
def episode(episode_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(EpisodeRead.model_validate(episode_or_404(session, episode_id)))


@router.get("/episodes/{episode_id}/scenes")
def episode_scenes(episode_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    episode_or_404(session, episode_id)
    scenes = session.scalars(
        select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.ordinal)
    ).all()
    return success([SceneRead.model_validate(scene) for scene in scenes])


@router.get("/scenes/{scene_id}")
def scene(scene_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    scene_model = scene_or_404(session, scene_id)
    shots = session.scalars(
        select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.ordinal)
    ).all()
    return success(
        {
            "scene": SceneRead.model_validate(scene_model),
            "shots": [shot_to_read(session, shot) for shot in shots],
        }
    )


@router.get("/shots/{shot_id}")
def shot(shot_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(shot_to_read(session, shot_or_404(session, shot_id)))


@router.patch("/shots/{shot_id}")
def update_shot_spec(
    shot_id: str,
    payload: ShotSpecUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    shot_model = shot_or_404(session, shot_id)
    project_id = shot_model.scene.episode.project_id
    spec = session.scalar(select(ShotSpec).where(ShotSpec.shot_id == shot_model.id))
    target_version_id = spec.id if spec is not None else shot_model.id
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{project_id}:domain-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    changes = payload.model_dump(
        mode="json",
        exclude={"expected_version", "actor"},
        exclude_none=True,
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="UPDATE_SHOT_SPEC",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=shot_model.id,
            target_version_id=target_version_id,
            expected_version=ExpectedVersion(
                object_lock_version=payload.expected_version,
                target_version_id=target_version_id,
            ),
            payload={**changes, "confirmed": True},
            idempotency_key=idempotency_key or f"shot-update-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"shot-update:{shot_model.id}",
                "expected_version": payload.expected_version,
                "changes": changes,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.put("/scenes/{scene_id}/shots/order")
def reorder_scene_shots(
    scene_id: str,
    payload: SceneShotOrderRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    scene_model = scene_or_404(session, scene_id)
    project = scene_model.episode.project
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{project.id}:domain-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    execution = dispatch_domain_command(
        session,
        project_id=project.id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="REORDER_SCENE_SHOTS",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=scene_model.id,
            target_version_id=scene_model.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=scene_model.id,
            ),
            payload={
                "shot_ids": payload.shot_ids,
                "confirmed": True,
            },
            idempotency_key=idempotency_key or f"shot-order-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"scene-shot-order:{scene_model.id}",
                "expected_version": payload.expected_version,
                "shot_ids": payload.shot_ids,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
