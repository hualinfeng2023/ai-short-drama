import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    ChangeSet,
    Character,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    CharacterVisualProfileVersion,
    Episode,
    IdempotencyKey,
    Project,
    Scene,
    ScriptVersion,
    Shot,
    StoryboardVersion,
    Take,
    TimelineItem,
    TimelineVersion,
)
from app.domain.commands import DirectorCommand
from app.schemas import (
    CharacterVisualProfileConfirmRequest,
    CharacterVisualProfileUpdateRequest,
    IdentityReviewRequest,
    RevisionCreateRequest,
    ScriptEpisodeUpdateRequest,
    ScriptLineUpdateRequest,
    ScriptSceneUpdateRequest,
    ShotCharacterBindingUpdate,
)
from app.services.character_visuals import (
    confirm_visual_profile,
    lock_character_identity,
    restore_character_identity,
    update_visual_profile,
)
from app.services.creative_story import approve_script, revise_script
from app.services.events import append_event
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.revisions import approve_timeline, create_revision, rollback_timeline
from app.services.storyboards_v2 import approve_storyboard
from app.services.takes import (
    apply_candidate_take,
    review_candidate_identity,
    set_shot_character_bindings,
)
from app.services.workspace import shot_or_404, shot_to_read

RESULT_ADAPTER = TypeAdapter(dict[str, object])


@dataclass(frozen=True)
class CommandExecution:
    command_id: str
    command_type: str
    status: str
    result: dict[str, object]
    idempotency_replayed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type,
            "status": self.status,
            "result": self.result,
            "idempotency_replayed": self.idempotency_replayed,
        }


@dataclass(frozen=True)
class MutationResult:
    result: dict[str, object]
    entity_type: str
    entity_id: str
    before_hash: str
    after_hash: str


def _command_scope(project_id: str) -> str:
    return f"domain-command:{project_id}"


def _request_hash(
    project_id: str,
    command: DirectorCommand,
    request_fingerprint: str | None,
) -> str:
    if request_fingerprint is not None:
        return content_hash(
            {
                "project_id": project_id,
                "command_id": command.command_id,
                "command_type": command.command_type,
                "actor": command.actor.model_dump(mode="json"),
                "idempotency_key": command.idempotency_key,
                "request_fingerprint": request_fingerprint,
            }
        )
    return content_hash(
        {
            "project_id": project_id,
            "command": command.model_dump(mode="json", exclude={"created_at"}),
        }
    )


def _idempotency_conflict(command: DirectorCommand) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "IDEMPOTENCY_CONFLICT",
            "message": "该幂等键已用于不同的领域命令",
            "user_action": "刷新对象版本，并为新的修改使用新的幂等键",
            "retryable": False,
            "details": {
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
            },
        },
    )


def _replay(
    record: IdempotencyKey,
    *,
    command: DirectorCommand,
    request_hash: str,
) -> CommandExecution:
    if record.request_hash != request_hash:
        raise _idempotency_conflict(command)
    stored = json.loads(record.response_json)
    return CommandExecution(
        command_id=str(stored["command_id"]),
        command_type=str(stored["command_type"]),
        status=str(stored["status"]),
        result=dict(stored["result"]),
        idempotency_replayed=True,
    )


def _script_changes(command: DirectorCommand) -> tuple[str, str, dict[str, object]]:
    scope = command.payload.get("scope")
    entity_id = command.payload.get("entity_id")
    changes = command.payload.get("changes")
    if scope not in {"EPISODE", "SCENE", "LINE"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "剧本修改范围无效"},
        )
    if not isinstance(entity_id, str) or not isinstance(changes, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "剧本修改目标或内容无效"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "剧本命令必须提供项目锁版本",
            },
        )
    if scope == "EPISODE":
        validated = ScriptEpisodeUpdateRequest(
            expected_version=expected_version,
            **changes,
        )
    elif scope == "SCENE":
        validated = ScriptSceneUpdateRequest(
            expected_version=expected_version,
            **changes,
        )
    else:
        validated = ScriptLineUpdateRequest(
            expected_version=expected_version,
            **changes,
        )
    return (
        scope,
        entity_id,
        validated.model_dump(exclude={"expected_version"}, exclude_none=True),
    )


