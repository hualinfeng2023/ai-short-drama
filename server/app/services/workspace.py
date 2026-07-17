import json

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    Character,
    CharacterCandidate,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    Episode,
    Job,
    Project,
    Scene,
    Shot,
    Take,
)
from app.schemas import (
    EpisodeRead,
    IdentityReviewRecord,
    JobRead,
    ProjectRead,
    ProjectSummary,
    SceneRead,
    ShotCharacterBindingRead,
    ShotRead,
    WorkspaceRead,
)


def not_found(entity: str, entity_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "NOT_FOUND",
            "message": f"{entity} 不存在",
            "details": {"id": entity_id},
        },
    )


def project_or_404(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise not_found("项目", project_id)
    return project


def episode_or_404(session: Session, episode_id: str) -> Episode:
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise not_found("单集", episode_id)
    return episode


def scene_or_404(session: Session, scene_id: str) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise not_found("场景", scene_id)
    return scene


def shot_or_404(session: Session, shot_id: str) -> Shot:
    shot = session.get(Shot, shot_id)
    if shot is None:
        raise not_found("镜头", shot_id)
    return shot


def _image_model_for_take(session: Session, shot_id: str, take_version: int | None) -> str | None:
    if take_version is None:
        return None
    jobs = session.scalars(
        select(Job)
        .where(Job.job_type == "GENERATE_SHOT_IMAGE", Job.entity_id == shot_id)
        .order_by(Job.created_at.desc())
    ).all()
    for job in jobs:
        for raw_payload in (job.output_json, job.input_json):
            if not raw_payload:
                continue
            payload = json.loads(raw_payload)
            if int(payload.get("take_version", -1)) != take_version:
                continue
            model = payload.get("model")
            if isinstance(model, str) and model:
                return model
    return None


def _identity_review_record(take: Take | None) -> IdentityReviewRecord | None:
    if take is None or not take.identity_review_decision or not take.identity_review_actor:
        return None
    try:
        issues = json.loads(take.identity_review_issues_json or "[]")
        reference_asset_ids = json.loads(take.identity_reference_asset_ids_json or "[]")
    except json.JSONDecodeError:
        issues, reference_asset_ids = [], []
    return IdentityReviewRecord(
        decision=take.identity_review_decision,
        issues=[item for item in issues if isinstance(item, str)],
        note=take.identity_review_note,
        actor=take.identity_review_actor,
        reviewed_at=take.identity_reviewed_at or take.created_at,
        score=take.identity_score,
        reference_asset_ids=[item for item in reference_asset_ids if isinstance(item, str)],
        look_version=take.identity_review_look_version,
    )


def shot_to_read(session: Session, shot: Shot) -> ShotRead:
    takes = session.scalars(select(Take).where(Take.shot_id == shot.id)).all()
    asset_ids = [take.asset_id for take in takes]
    assets = (
        {asset.id: asset for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))}
        if asset_ids
        else {}
    )
    current = next((take for take in takes if take.id == shot.current_take_id), None)
    candidate = next(
        (
            take
            for take in takes
            if take.version == shot.candidate_take and take.kind != "VIDEO" and not take.is_current
        ),
        None,
    )
    current_video = next(
        (take for take in takes if take.kind == "VIDEO" and take.version == shot.current_take),
        None,
    )
    candidate_video = next(
        (take for take in takes if take.kind == "VIDEO" and take.version == shot.candidate_take),
        None,
    )
    latest_reviewed = max(
        (take for take in takes if take.identity_review_decision),
        key=lambda take: take.identity_reviewed_at or take.created_at,
        default=None,
    )
    current_asset = assets.get(current.asset_id) if current else None
    candidate_asset = assets.get(candidate.asset_id) if candidate else None
    current_video_asset = assets.get(current_video.asset_id) if current_video else None
    candidate_video_asset = assets.get(candidate_video.asset_id) if candidate_video else None
    try:
        stored_character_ids = json.loads(shot.character_ids_json or "[]")
    except json.JSONDecodeError:
        stored_character_ids = []
    character_ids = (
        [item for item in stored_character_ids if isinstance(item, str)]
        if isinstance(stored_character_ids, list)
        else []
    )

    def version_ids(raw: str) -> list[str]:
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return (
            [item for item in values if isinstance(item, str)] if isinstance(values, list) else []
        )

    identity_ids = version_ids(shot.character_identity_version_ids_json)
    look_ids = version_ids(shot.character_look_version_ids_json)
    story_state_ids = version_ids(shot.character_story_state_version_ids_json)
    characters = (
        list(session.scalars(select(Character).where(Character.id.in_(character_ids))).all())
        if character_ids
        else []
    )
    character_by_id = {character.id: character for character in characters}
    identities = (
        {
            item.character_id: item
            for item in session.scalars(
                select(CharacterIdentityVersion).where(
                    CharacterIdentityVersion.id.in_(identity_ids)
                )
            ).all()
        }
        if identity_ids
        else {}
    )
    looks = (
        {
            item.character_id: item
            for item in session.scalars(
                select(CharacterLookVersion).where(CharacterLookVersion.id.in_(look_ids))
            ).all()
        }
        if look_ids
        else {}
    )
    story_states = (
        {
            item.character_id: item
            for item in session.scalars(
                select(CharacterStoryStateVersion).where(
                    CharacterStoryStateVersion.id.in_(story_state_ids)
                )
            ).all()
        }
        if story_state_ids
        else {}
    )
    character_bindings: list[ShotCharacterBindingRead] = []
    for character_id in character_ids:
        character = character_by_id.get(character_id)
        if character is None:
            continue
        identity = identities.get(character_id)
        candidate_id = identity.source_candidate_id if identity else character.locked_candidate_id
        if not candidate_id:
            continue
        locked = session.get(CharacterCandidate, candidate_id)
        if locked is None:
            continue
        reference_asset = session.get(Asset, locked.asset_id)
        if reference_asset is None:
            continue
        character_bindings.append(
            ShotCharacterBindingRead(
                id=character.id,
                name=character.name,
                role=character.role,
                visual_brief=character.visual_brief,
                look_version=shot.character_look_version,
                locked_candidate_id=locked.id,
                reference_asset_id=reference_asset.id,
                reference_asset_url=f"/api/v1/assets/{reference_asset.id}/content",
                identity_version_id=identity.id if identity else None,
                look_version_id=looks[character_id].id if character_id in looks else None,
                story_state_version_id=(
                    story_states[character_id].id if character_id in story_states else None
                ),
            )
        )
    return ShotRead.model_validate(shot).model_copy(
        update={
            "character_ids": character_ids,
            "character_identity_version_ids": identity_ids,
            "character_look_version_ids": look_ids,
            "character_story_state_version_ids": story_state_ids,
            "character_bindings": character_bindings,
            "current_image_url": (
                f"/api/v1/assets/{current_asset.id}/content" if current_asset else None
            ),
            "candidate_image_url": (
                f"/api/v1/assets/{candidate_asset.id}/content" if candidate_asset else None
            ),
            "current_image_model": _image_model_for_take(session, shot.id, shot.current_take),
            "candidate_image_model": _image_model_for_take(session, shot.id, shot.candidate_take),
            "current_video_url": (
                f"/api/v1/assets/{current_video_asset.id}/content" if current_video_asset else None
            ),
            "candidate_video_url": (
                f"/api/v1/assets/{candidate_video_asset.id}/content"
                if candidate_video_asset
                else None
            ),
            "current_identity_status": current.identity_status if current else None,
            "candidate_identity_status": candidate.identity_status if candidate else None,
            "candidate_identity_score": candidate.identity_score if candidate else None,
            "candidate_identity_message": candidate.identity_message if candidate else None,
            "current_identity_review": _identity_review_record(current),
            "candidate_identity_review": _identity_review_record(candidate),
            "latest_identity_review": _identity_review_record(latest_reviewed),
        }
    )


