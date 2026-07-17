from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Episode,
    Job,
    Project,
    Scene,
    ScriptVersion,
    Shot,
    StoryboardVersion,
    StoryVersion,
    TimelineVersion,
    VisualBibleVersion,
    WorkflowRun,
)
from app.domain.statuses import JobStatus, ProjectStatus
from app.schemas import (
    ProjectReadinessBlockerRead,
    ProjectReadinessRead,
    ProjectStageRead,
)
from app.services.workspace import project_or_404

ACTIVE_JOB_STATUSES = {
    JobStatus.PENDING,
    JobStatus.RETRY_WAIT,
    JobStatus.RUNNING,
    JobStatus.CANCEL_REQUESTED,
}

PIPELINE_COMPLETE_STATUSES: dict[str, set[ProjectStatus]] = {
    "BRIEF": set(ProjectStatus)
    - {
        ProjectStatus.DRAFT,
        ProjectStatus.PROPOSAL_RUNNING,
        ProjectStatus.BLOCKED,
        ProjectStatus.ARCHIVED,
    },
    "STORY": {
        ProjectStatus.STORY_APPROVED,
        ProjectStatus.PREPRODUCTION_READY,
        ProjectStatus.PREPRODUCTION_APPROVED,
        ProjectStatus.STORYBOARD_READY,
        ProjectStatus.STORYBOARD_APPROVED,
        ProjectStatus.CHARACTER_LOCKED,
        ProjectStatus.PRODUCING,
        ProjectStatus.PREVIEW_READY,
        ProjectStatus.APPROVED,
        ProjectStatus.EXPORTING,
        ProjectStatus.EXPORTED,
    },
    "PREPRODUCTION": {
        ProjectStatus.PREPRODUCTION_APPROVED,
        ProjectStatus.STORYBOARD_READY,
        ProjectStatus.STORYBOARD_APPROVED,
        ProjectStatus.CHARACTER_LOCKED,
        ProjectStatus.PRODUCING,
        ProjectStatus.PREVIEW_READY,
        ProjectStatus.APPROVED,
        ProjectStatus.EXPORTING,
        ProjectStatus.EXPORTED,
    },
    "STORYBOARD": {
        ProjectStatus.STORYBOARD_APPROVED,
        ProjectStatus.PRODUCING,
        ProjectStatus.PREVIEW_READY,
        ProjectStatus.APPROVED,
        ProjectStatus.EXPORTING,
        ProjectStatus.EXPORTED,
    },
    "PRODUCTION": {ProjectStatus.APPROVED, ProjectStatus.EXPORTING, ProjectStatus.EXPORTED},
}