def _execute_script_revision(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    if (
        command.target_object_id != command.target_version_id
        or command.target_version_id != command.expected_version.target_version_id
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "命令目标与预期版本不一致",
            },
        )
    source = session.get(ScriptVersion, command.target_version_id)
    if source is None or source.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标剧本不存在"},
        )
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    expected_project_version = command.expected_version.project_lock_version
    if expected_project_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "剧本命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_project_version:
        raise version_conflict(project, expected_project_version)
    if (
        command.expected_version.target_hash is not None
        and source.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标剧本内容已经变化，请刷新后重试",
            },
        )
    scope, entity_id, changes = _script_changes(command)
    before_hash = source.content_hash
    result = revise_script(
        session,
        script_id=source.id,
        expected_version=expected_project_version,
        scope=scope,
        entity_id=entity_id,
        changes=changes,
        commit=False,
    )
    return MutationResult(
        result=result,
        entity_type="script_version",
        entity_id=str(result["id"]),
        before_hash=before_hash,
        after_hash=str(result["content_hash"]),
    )


def _object_lock_version(command: DirectorCommand) -> int:
    expected_version = command.expected_version.object_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OBJECT_VERSION_REQUIRED",
                "message": "该命令必须提供目标对象锁版本",
            },
        )
    return expected_version


def _validate_target(
    command: DirectorCommand,
    *,
    object_id: str,
    version_id: str,
) -> None:
    if (
        command.target_object_id != object_id
        or command.target_version_id != version_id
        or command.expected_version.target_version_id != version_id
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "命令目标与预期版本不一致",
            },
        )


def _character_profile_state_hash(
    character: Character,
    profile: CharacterVisualProfileVersion,
) -> str:
    return content_hash(
        {
            "character_id": character.id,
            "character_lock_version": character.lock_version,
            "character_status": character.status,
            "current_profile_version_id": character.current_profile_version_id,
            "profile_id": profile.id,
            "profile_content_hash": profile.content_hash,
            "profile_status": profile.status,
            "profile_confirmed_by": profile.confirmed_by,
        }
    )


def _character_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Character, CharacterVisualProfileVersion]:
    character = session.get(Character, command.target_object_id)
    profile = session.get(CharacterVisualProfileVersion, command.target_version_id)
    if (
        character is None
        or character.project_id != project_id
        or profile is None
        or profile.project_id != project_id
        or profile.character_id != character.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标角色版本不存在"},
        )
    _validate_target(
        command,
        object_id=character.id,
        version_id=profile.id,
    )
    if character.current_profile_version_id != profile.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_VERSION_STALE",
                "message": "命令目标不是当前角色视觉版本",
            },
        )
    return character, profile


def _execute_character_profile_update(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    character, profile = _character_target(
        session,
        project_id=project_id,
        command=command,
    )
    expected_version = _object_lock_version(command)
    before_hash = _character_profile_state_hash(character, profile)
    if (
        command.expected_version.target_hash is not None
        and profile.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标角色视觉版本已经变化，请刷新后重试",
            },
        )
    changes = command.payload.get("changes")
    if not isinstance(changes, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "角色视觉修改内容无效"},
        )
    validated = CharacterVisualProfileUpdateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **changes,
    )
    result = update_visual_profile(
        session,
        project_id=project_id,
        character_id=character.id,
        expected_version=expected_version,
        changes=validated.model_dump(
            exclude={"expected_version", "actor"},
            exclude_none=True,
        ),
        actor=command.actor.id,
        commit=False,
    )
    updated_profile = session.get(CharacterVisualProfileVersion, str(result["id"]))
    if updated_profile is None:
        raise RuntimeError("角色视觉版本创建后无法读取")
    return MutationResult(
        result=result,
        entity_type="character_visual_profile_version",
        entity_id=updated_profile.id,
        before_hash=before_hash,
        after_hash=_character_profile_state_hash(character, updated_profile),
    )