def list_projects(session: Session) -> list[ProjectSummary]:
    projects = session.scalars(select(Project).order_by(Project.updated_at.desc())).all()
    result: list[ProjectSummary] = []
    for project in projects:
        episode_count = (
            session.scalar(select(func.count(Episode.id)).where(Episode.project_id == project.id))
            or 0
        )
        scene_count = (
            session.scalar(
                select(func.count(Scene.id))
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project.id)
            )
            or 0
        )
        shot_count = (
            session.scalar(
                select(func.count(Shot.id))
                .join(Scene, Shot.scene_id == Scene.id)
                .join(Episode, Scene.episode_id == Episode.id)
                .where(Episode.project_id == project.id)
            )
            or 0
        )
        result.append(
            ProjectSummary(
                **ProjectRead.model_validate(project).model_dump(),
                episode_count=episode_count,
                scene_count=scene_count,
                shot_count=shot_count,
            )
        )
    return result


def get_workspace(session: Session, project_id: str) -> WorkspaceRead:
    project = project_or_404(session, project_id)
    episode = session.scalar(
        select(Episode).where(Episode.project_id == project_id).order_by(Episode.code)
    )
    if episode is None:
        raise not_found("项目单集", project_id)
    scenes = session.scalars(
        select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.ordinal)
    ).all()
    scene_ids = [scene.id for scene in scenes]
    shots = (
        session.scalars(
            select(Shot).where(Shot.scene_id.in_(scene_ids)).order_by(Shot.ordinal)
        ).all()
        if scene_ids
        else []
    )
    jobs = session.scalars(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    ).all()
    return WorkspaceRead(
        project=ProjectRead.model_validate(project),
        episode=EpisodeRead.model_validate(episode),
        scenes=[SceneRead.model_validate(scene) for scene in scenes],
        shots=[shot_to_read(session, shot) for shot in shots],
        jobs=[JobRead.model_validate(job) for job in jobs],
    )