def _count(session: Session, model: type[object], *conditions: object) -> int:
    query = select(func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    return int(session.scalar(query) or 0)


def _job_stage(job_type: str) -> str:
    normalized = job_type.upper()
    if any(token in normalized for token in ("PROPOSAL", "STORY", "SCRIPT", "RELATIONSHIP")):
        return "STORY"
    if any(token in normalized for token in ("CHARACTER", "PREPRODUCTION", "VISUAL_BIBLE")):
        return "PREPRODUCTION"
    if any(token in normalized for token in ("STORYBOARD", "ANIMATIC")):
        return "STORYBOARD"
    if any(
        token in normalized
        for token in (
            "SHOT",
            "IMAGE",
            "VIDEO",
            "AUDIO",
            "TIMELINE",
            "PREVIEW",
            "EXPORT",
            "REVISION",
        )
    ):
        return "PRODUCTION"
    return "BRIEF"


def _classic_stages(
    project: Project,
    episode: Episode | None,
    scene: Scene | None,
    shot_count: int,
    approved_shot_count: int,
    timeline: TimelineVersion | None,
    active_jobs: list[Job],
) -> list[ProjectStageRead]:
    project_root = f"/projects/{project.id}"
    episode_href = f"{project_root}/episodes/{episode.id}" if episode else project_root
    shots_href = f"{episode_href}/scenes/{scene.id}" if scene else episode_href
    preview_href = f"{episode_href}/preview" if episode else project_root
    active_production = any(_job_stage(job.job_type) == "PRODUCTION" for job in active_jobs)

    brief_complete = project.status != ProjectStatus.DRAFT
    episode_complete = shot_count > 0
    shots_complete = shot_count > 0 and approved_shot_count == shot_count and timeline is not None
    preview_complete = timeline is not None and timeline.status == "APPROVED"

    raw = [
        ("BRIEF", "项目简报", brief_complete, project_root, "项目目标、受众与制作约束"),
        ("EPISODE", "分集与场景", episode_complete, episode_href, "分集结构、场景和镜头清单"),
        ("SHOTS", "镜头制作", shots_complete, shots_href, "逐镜头生成、复核与版本应用"),
        ("PREVIEW", "完整小样", preview_complete, preview_href, "时间线、小样审批与导出"),
    ]
    first_incomplete = next((index for index, item in enumerate(raw) if not item[2]), len(raw) - 1)
    stages: list[ProjectStageRead] = []
    for index, (key, label, complete, href, detail) in enumerate(raw):
        if complete:
            status = "COMPLETE"
        elif key in {"SHOTS", "PREVIEW"} and active_production:
            status = "IN_PROGRESS"
        elif index == first_incomplete:
            status = "CURRENT"
        else:
            status = "LOCKED"
        stages.append(
            ProjectStageRead(key=key, label=label, status=status, href=href, detail=detail)
        )
    return stages


def _pipeline_stages(project: Project, active_jobs: list[Job]) -> list[ProjectStageRead]:
    project_root = f"/projects/{project.id}"
    raw = [
        ("BRIEF", "项目简报", project_root, "项目目标、受众与制作约束"),
        ("STORY", "故事与剧本", f"{project_root}/story", "故事方向、关系、分集与剧本"),
        (
            "PREPRODUCTION",
            "前期资产",
            f"{project_root}/preproduction",
            "角色、造型、场景、道具与声音",
        ),
        ("STORYBOARD", "动态分镜", f"{project_root}/storyboard", "镜头规格、关键帧与节奏样片"),
        ("PRODUCTION", "正式制作", f"{project_root}/production", "音视频、时间线、质检与交付"),
    ]
    try:
        project_status = ProjectStatus(project.status)
    except ValueError:
        project_status = ProjectStatus.DRAFT
    complete = [project_status in PIPELINE_COMPLETE_STATUSES[key] for key, *_ in raw]
    first_incomplete = next(
        (index for index, value in enumerate(complete) if not value), len(raw) - 1
    )
    active_stage_keys = {_job_stage(job.job_type) for job in active_jobs}
    stages: list[ProjectStageRead] = []
    for index, (key, label, href, detail) in enumerate(raw):
        if complete[index]:
            status = "COMPLETE"
        elif key in active_stage_keys:
            status = "IN_PROGRESS"
        elif index == first_incomplete:
            status = "CURRENT"
        else:
            status = "LOCKED"
        stages.append(
            ProjectStageRead(key=key, label=label, status=status, href=href, detail=detail)
        )
    return stages


def get_project_readiness(session: Session, project_id: str) -> ProjectReadinessRead:
    project = project_or_404(session, project_id)
    episode = session.scalar(
        select(Episode)
        .where(Episode.project_id == project_id)
        .order_by(Episode.ordinal, Episode.code)
    )
    scene = (
        session.scalar(select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.ordinal))
        if episode
        else None
    )
    shot_count = (
        _count(
            session,
            Shot,
            Shot.scene_id.in_(
                select(Scene.id)
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project_id)
            ),
        )
        if episode
        else 0
    )
    approved_shot_count = (
        _count(
            session,
            Shot,
            Shot.status == "APPROVED",
            Shot.scene_id.in_(
                select(Scene.id)
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project_id)
            ),
        )
        if episode
        else 0
    )
    timeline = session.scalar(
        select(TimelineVersion)
        .where(TimelineVersion.project_id == project_id)
        .order_by(TimelineVersion.version.desc())
    )
    jobs = session.scalars(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    ).all()
    active_jobs = [job for job in jobs if job.status in ACTIVE_JOB_STATUSES]

    v2_artifact_count = sum(
        _count(session, model, model.project_id == project_id)
        for model in (
            StoryVersion,
            ScriptVersion,
            VisualBibleVersion,
            StoryboardVersion,
            WorkflowRun,
        )
    )
    has_classic_workspace = episode is not None and shot_count > 0
    if v2_artifact_count and has_classic_workspace:
        workflow_mode = "HYBRID"
    elif v2_artifact_count or not has_classic_workspace:
        workflow_mode = "PIPELINE"
    else:
        workflow_mode = "CLASSIC"

    stages = (
        _classic_stages(
            project,
            episode,
            scene,
            shot_count,
            approved_shot_count,
            timeline,
            active_jobs,
        )
        if workflow_mode == "CLASSIC"
        else _pipeline_stages(project, active_jobs)
    )
    active_stage = next(
        (stage for stage in stages if stage.status in {"IN_PROGRESS", "CURRENT", "BLOCKED"}),
        stages[-1],
    )
    blockers: list[ProjectReadinessBlockerRead] = []
    if project.status == ProjectStatus.BLOCKED:
        failed_job = next((job for job in jobs if job.status == JobStatus.FAILED), None)
        blocker_message = "项目被阻断，请查看失败任务并按提示恢复。"
        blockers.append(
            ProjectReadinessBlockerRead(
                code=(failed_job.error_code or "PROJECT_BLOCKED")
                if failed_job
                else "PROJECT_BLOCKED",
                message=(failed_job.error_message or blocker_message)
                if failed_job
                else blocker_message,
                action_label="查看失败任务",
                action_href=f"/tasks?project={project_id}",
            )
        )
        active_stage.status = "BLOCKED"

    if blockers:
        summary_status = "BLOCKED"
    elif active_jobs:
        summary_status = "IN_PROGRESS"
    elif all(stage.status == "COMPLETE" for stage in stages):
        summary_status = "READY"
    else:
        summary_status = "ACTION_REQUIRED"

    return ProjectReadinessRead(
        project_id=project_id,
        workflow_mode=workflow_mode,
        project_status=project.status,
        summary_status=summary_status,
        active_stage_key=active_stage.key,
        active_job_count=len(active_jobs),
        stages=stages,
        blockers=blockers,
        next_action_label=("查看进行中的任务" if active_jobs else f"继续{active_stage.label}"),
        next_action_href=(f"/tasks?project={project_id}" if active_jobs else active_stage.href),
        updated_at=project.updated_at,
    )