def _execute_character_profile_confirmation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    character, profile = _character_target(
        session,
        project_id=project_id,
        command=command,
    )
    expected_version = _object_lock_version(command)
    profile_version_id = command.payload.get("profile_version_id")
    validated = CharacterVisualProfileConfirmRequest(
        expected_version=expected_version,
        profile_version_id=profile_version_id,
        actor=command.actor.id,
    )
    if validated.profile_version_id != profile.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "确认的角色视觉版本与命令目标不一致",
            },
        )
    before_hash = _character_profile_state_hash(character, profile)
    result = confirm_visual_profile(
        session,
        project_id=project_id,
        character_id=character.id,
        profile_version_id=profile.id,
        expected_version=expected_version,
        actor=command.actor.id,
        commit=False,
    )
    return MutationResult(
        result=result,
        entity_type="character_visual_profile_version",
        entity_id=profile.id,
        before_hash=before_hash,
        after_hash=_character_profile_state_hash(character, profile),
    )


def _shot_state_hash(shot: Shot) -> str:
    return content_hash(
        {
            "shot_id": shot.id,
            "lock_version": shot.lock_version,
            "character_ids_json": shot.character_ids_json,
            "character_look_version": shot.character_look_version,
            "character_identity_version_ids_json": shot.character_identity_version_ids_json,
            "character_look_version_ids_json": shot.character_look_version_ids_json,
            "character_story_state_version_ids_json": (
                shot.character_story_state_version_ids_json
            ),
        }
    )


def _execute_shot_character_bindings(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    shot = shot_or_404(session, command.target_object_id)
    _validate_target(command, object_id=shot.id, version_id=shot.id)
    resolved_project = session.scalar(
        select(Project)
        .join(Episode, Episode.project_id == Project.id)
        .join(Scene, Scene.episode_id == Episode.id)
        .where(Scene.id == shot.scene_id)
    )
    if resolved_project is None or resolved_project.id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标镜头不存在"},
        )
    expected_version = _object_lock_version(command)
    before_hash = _shot_state_hash(shot)
    if (
        command.expected_version.target_hash is not None
        and before_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标镜头已经变化，请刷新后重试",
            },
        )
    validated = ShotCharacterBindingUpdate(
        expected_version=expected_version,
        **command.payload,
    )
    updated = set_shot_character_bindings(
        session,
        shot_id=shot.id,
        expected_version=expected_version,
        character_ids=validated.character_ids,
        look_version=validated.look_version,
        commit=False,
    )
    return MutationResult(
        result=shot_to_read(session, updated).model_dump(mode="json"),
        entity_type="shot",
        entity_id=updated.id,
        before_hash=before_hash,
        after_hash=_shot_state_hash(updated),
    )


def _require_explicit_user_confirmation(command: DirectorCommand) -> None:
    if command.actor.type != "USER" or command.payload.get("confirmed") is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "USER_CONFIRMATION_REQUIRED",
                "message": "批准命令必须由用户明确确认",
                "user_action": "展示影响范围与成本后，由用户重新确认批准",
                "retryable": False,
            },
        )


def _script_approval_state_hash(project: Project, script: ScriptVersion) -> str:
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "project_status": project.status,
            "script_id": script.id,
            "script_content_hash": script.content_hash,
            "script_status": script.status,
            "approved_by": script.approved_by,
        }
    )


def _execute_script_approval(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    _validate_target(
        command,
        object_id=command.target_object_id,
        version_id=command.target_object_id,
    )
    script = session.get(ScriptVersion, command.target_version_id)
    if script is None or script.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标剧本不存在"},
        )
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "剧本批准命令必须提供项目锁版本",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and script.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标剧本已经变化，请刷新后重试",
            },
        )
    before_hash = _script_approval_state_hash(project, script)
    script_result, job, _replayed = approve_script(
        session,
        script_id=script.id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result={
            "script": script_result,
            "job": job.model_dump(mode="json"),
        },
        entity_type="script_version",
        entity_id=script.id,
        before_hash=before_hash,
        after_hash=_script_approval_state_hash(project, script),
    )


def _storyboard_approval_state_hash(
    project: Project,
    storyboard: StoryboardVersion,
) -> str:
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "project_status": project.status,
            "storyboard_id": storyboard.id,
            "storyboard_content_hash": storyboard.content_hash,
            "storyboard_status": storyboard.status,
            "approved_by": storyboard.approved_by,
        }
    )


