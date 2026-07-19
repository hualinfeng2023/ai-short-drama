from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.db.models import Scene, Shot
from app.db.session import get_session
from app.schemas import (
    BriefAvoidancesSuggestionRead,
    BriefAvoidancesSuggestionRequest,
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
)
from app.services.brief_assistant import suggest_brief_avoidances, suggest_brief_requirements
from app.services.project_naming import ProjectNamingError, suggest_project_name
from app.services.project_readiness import get_project_readiness
from app.services.projects import (
    create_project,
    delete_project,
    list_brief_versions,
    update_project,
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
    result = await create_project(session, payload, idempotency_key)
    response.headers["Idempotency-Replayed"] = str(result.idempotency_replayed).lower()
    return success(result)


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
def remove_project(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(delete_project(session, project_id))


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
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(update_project(session, project_id, payload))


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