def _execute_storyboard_approval(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    _validate_target(
        command,
        object_id=command.target_object_id,
        version_id=command.target_object_id,
    )
    storyboard = session.get(StoryboardVersion, command.target_version_id)
    if storyboard is None or storyboard.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标分镜不存在"},
        )
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "分镜批准命令必须提供项目锁版本",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and storyboard.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标分镜已经变化，请刷新后重试",
            },
        )
    before_hash = _storyboard_approval_state_hash(project, storyboard)
    storyboard_result, job, _replayed = approve_storyboard(
        session,
        storyboard_id=storyboard.id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result={
            "storyboard": storyboard_result,
            "job": job.model_dump(mode="json"),
        },
        entity_type="storyboard_version",
        entity_id=storyboard.id,
        before_hash=before_hash,
        after_hash=_storyboard_approval_state_hash(project, storyboard),
    )


def _take_state_hash(take: Take) -> str:
    return content_hash(
        {
            "take_id": take.id,
            "shot_id": take.shot_id,
            "version": take.version,
            "asset_id": take.asset_id,
            "status": take.status,
            "approval": take.approval,
            "is_current": take.is_current,
            "identity_status": take.identity_status,
            "identity_review_decision": take.identity_review_decision,
            "identity_review_issues_json": take.identity_review_issues_json,
            "identity_review_note": take.identity_review_note,
            "identity_review_actor": take.identity_review_actor,
        }
    )


def _shot_take_state_hash(session: Session, project: Project, shot: Shot) -> str:
    takes = list(
        session.scalars(
            select(Take).where(Take.shot_id == shot.id).order_by(Take.kind, Take.version)
        ).all()
    )
    return content_hash(
        {
            "project_id": project.id,
            "project_status": project.status,
            "project_timeline_version": project.timeline_version,
            "shot_id": shot.id,
            "shot_status": shot.status,
            "shot_lock_version": shot.lock_version,
            "current_take_id": shot.current_take_id,
            "current_take": shot.current_take,
            "candidate_take": shot.candidate_take,
            "takes": [
                {
                    "id": take.id,
                    "state_hash": _take_state_hash(take),
                }
                for take in takes
            ],
        }
    )


def _shot_take_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Project, Shot, Take]:
    shot = shot_or_404(session, command.target_object_id)
    take = session.get(Take, command.target_version_id)
    if take is None or take.shot_id != shot.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标 Take 不存在"},
        )
    _validate_target(command, object_id=shot.id, version_id=take.id)
    project = session.scalar(
        select(Project)
        .join(Episode, Episode.project_id == Project.id)
        .join(Scene, Scene.episode_id == Episode.id)
        .where(Scene.id == shot.scene_id)
    )
    if project is None or project.id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标镜头不存在"},
        )
    if shot.candidate_take != take.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TAKE_VERSION_STALE",
                "message": "命令目标已经不是当前候选 Take",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and _take_state_hash(take) != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标 Take 已经变化，请刷新后重试",
            },
        )
    return project, shot, take


def _execute_shot_take_review(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, shot, take = _shot_take_target(
        session,
        project_id=project_id,
        command=command,
    )
    expected_version = _object_lock_version(command)
    review_payload = {
        key: value for key, value in command.payload.items() if key != "confirmed"
    }
    validated = IdentityReviewRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **review_payload,
    )
    before_hash = _shot_take_state_hash(session, project, shot)
    updated, job = review_candidate_identity(
        session,
        shot_id=shot.id,
        decision=validated.decision,
        issues=list(validated.issues),
        note=validated.note,
        expected_version=expected_version,
        actor=command.actor.id,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result={
            "action": validated.decision,
            "shot": shot_to_read(session, updated).model_dump(mode="json"),
            "job": job.model_dump(mode="json") if job is not None else None,
        },
        entity_type="take",
        entity_id=take.id,
        before_hash=before_hash,
        after_hash=_shot_take_state_hash(session, project, updated),
    )


def _execute_shot_take_apply(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, shot, take = _shot_take_target(
        session,
        project_id=project_id,
        command=command,
    )
    expected_version = _object_lock_version(command)
    before_hash = _shot_take_state_hash(session, project, shot)
    updated = apply_candidate_take(
        session,
        shot.id,
        expected_version=expected_version,
        commit=False,
    )
    return MutationResult(
        result=shot_to_read(session, updated).model_dump(mode="json"),
        entity_type="take",
        entity_id=take.id,
        before_hash=before_hash,
        after_hash=_shot_take_state_hash(session, project, updated),
    )


def _execute_revision_change_set(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    timeline = session.get(TimelineVersion, command.target_version_id)
    if (
        project is None
        or command.target_object_id != project.id
        or timeline is None
        or timeline.project_id != project.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "变更集基础版本不存在"},
        )
    _validate_target(
        command,
        object_id=project.id,
        version_id=timeline.id,
    )
    if project.current_timeline_version_id != timeline.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TIMELINE_VERSION_STALE",
                "message": "命令目标不是当前 Preview 版本",
            },
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "创建变更集必须提供项目锁版本",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and timeline.baseline_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "基础 Preview 已经变化，请重新分析影响范围",
            },
        )
    validated = RevisionCreateRequest(
        expected_version=expected_version,
        **command.payload,
    )
    before_hash = content_hash(
        {
            "project_lock_version": project.lock_version,
            "timeline_id": timeline.id,
            "timeline_baseline_hash": timeline.baseline_hash,
        }
    )
    change_set, job, _replayed = create_revision(
        session,
        project_id=project.id,
        expected_version=expected_version,
        scope=validated.scope,
        instruction=validated.instruction,
        confirmed=validated.confirmed,
        idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    persisted = session.get(ChangeSet, change_set.id)
    if persisted is None:
        raise RuntimeError("变更集创建后无法读取")
    return MutationResult(
        result={
            "revision": change_set.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
        },
        entity_type="change_set",
        entity_id=persisted.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "change_set_id": persisted.id,
                "base_timeline_id": persisted.base_timeline_id,
                "scope": persisted.scope_json,
                "impact": persisted.impact_json,
                "status": persisted.status,
            }
        ),
    )


def _timeline_state_hash(
    session: Session,
    project: Project,
    timeline: TimelineVersion,
) -> str:
    items = list(
        session.scalars(
            select(TimelineItem)
            .where(TimelineItem.timeline_id == timeline.id)
            .order_by(TimelineItem.ordinal)
        ).all()
    )
    take_ids = [item.take_id for item in items]
    takes = (
        {
            take.id: take
            for take in session.scalars(select(Take).where(Take.id.in_(take_ids))).all()
        }
        if take_ids
        else {}
    )
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "project_status": project.status,
            "current_timeline_version_id": project.current_timeline_version_id,
            "preview_approved": project.preview_approved,
            "timeline_id": timeline.id,
            "timeline_status": timeline.status,
            "timeline_baseline_hash": timeline.baseline_hash,
            "items": [
                {
                    "shot_id": item.shot_id,
                    "take_id": item.take_id,
                    "take_state_hash": (
                        _take_state_hash(takes[item.take_id])
                        if item.take_id in takes
                        else None
                    ),
                }
                for item in items
            ],
        }
    )


def _timeline_command_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Project, TimelineVersion, int]:
    project = session.get(Project, project_id)
    timeline = session.get(TimelineVersion, command.target_version_id)
    if (
        project is None
        or command.target_object_id != project.id
        or timeline is None
        or timeline.project_id != project.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标 Preview 不存在"},
        )
    _validate_target(command, object_id=project.id, version_id=timeline.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "Preview 命令必须提供项目锁版本",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and timeline.baseline_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标 Preview 已经变化，请刷新后重试",
            },
        )
    return project, timeline, expected_version


def _execute_preview_approval(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, timeline, expected_version = _timeline_command_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _timeline_state_hash(session, project, timeline)
    result = approve_timeline(
        session,
        timeline_id=timeline.id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
        record_audit=False,
    )
    return MutationResult(
        result=result.model_dump(mode="json"),
        entity_type="timeline",
        entity_id=timeline.id,
        before_hash=before_hash,
        after_hash=_timeline_state_hash(session, project, timeline),
    )


def _execute_preview_rollback(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, timeline, expected_version = _timeline_command_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _timeline_state_hash(session, project, timeline)
    result = rollback_timeline(
        session,
        timeline_id=timeline.id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
        record_audit=False,
    )
    return MutationResult(
        result=result.model_dump(mode="json"),
        entity_type="timeline",
        entity_id=timeline.id,
        before_hash=before_hash,
        after_hash=_timeline_state_hash(session, project, timeline),
    )


def _character_identity_state_hash(
    session: Session,
    character: Character,
) -> str:
    identities = list(
        session.scalars(
            select(CharacterIdentityVersion)
            .where(CharacterIdentityVersion.character_id == character.id)
            .order_by(CharacterIdentityVersion.version)
        ).all()
    )
    looks = list(
        session.scalars(
            select(CharacterLookVersion)
            .where(CharacterLookVersion.character_id == character.id)
            .order_by(CharacterLookVersion.version)
        ).all()
    )
    states = list(
        session.scalars(
            select(CharacterStoryStateVersion)
            .where(CharacterStoryStateVersion.character_id == character.id)
            .order_by(CharacterStoryStateVersion.version)
        ).all()
    )
    return content_hash(
        {
            "character_id": character.id,
            "lock_version": character.lock_version,
            "status": character.status,
            "locked_candidate_id": character.locked_candidate_id,
            "locked_identity_version_id": character.locked_identity_version_id,
            "active_look_version_id": character.active_look_version_id,
            "active_story_state_version_id": character.active_story_state_version_id,
            "identities": [
                {
                    "id": identity.id,
                    "content_hash": identity.content_hash,
                    "status": identity.status,
                    "locked_by": identity.locked_by,
                }
                for identity in identities
            ],
            "looks": [
                {
                    "id": look.id,
                    "content_hash": look.content_hash,
                    "status": look.status,
                }
                for look in looks
            ],
            "states": [
                {
                    "id": state.id,
                    "content_hash": state.content_hash,
                    "status": state.status,
                }
                for state in states
            ],
        }
    )


def _project_shot_snapshot_hash(session: Session, project_id: str) -> str:
    shot_ids = select(Shot.id).join(Scene).join(Episode).where(Episode.project_id == project_id)
    shots = list(
        session.scalars(
            select(Shot).where(Shot.id.in_(shot_ids)).order_by(Shot.id)
        ).all()
    )
    return content_hash(
        [
            {
                "id": shot.id,
                "lock_version": shot.lock_version,
                "status": shot.status,
                "current_take_id": shot.current_take_id,
                "candidate_take": shot.candidate_take,
                "character_identity_version_ids_json": (
                    shot.character_identity_version_ids_json
                ),
                "character_look_version_ids_json": shot.character_look_version_ids_json,
                "character_story_state_version_ids_json": (
                    shot.character_story_state_version_ids_json
                ),
            }
            for shot in shots
        ]
    )


def _character_identity_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Character, CharacterIdentityVersion, int]:
    character = session.get(Character, command.target_object_id)
    identity = session.get(CharacterIdentityVersion, command.target_version_id)
    if (
        character is None
        or character.project_id != project_id
        or identity is None
        or identity.project_id != project_id
        or identity.character_id != character.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标角色身份不存在"},
        )
    _validate_target(command, object_id=character.id, version_id=identity.id)
    expected_version = _object_lock_version(command)
    if command.payload.get("identity_version_id") != identity.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "角色身份参数与命令目标不一致",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and identity.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标角色身份已经变化，请刷新后重试",
            },
        )
    return character, identity, expected_version


def _execute_character_identity_lock(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, identity, expected_version = _character_identity_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _character_identity_state_hash(session, character)
    shots_before = _project_shot_snapshot_hash(session, project_id)
    result, script_job = lock_character_identity(
        session,
        project_id=project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    if _project_shot_snapshot_hash(session, project_id) != shots_before:
        raise RuntimeError("角色身份锁定意外修改了既有镜头")
    return MutationResult(
        result={
            "identity": result,
            "script_job": (
                script_job.model_dump(mode="json") if script_job is not None else None
            ),
        },
        entity_type="character_identity_version",
        entity_id=identity.id,
        before_hash=before_hash,
        after_hash=_character_identity_state_hash(session, character),
    )


def _execute_character_identity_restore(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, identity, expected_version = _character_identity_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _character_identity_state_hash(session, character)
    shots_before = _project_shot_snapshot_hash(session, project_id)
    result = restore_character_identity(
        session,
        project_id=project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        expected_version=expected_version,
        actor=command.actor.id,
        commit=False,
    )
    if _project_shot_snapshot_hash(session, project_id) != shots_before:
        raise RuntimeError("角色身份恢复意外修改了既有镜头")
    return MutationResult(
        result=result,
        entity_type="character_identity_version",
        entity_id=identity.id,
        before_hash=before_hash,
        after_hash=_character_identity_state_hash(session, character),
    )


def dispatch_domain_command(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
    request_fingerprint: str | None = None,
) -> CommandExecution:
    request_hash = _request_hash(project_id, command, request_fingerprint)
    scope = _command_scope(project_id)
    existing = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == command.idempotency_key,
        )
    )
    if existing is not None:
        return _replay(existing, command=command, request_hash=request_hash)

    try:
        if command.command_type == "REVISE_SCRIPT":
            mutation = _execute_script_revision(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "UPDATE_CHARACTER_VISUAL_PROFILE":
            mutation = _execute_character_profile_update(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CONFIRM_CHARACTER_VISUAL_PROFILE":
            mutation = _execute_character_profile_confirmation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "SET_SHOT_CHARACTER_BINDINGS":
            mutation = _execute_shot_character_bindings(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPROVE_SCRIPT":
            mutation = _execute_script_approval(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPROVE_STORYBOARD":
            mutation = _execute_storyboard_approval(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REVIEW_SHOT_TAKE":
            mutation = _execute_shot_take_review(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPLY_SHOT_TAKE":
            mutation = _execute_shot_take_apply(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_REVISION_CHANGE_SET":
            mutation = _execute_revision_change_set(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPROVE_PREVIEW":
            mutation = _execute_preview_approval(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "ROLLBACK_PREVIEW":
            mutation = _execute_preview_rollback(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "LOCK_CHARACTER_IDENTITY":
            mutation = _execute_character_identity_lock(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "RESTORE_CHARACTER_IDENTITY":
            mutation = _execute_character_identity_restore(
                session,
                project_id=project_id,
                command=command,
            )
        else:  # pragma: no cover - guarded by the command schema
            raise HTTPException(
                status_code=422,
                detail={"code": "COMMAND_TYPE_UNSUPPORTED", "message": "领域命令类型暂不支持"},
            )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "领域命令内容不符合目标对象约束",
                "details": {
                    "issues": exc.errors(
                        include_url=False,
                        include_context=False,
                    )
                },
            },
        ) from exc

    session.add(
        AuditLog(
            id=command.command_id,
            project_id=project_id,
            actor=command.actor.id,
            action=command.command_type,
            entity_type=mutation.entity_type,
            entity_id=mutation.entity_id,
            before_hash=mutation.before_hash,
            after_hash=mutation.after_hash,
            trace_id=command.command_id,
            created_at=datetime.now(UTC),
        )
    )
    append_event(
        session,
        project_id=project_id,
        event_type="domain_command.executed",
        payload={
            "command_id": command.command_id,
            "command_type": command.command_type,
            "target_object_id": command.target_object_id,
            "target_version_id": command.target_version_id,
            "result_entity_type": mutation.entity_type,
            "result_entity_id": mutation.entity_id,
            "actor": command.actor.model_dump(mode="json"),
        },
    )
    execution = CommandExecution(
        command_id=command.command_id,
        command_type=command.command_type,
        status="SUCCEEDED",
        result=RESULT_ADAPTER.dump_python(mutation.result, mode="json"),
        idempotency_replayed=False,
    )
    now = datetime.now(UTC)
    session.add(
        IdempotencyKey(
            id=command.command_id,
            scope=scope,
            key=command.idempotency_key,
            request_hash=request_hash,
            response_json=canonical_json(execution.as_dict()),
            status_code=200,
            resource_id=mutation.entity_id,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == scope,
                IdempotencyKey.key == command.idempotency_key,
            )
        )
        if winner is None:
            raise
        return _replay(winner, command=command, request_hash=request_hash)
    return execution
