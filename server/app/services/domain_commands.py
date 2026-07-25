import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Asset,
    AuditLog,
    ChangeSet,
    Character,
    CharacterCandidate,
    CharacterIdentityVersion,
    CharacterLookVersion,
    CharacterStoryStateVersion,
    CharacterVisualProfileVersion,
    Episode,
    GenerationRecord,
    IdempotencyKey,
    Job,
    Project,
    Scene,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryboardVersion,
    Take,
    TimelineClip,
    TimelineItem,
    TimelineVersion,
)
from app.domain.commands import DirectorCommand
from app.domain.director import DirectorReviewOutput
from app.schemas import (
    CharacterCandidateDeleteRequest,
    CharacterCandidateGenerateRequest,
    CharacterCandidateSelectRequest,
    CharacterChangeApplyRequest,
    CharacterIdentityViewGenerateRequest,
    CharacterVisualProfileConfirmRequest,
    CharacterVisualProfileUpdateRequest,
    IdentityReviewRequest,
    ReferenceAssetUploadCommandPayload,
    RevisionCreateRequest,
    ScriptEpisodeUpdateRequest,
    ScriptLineUpdateRequest,
    ScriptSceneUpdateRequest,
    ShotCharacterBindingUpdate,
    ShotImageGenerateRequest,
    ShotVideoGenerateRequest,
    StoryboardShotRegenerateRequest,
)
from app.services.assets import (
    asset_to_read,
    cleanup_unreferenced_asset_files,
    delete_reference_asset,
    sha256_file,
)
from app.services.character_visuals import (
    apply_character_change,
    confirm_visual_profile,
    delete_character_candidate,
    generate_character_candidates,
    generate_character_identity_view,
    lock_character_identity,
    restore_character_identity,
    select_character_candidate,
    update_visual_profile,
)
from app.services.creative_story import approve_script, revise_script
from app.services.director_proposals import director_proposal_to_read
from app.services.events import append_event
from app.services.projects import canonical_json, content_hash, version_conflict
from app.services.revisions import approve_timeline, create_revision, rollback_timeline
from app.services.storyboards_v2 import approve_storyboard, regenerate_storyboard_shot
from app.services.takes import (
    apply_candidate_take,
    create_shot_image_job,
    review_candidate_identity,
    set_shot_character_bindings,
)
from app.services.uploads import upload_rule, validate_and_register_upload
from app.services.videos import create_shot_video_job
from app.services.workspace import shot_or_404, shot_to_read

RESULT_ADAPTER = TypeAdapter(dict[str, object])
logger = logging.getLogger(__name__)


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
    post_commit: Callable[[], None] | None = None
    rollback_action: Callable[[], None] | None = None


def _run_post_commit_action(mutation: MutationResult) -> None:
    if mutation.post_commit is None:
        return
    try:
        mutation.post_commit()
    except OSError:
        logger.exception(
            "领域命令已提交，但提交后清理失败：entity_type=%s entity_id=%s",
            mutation.entity_type,
            mutation.entity_id,
        )


def _run_rollback_action(mutation: MutationResult) -> None:
    if mutation.rollback_action is None:
        return
    try:
        mutation.rollback_action()
    except Exception:
        logger.exception(
            "领域命令已回滚，但回滚清理失败：entity_type=%s entity_id=%s",
            mutation.entity_type,
            mutation.entity_id,
        )


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


def _character_candidate_generation_state_hash(
    session: Session,
    character: Character,
    profile: CharacterVisualProfileVersion,
) -> str:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.project_id == character.project_id,
                Job.entity_id == character.id,
                Job.job_type == "GENERATE_CHARACTER_VISUAL_CANDIDATE",
            )
            .order_by(Job.created_at, Job.id)
        ).all()
    )
    return content_hash(
        {
            "profile_state": _character_profile_state_hash(character, profile),
            "jobs": [
                {
                    "id": job.id,
                    "status": job.status,
                    "request_hash": job.request_hash,
                    "input_json": job.input_json,
                }
                for job in jobs
            ],
        }
    )


def _execute_character_candidate_generation_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, profile = _character_target(
        session,
        project_id=project_id,
        command=command,
    )
    expected_version = _object_lock_version(command)
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
    validated = CharacterCandidateGenerateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    if validated.profile_version_id != profile.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "候选生成使用的视觉版本与命令目标不一致",
            },
        )
    before_hash = _character_candidate_generation_state_hash(session, character, profile)
    batch, jobs = generate_character_candidates(
        session,
        project_id=project_id,
        character_id=character.id,
        profile_version_id=profile.id,
        expected_version=expected_version,
        count=validated.count,
        source_candidate_id=validated.source_candidate_id,
        refinement_note=validated.refinement_note,
        custom_prompt=validated.custom_prompt,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result={
            "batch": batch,
            "jobs": [job.model_dump(mode="json") for job in jobs],
        },
        entity_type="character_candidate_batch",
        entity_id=str(batch["id"]),
        before_hash=before_hash,
        after_hash=_character_candidate_generation_state_hash(session, character, profile),
    )


def _character_candidate_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Character, CharacterCandidate, int]:
    character = session.get(Character, command.target_object_id)
    candidate = session.get(CharacterCandidate, command.target_version_id)
    if (
        character is None
        or character.project_id != project_id
        or candidate is None
        or candidate.project_id != project_id
        or candidate.character_id != character.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标角色候选不存在"},
        )
    _validate_target(command, object_id=character.id, version_id=candidate.id)
    expected_version = _object_lock_version(command)
    if command.payload.get("candidate_id") != candidate.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "选定的角色候选与命令目标不一致",
            },
        )
    return character, candidate, expected_version


def _character_candidate_selection_state_hash(
    session: Session,
    character: Character,
    candidate: CharacterCandidate,
) -> str:
    return content_hash(
        {
            "identity_state": _character_identity_state_hash(session, character),
            "candidate": {
                "id": candidate.id,
                "profile_version_id": candidate.profile_version_id,
                "status": candidate.status,
                "review_status": candidate.review_status,
                "selected": candidate.selected,
                "asset_id": candidate.asset_id,
            },
        }
    )


def _execute_character_candidate_selection(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, candidate, expected_version = _character_candidate_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _character_candidate_selection_state_hash(session, character, candidate)
    if (
        command.expected_version.target_hash is not None
        and before_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标角色候选已经变化，请刷新后重试",
            },
        )
    validated = CharacterCandidateSelectRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    shots_before = _project_shot_snapshot_hash(session, project_id)
    identity, jobs = select_character_candidate(
        session,
        project_id=project_id,
        character_id=character.id,
        candidate_id=validated.candidate_id,
        expected_version=expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    if _project_shot_snapshot_hash(session, project_id) != shots_before:
        raise RuntimeError("角色候选选定意外修改了既有镜头")
    return MutationResult(
        result={
            "identity": identity,
            "jobs": [job.model_dump(mode="json") for job in jobs],
        },
        entity_type="character_identity_version",
        entity_id=str(identity["id"]),
        before_hash=before_hash,
        after_hash=_character_candidate_selection_state_hash(session, character, candidate),
    )


def _character_candidate_deletion_state_hash(
    session: Session,
    character: Character,
    candidate_id: str,
) -> str:
    candidate = session.get(CharacterCandidate, candidate_id)
    asset = session.get(Asset, candidate.asset_id) if candidate is not None else None
    return content_hash(
        {
            "character_id": character.id,
            "character_lock_version": character.lock_version,
            "character_status": character.status,
            "candidate": (
                {
                    "id": candidate.id,
                    "profile_version_id": candidate.profile_version_id,
                    "status": candidate.status,
                    "review_status": candidate.review_status,
                    "selected": candidate.selected,
                    "asset_id": candidate.asset_id,
                }
                if candidate is not None
                else None
            ),
            "asset_id": asset.id if asset is not None else None,
        }
    )


def _delete_local_asset(path: Path) -> None:
    path.unlink(missing_ok=True)


def _execute_character_candidate_deletion(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, candidate, expected_version = _character_candidate_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _character_candidate_deletion_state_hash(
        session,
        character,
        candidate.id,
    )
    if (
        command.expected_version.target_hash is not None
        and before_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标角色候选已经变化，请刷新后重试",
            },
        )
    validated = CharacterCandidateDeleteRequest(
        expected_version=expected_version,
        actor=command.actor.id,
    )
    result, asset_path = delete_character_candidate(
        session,
        get_settings(),
        project_id=project_id,
        character_id=character.id,
        candidate_id=candidate.id,
        expected_version=validated.expected_version,
        actor=validated.actor,
        commit=False,
    )
    return MutationResult(
        result=result,
        entity_type="character_candidate",
        entity_id=candidate.id,
        before_hash=before_hash,
        after_hash=_character_candidate_deletion_state_hash(
            session,
            character,
            candidate.id,
        ),
        post_commit=(
            (lambda path=asset_path: _delete_local_asset(path))
            if asset_path is not None
            else None
        ),
    )


def _reference_asset_state_hash(
    project: Project,
    asset: Asset | None,
    asset_id: str,
) -> str:
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "asset_id": asset_id,
            "asset": (
                {
                    "kind": asset.kind,
                    "storage_key": asset.storage_key,
                    "sha256": asset.sha256,
                    "status": asset.status,
                    "rights_status": asset.rights_status,
                    "source_entity_type": asset.source_entity_type,
                    "source_entity_id": asset.source_entity_id,
                }
                if asset is not None
                else None
            ),
        }
    )


def _reference_assets_state_hash(session: Session, project: Project) -> str:
    assets = list(
        session.scalars(
            select(Asset)
            .where(
                Asset.project_id == project.id,
                Asset.kind.like("REFERENCE_%"),
            )
            .order_by(Asset.id)
        ).all()
    )
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "assets": [
                {
                    "id": asset.id,
                    "kind": asset.kind,
                    "sha256": asset.sha256,
                    "storage_key": asset.storage_key,
                    "status": asset.status,
                    "rights_status": asset.rights_status,
                }
                for asset in assets
            ],
        }
    )


def _execute_reference_asset_upload(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    _validate_target(
        command,
        object_id=project.id,
        version_id=command.target_version_id,
    )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "素材上传命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    validated = ReferenceAssetUploadCommandPayload(
        **{key: value for key, value in command.payload.items() if key != "confirmed"}
    )
    stage_token = str(validated.stage_token)
    if stage_token != command.target_version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMAND_TARGET_VERSION_MISMATCH",
                "message": "上传暂存令牌与命令目标不一致",
            },
        )
    extension, _limit, _mime, _kind = upload_rule(validated.filename)
    settings = get_settings()
    upload_root = (settings.data_dir / "tmp" / "uploads").resolve()
    staged_path = (upload_root / f"{stage_token}{extension}").resolve()
    if not staged_path.is_relative_to(upload_root) or not staged_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "STAGED_UPLOAD_NOT_FOUND",
                "message": "上传暂存文件不存在或已经失效",
            },
        )
    staged_hash = sha256_file(staged_path)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != staged_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "上传暂存文件内容已经变化，请重新上传",
            },
        )
    before_hash = _reference_assets_state_hash(session, project)
    asset, created_files = validate_and_register_upload(
        session,
        settings,
        project_id=project.id,
        source=staged_path,
        filename=validated.filename,
        declared_content_type=validated.declared_content_type,
        commit=False,
    )
    return MutationResult(
        result=asset_to_read(asset).model_dump(mode="json"),
        entity_type="asset",
        entity_id=asset.id,
        before_hash=before_hash,
        after_hash=_reference_assets_state_hash(session, project),
        rollback_action=(
            lambda paths=created_files: cleanup_unreferenced_asset_files(
                session,
                settings,
                paths,
            )
        ),
    )


def _execute_reference_asset_deletion(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    asset = session.get(Asset, command.target_object_id)
    if project is None or asset is None or asset.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标素材不存在"},
        )
    _validate_target(command, object_id=asset.id, version_id=asset.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "素材删除命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    before_hash = _reference_asset_state_hash(project, asset, asset.id)
    if (
        command.expected_version.target_hash is not None
        and before_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标素材已经变化，请刷新后重试",
            },
        )
    result, cleanup_path = delete_reference_asset(
        session,
        get_settings(),
        asset_id=asset.id,
        commit=False,
    )
    return MutationResult(
        result=result,
        entity_type="asset",
        entity_id=asset.id,
        before_hash=before_hash,
        after_hash=_reference_asset_state_hash(project, None, asset.id),
        post_commit=(
            (lambda path=cleanup_path: _delete_local_asset(path))
            if cleanup_path is not None
            else None
        ),
    )


def _character_change_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Character, int]:
    character = session.get(Character, command.target_object_id)
    if character is None or character.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标角色不存在"},
        )
    _validate_target(command, object_id=character.id, version_id=character.id)
    return character, _object_lock_version(command)


def _execute_character_change(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    character, expected_version = _character_change_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _character_identity_state_hash(session, character)
    if (
        command.expected_version.target_hash is not None
        and before_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标角色已经变化，请刷新后重试",
            },
        )
    validated = CharacterChangeApplyRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    shots_before = _project_shot_snapshot_hash(session, project_id)
    result = apply_character_change(
        session,
        project_id=project_id,
        character_id=character.id,
        expected_version=expected_version,
        change_type=validated.change_type,
        payload=validated.payload,
        decision=validated.decision,
        actor=command.actor.id,
        commit=False,
    )
    if _project_shot_snapshot_hash(session, project_id) != shots_before:
        raise RuntimeError("角色变更意外修改了既有镜头")
    return MutationResult(
        result=result,
        entity_type="character",
        entity_id=character.id,
        before_hash=before_hash,
        after_hash=_character_identity_state_hash(session, character),
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
            "character_story_state_version_ids_json": (shot.character_story_state_version_ids_json),
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


def _storyboard_regeneration_state_hash(
    session: Session,
    project: Project,
    storyboard: StoryboardVersion,
    spec: ShotSpec,
    shot: Shot,
) -> str:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.entity_id == spec.id,
                Job.job_type == "GENERATE_STORYBOARD_TAKE",
            )
            .order_by(Job.created_at, Job.id)
        ).all()
    )
    return content_hash(
        {
            "project_lock_version": project.lock_version,
            "project_status": project.status,
            "storyboard_id": storyboard.id,
            "storyboard_status": storyboard.status,
            "storyboard_content_hash": storyboard.content_hash,
            "animatic_asset_id": storyboard.animatic_asset_id,
            "shot_spec_id": spec.id,
            "shot_spec_status": spec.status,
            "shot_id": shot.id,
            "shot_status": shot.status,
            "shot_lock_version": shot.lock_version,
            "jobs": [
                {
                    "id": job.id,
                    "status": job.status,
                    "request_hash": job.request_hash,
                    "input_json": job.input_json,
                }
                for job in jobs
            ],
        }
    )


def _execute_storyboard_shot_regeneration_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    spec = session.get(ShotSpec, command.target_object_id)
    storyboard = session.get(StoryboardVersion, command.target_version_id)
    shot = session.get(Shot, spec.shot_id) if spec is not None else None
    project = session.get(Project, project_id)
    if (
        spec is None
        or storyboard is None
        or spec.storyboard_version_id != storyboard.id
        or storyboard.project_id != project_id
        or shot is None
        or project is None
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标分镜镜头不存在"},
        )
    _validate_target(command, object_id=spec.id, version_id=storyboard.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "分镜重生成命令必须提供项目锁版本",
            },
        )
    if (
        command.expected_version.target_hash is not None
        and storyboard.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_CONTENT_CHANGED", "message": "目标分镜版本已经变化"},
        )
    validated = StoryboardShotRegenerateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    before_hash = _storyboard_regeneration_state_hash(
        session, project, storyboard, spec, shot
    )
    shot_result, job, _ = regenerate_storyboard_shot(
        session,
        shot_spec_id=spec.id,
        expected_version=expected_version,
        actor=validated.actor,
        trace_id=command.command_id,
        note=validated.note,
        commit=False,
    )
    return MutationResult(
        result={"shot": shot_result, "job": job.model_dump(mode="json")},
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_storyboard_regeneration_state_hash(
            session, project, storyboard, spec, shot
        ),
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


def _shot_generation_state_hash(session: Session, project: Project, shot: Shot) -> str:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.entity_type == "shot",
                Job.entity_id == shot.id,
                Job.job_type.in_(["GENERATE_SHOT_IMAGE", "GENERATE_SHOT_VIDEO"]),
            )
            .order_by(Job.created_at, Job.id)
        ).all()
    )
    return content_hash(
        {
            "shot_state": _shot_take_state_hash(session, project, shot),
            "generation_jobs": [
                {
                    "id": job.id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "request_hash": job.request_hash,
                    "input_json": job.input_json,
                }
                for job in jobs
            ],
        }
    )


def _shot_generation_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Project, Shot]:
    shot = shot_or_404(session, command.target_object_id)
    _validate_target(command, object_id=shot.id, version_id=shot.id)
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
    expected_version = _object_lock_version(command)
    if shot.lock_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "镜头版本已经变化，请刷新后重试",
                "details": {
                    "expected_version": expected_version,
                    "actual_version": shot.lock_version,
                },
            },
        )
    if (
        command.expected_version.target_hash is not None
        and _shot_generation_state_hash(session, project, shot)
        != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_CONTENT_CHANGED", "message": "镜头生成上下文已经变化"},
        )
    return project, shot


def _execute_shot_image_generation_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, shot = _shot_generation_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _shot_generation_state_hash(session, project, shot)
    validated = ShotImageGenerateRequest(
        **{key: value for key, value in command.payload.items() if key != "confirmed"}
    )
    job, _ = create_shot_image_job(
        session,
        shot_id=shot.id,
        prompt=validated.prompt,
        model=validated.model,
        resolution=validated.resolution,
        aspect_ratio=validated.aspect_ratio,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_shot_generation_state_hash(session, project, shot),
    )


def _execute_shot_video_generation_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, shot = _shot_generation_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _shot_generation_state_hash(session, project, shot)
    validated = ShotVideoGenerateRequest(
        **{key: value for key, value in command.payload.items() if key != "confirmed"}
    )
    job, _ = create_shot_video_job(
        session,
        shot_id=shot.id,
        payload=validated,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_shot_generation_state_hash(session, project, shot),
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
    review_payload = {key: value for key, value in command.payload.items() if key != "confirmed"}
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
        {take.id: take for take in session.scalars(select(Take).where(Take.id.in_(take_ids))).all()}
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
                        _take_state_hash(takes[item.take_id]) if item.take_id in takes else None
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
    profile = session.get(
        CharacterVisualProfileVersion,
        character.current_profile_version_id,
    )
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
            "current_profile_version_id": character.current_profile_version_id,
            "current_profile_content_hash": profile.content_hash if profile is not None else None,
            "current_profile_status": profile.status if profile is not None else None,
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
    shots = list(session.scalars(select(Shot).where(Shot.id.in_(shot_ids)).order_by(Shot.id)).all())
    return content_hash(
        [
            {
                "id": shot.id,
                "lock_version": shot.lock_version,
                "status": shot.status,
                "current_take_id": shot.current_take_id,
                "candidate_take": shot.candidate_take,
                "character_identity_version_ids_json": (shot.character_identity_version_ids_json),
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
            "script_job": (script_job.model_dump(mode="json") if script_job is not None else None),
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


def _execute_character_identity_view_generation_request(
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
    validated = CharacterIdentityViewGenerateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{
            key: value
            for key, value in command.payload.items()
            if key not in {"confirmed", "identity_version_id"}
        },
    )
    before_hash = _character_identity_state_hash(session, character)
    job = generate_character_identity_view(
        session,
        project_id=project_id,
        character_id=character.id,
        identity_version_id=identity.id,
        view_type=validated.view_type,
        expected_version=expected_version,
        refinement_note=validated.refinement_note,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result={"job": job.model_dump(mode="json")},
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_character_identity_state_hash(session, character),
    )


def _director_proposal_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Project, ChangeSet, dict[str, object]]:
    project = session.get(Project, project_id)
    change_set = session.get(ChangeSet, command.target_object_id)
    if project is None or change_set is None or change_set.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DIRECTOR_PROPOSAL_NOT_FOUND",
                "message": "Director Proposal 不存在",
            },
        )
    impact = json.loads(change_set.impact_json)
    if "proposal" not in impact:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DIRECTOR_PROPOSAL_NOT_FOUND",
                "message": "该 ChangeSet 不是 Director Proposal",
            },
        )
    return project, change_set, impact


def _execute_create_director_proposal(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    project = session.get(Project, project_id)
    script = session.get(ScriptVersion, command.target_version_id)
    scene = session.get(ScriptScene, command.target_object_id)
    if (
        project is None
        or script is None
        or script.project_id != project.id
        or scene is None
        or scene.script_version_id != script.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "Director 审查目标不存在"},
        )
    _validate_target(command, object_id=scene.id, version_id=script.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None or project.lock_version != expected_version:
        raise version_conflict(project, expected_version or 0)
    if (
        command.expected_version.target_hash is not None
        and script.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标剧本已变化，请重新发起 Director 审查",
            },
        )
    payload = command.payload
    review_payload = payload.get("review")
    context = payload.get("context")
    impact_payload = payload.get("impact")
    provider = payload.get("provider")
    if not all(
        isinstance(value, dict) for value in (review_payload, context, impact_payload, provider)
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "Director Proposal 内容无效"},
        )
    review = DirectorReviewOutput.model_validate(review_payload)
    proposal = {
        "issue_type": review.issue_type,
        "observation": review.observation,
        "rationale": review.rationale,
        "target_objects": [{"type": "ScriptScene", "id": scene.id, "version_id": script.id}],
        "proposed_changes": [
            item.proposed_change.model_dump(mode="json") for item in review.options
        ],
        "alternatives": [item.model_dump(mode="json") for item in review.options],
        "recommended_option": review.recommended_option_id,
        "confidence": review.confidence,
        "affected_objects": impact_payload.get("affected_objects", []),
        "preserved_objects": impact_payload.get("preserved_objects", []),
        "estimated_time_seconds": sum(item.estimated_time_seconds for item in review.options),
        "estimated_cost_usd": max(item.estimated_cost_usd for item in review.options),
        "requires_confirmation": True,
        "validation_plan": review.validation_plan,
        "base_script_version_id": script.id,
        "script_scene_id": scene.id,
        "scene_ordinal": scene.ordinal,
        "requested_by": payload.get("requested_by"),
        "provider": provider,
    }
    stored_impact: dict[str, object] = {
        "proposal": proposal,
        "impact": impact_payload,
        "context": context,
    }
    change_set = ChangeSet(
        id=command.command_id,
        project_id=project.id,
        base_timeline_id=project.current_timeline_version_id,
        base_relationship_graph_id=script.relationship_graph_version_id,
        scope_json=canonical_json(
            {
                "type": "SCRIPT_SCENE",
                "ids": [scene.id],
                "script_version_id": script.id,
            }
        ),
        instruction=str(payload.get("instruction") or "Director 场景审查"),
        impact_json=canonical_json(stored_impact),
        estimate_json=canonical_json(
            {
                "estimated_seconds": proposal["estimated_time_seconds"],
                "estimated_cost_usd": proposal["estimated_cost_usd"],
                "media_generation": False,
            }
        ),
        status="PROPOSED",
        result_timeline_id=None,
        result_relationship_graph_id=None,
        created_at=datetime.now(UTC),
    )
    session.add(change_set)
    session.add(
        GenerationRecord(
            id=str(uuid4()),
            project_id=project.id,
            job_id=None,
            entity_type="director_proposal",
            entity_id=change_set.id,
            capability="DIRECTOR_SCENE_REVIEW",
            provider=str(provider.get("provider", "unknown")),
            model=str(provider.get("model", "unknown")),
            config_version="director-proposal-v1",
            prompt_hash=content_hash(context),
            seed=None,
            reference_asset_ids_json="[]",
            provider_request_id=(
                str(provider["request_id"]) if provider.get("request_id") else None
            ),
            provider_task_id=None,
            status="SUCCEEDED",
            latency_ms=None,
            input_units=None,
            output_units=None,
            estimated_cost_usd=float(proposal["estimated_cost_usd"]),
            output_asset_id=None,
            metadata_json=canonical_json(
                {
                    "command_id": command.command_id,
                    "target_script_version_id": script.id,
                    "target_script_scene_id": scene.id,
                    "media_generation": False,
                }
            ),
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    session.flush()
    return MutationResult(
        result=director_proposal_to_read(change_set),
        entity_type="director_proposal",
        entity_id=change_set.id,
        before_hash=script.content_hash,
        after_hash=content_hash(stored_impact),
    )


def _take_state_hash(take: Take) -> str:
    return content_hash(
        {
            "status": take.status,
            "approval": take.approval,
            "asset_id": take.asset_id,
            "is_current": take.is_current,
        }
    )


def _assert_preserved_director_objects(
    session: Session, preserved: list[dict[str, object]]
) -> None:
    for expected in preserved:
        if expected.get("type") != "Take":
            continue
        take = session.get(Take, str(expected["id"]))
        if take is None or _take_state_hash(take) != expected.get("state_hash"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PRESERVED_OBJECT_CHANGED",
                    "message": "范围外 Approved Take 已变化，Director 命令已中止",
                    "details": {"object": expected},
                },
            )


def _revised_entity(
    session: Session,
    *,
    source_scene_id: str,
    source_entity_id: str,
    revised_script_id: str,
    scope: str,
) -> tuple[ScriptScene, ScriptLine | None]:
    source_scene = session.get(ScriptScene, source_scene_id)
    if source_scene is None:
        raise RuntimeError("Director Proposal 来源场景丢失")
    revised_scene = session.scalar(
        select(ScriptScene).where(
            ScriptScene.script_version_id == revised_script_id,
            ScriptScene.ordinal == source_scene.ordinal,
        )
    )
    if revised_scene is None:
        raise RuntimeError("Director 修改后的场景丢失")
    if scope == "SCENE":
        return revised_scene, None
    source_line = session.get(ScriptLine, source_entity_id)
    revised_line = (
        session.scalar(
            select(ScriptLine).where(
                ScriptLine.script_scene_id == revised_scene.id,
                ScriptLine.ordinal == source_line.ordinal,
            )
        )
        if source_line is not None
        else None
    )
    if revised_line is None:
        raise RuntimeError("Director 修改后的台词丢失")
    return revised_scene, revised_line


def _execute_apply_director_proposal(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, change_set, impact = _director_proposal_target(
        session, project_id=project_id, command=command
    )
    if change_set.status != "PROPOSED":
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PROPOSAL_NOT_PENDING", "message": "Proposal 已处理"},
        )
    proposal = dict(impact["proposal"])
    base_script_id = str(proposal["base_script_version_id"])
    _validate_target(command, object_id=change_set.id, version_id=base_script_id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None or project.lock_version != expected_version:
        raise version_conflict(project, expected_version or 0)
    base_script = session.get(ScriptVersion, base_script_id)
    if base_script is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_BASE_VERSION_MISSING", "message": "基础剧本版本不存在"},
        )
    if (
        command.expected_version.target_hash is not None
        and base_script.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_CONTENT_CHANGED", "message": "基础剧本内容已变化"},
        )
    option_id = command.payload.get("option_id")
    option = next(
        (item for item in proposal["alternatives"] if item.get("option_id") == option_id),
        None,
    )
    if option is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_OPTION_INVALID", "message": "选择的修复方案不存在"},
        )
    change = dict(option["proposed_change"])
    change_scope = str(change["scope"])
    change_entity_id = str(change["entity_id"])
    if change_scope == "SCENE":
        target_entity = session.get(ScriptScene, change_entity_id)
        if (
            target_entity is None
            or target_entity.id != proposal["script_scene_id"]
            or target_entity.script_version_id != base_script.id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_TARGET_CHANGED",
                    "message": "Proposal 的 Scene 目标已变化",
                },
            )
        validated_changes = ScriptSceneUpdateRequest(
            expected_version=expected_version,
            **dict(change["changes"]),
        ).model_dump(exclude={"expected_version"}, exclude_none=True)
    else:
        target_entity = session.get(ScriptLine, change_entity_id)
        target_scene = (
            session.get(ScriptScene, target_entity.script_scene_id)
            if target_entity is not None
            else None
        )
        if (
            target_entity is None
            or target_scene is None
            or target_scene.id != proposal["script_scene_id"]
            or target_scene.script_version_id != base_script.id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_TARGET_CHANGED",
                    "message": "Proposal 的 Line 目标已变化",
                },
            )
        validated_changes = ScriptLineUpdateRequest(
            expected_version=expected_version,
            **dict(change["changes"]),
        ).model_dump(exclude={"expected_version"}, exclude_none=True)
    for field, expected in dict(change["before"]).items():
        if getattr(target_entity, field, object()) != expected:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_SOURCE_CHANGED",
                    "message": "Director 审查使用的原始字段已变化，请重新审查",
                    "details": {"field": field},
                },
            )
    preserved = list(dict(impact["impact"]).get("preserved_objects", []))
    _assert_preserved_director_objects(session, preserved)
    result = revise_script(
        session,
        script_id=base_script.id,
        expected_version=expected_version,
        scope=change_scope,
        entity_id=change_entity_id,
        changes=validated_changes,
        commit=False,
        allow_director_revision=True,
    )
    revised_scene, revised_line = _revised_entity(
        session,
        source_scene_id=str(proposal["script_scene_id"]),
        source_entity_id=change_entity_id,
        revised_script_id=str(result["id"]),
        scope=change_scope,
    )
    downstream = dict(impact["impact"])
    shot_ids = list(downstream.get("shot_ids", []))
    take_ids = list(downstream.get("take_ids", []))
    timeline_clip_ids = list(downstream.get("timeline_clip_ids", []))
    for shot in session.scalars(select(Shot).where(Shot.id.in_(shot_ids))):
        shot.status = "SUSPECT"
    for take in session.scalars(select(Take).where(Take.id.in_(take_ids))):
        take.status = "SUSPECT"
    for clip in session.scalars(select(TimelineClip).where(TimelineClip.id.in_(timeline_clip_ids))):
        clip.degraded = True
    _assert_preserved_director_objects(session, preserved)
    after_values = {
        key: getattr(revised_line or revised_scene, key) for key in dict(change["changes"])
    }
    impact["selected_option_id"] = option_id
    impact["result_script_version_id"] = result["id"]
    impact["comparison"] = {
        "before": change["before"],
        "after": after_values,
        "base_script_version_id": base_script.id,
        "result_script_version_id": result["id"],
        "estimated_duration_before_ms": sum(
            int(item["estimated_duration_ms"]) for item in dict(impact["context"]).get("lines", [])
        ),
        "estimated_duration_after_ms": sum(
            line.estimated_duration_ms
            for line in session.scalars(
                select(ScriptLine).where(ScriptLine.script_scene_id == revised_scene.id)
            )
        ),
        "media_generation": False,
    }
    impact["invalidated"] = downstream.get("affected_objects", [])
    change_set.impact_json = canonical_json(impact)
    change_set.status = "APPLIED_PENDING_APPROVAL"
    session.flush()
    return MutationResult(
        result={
            "proposal": director_proposal_to_read(change_set),
            "script": result,
        },
        entity_type="director_proposal",
        entity_id=change_set.id,
        before_hash=base_script.content_hash,
        after_hash=str(result["content_hash"]),
    )


def _execute_decide_director_proposal(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, change_set, impact = _director_proposal_target(
        session, project_id=project_id, command=command
    )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None or project.lock_version != expected_version:
        raise version_conflict(project, expected_version or 0)
    proposal = dict(impact["proposal"])
    decision = command.payload.get("decision")
    before_hash = content_hash(impact)
    target_script = session.get(ScriptVersion, command.target_version_id)
    if command.expected_version.target_hash is not None and (
        target_script is None or target_script.content_hash != command.expected_version.target_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "Director 决策目标版本已变化",
            },
        )
    if decision == "REJECT":
        if change_set.status != "PROPOSED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_REJECT_REQUIRES_PENDING",
                    "message": "已执行的 Proposal 请使用 Rollback",
                },
            )
        _validate_target(
            command,
            object_id=change_set.id,
            version_id=str(proposal["base_script_version_id"]),
        )
        change_set.status = "REJECTED"
    elif decision == "APPROVE":
        result_script_id = str(impact.get("result_script_version_id") or "")
        if change_set.status != "APPLIED_PENDING_APPROVAL" or not result_script_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "DIRECTOR_APPROVAL_NOT_READY", "message": "Proposal 尚未执行"},
            )
        _validate_target(command, object_id=change_set.id, version_id=result_script_id)
        change_set.status = "APPROVED"
        impact["approval_result"] = {
            "decision": "APPROVE",
            "actor": command.actor.id,
            "at": datetime.now(UTC).isoformat(),
        }
    elif decision == "ROLLBACK":
        result_script_id = str(impact.get("result_script_version_id") or "")
        if change_set.status != "APPLIED_PENDING_APPROVAL" or not result_script_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "DIRECTOR_ROLLBACK_NOT_READY", "message": "Proposal 尚未执行"},
            )
        _validate_target(command, object_id=change_set.id, version_id=result_script_id)
        option_id = str(impact["selected_option_id"])
        option = next(item for item in proposal["alternatives"] if item["option_id"] == option_id)
        change = dict(option["proposed_change"])
        revised_scene, revised_line = _revised_entity(
            session,
            source_scene_id=str(proposal["script_scene_id"]),
            source_entity_id=str(change["entity_id"]),
            revised_script_id=result_script_id,
            scope=str(change["scope"]),
        )
        rollback = revise_script(
            session,
            script_id=result_script_id,
            expected_version=expected_version,
            scope=str(change["scope"]),
            entity_id=(revised_line or revised_scene).id,
            changes=dict(change["before"]),
            commit=False,
            allow_director_revision=True,
        )
        impact["rollback_script_version_id"] = rollback["id"]
        impact["approval_result"] = {
            "decision": "ROLLBACK",
            "actor": command.actor.id,
            "at": datetime.now(UTC).isoformat(),
        }
        change_set.status = "ROLLED_BACK"
    else:
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_DECISION_INVALID", "message": "决策类型无效"},
        )
    change_set.impact_json = canonical_json(impact)
    session.flush()
    return MutationResult(
        result=director_proposal_to_read(change_set),
        entity_type="director_proposal",
        entity_id=change_set.id,
        before_hash=before_hash,
        after_hash=content_hash({"status": change_set.status, "impact": change_set.impact_json}),
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
        elif command.command_type == "REQUEST_SHOT_IMAGE_GENERATION":
            mutation = _execute_shot_image_generation_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_SHOT_VIDEO_GENERATION":
            mutation = _execute_shot_video_generation_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_CHARACTER_CANDIDATE_GENERATION":
            mutation = _execute_character_candidate_generation_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_CHARACTER_IDENTITY_VIEW_GENERATION":
            mutation = _execute_character_identity_view_generation_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "SELECT_CHARACTER_CANDIDATE":
            mutation = _execute_character_candidate_selection(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "DELETE_CHARACTER_CANDIDATE":
            mutation = _execute_character_candidate_deletion(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "DELETE_REFERENCE_ASSET":
            mutation = _execute_reference_asset_deletion(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "UPLOAD_REFERENCE_ASSET":
            mutation = _execute_reference_asset_upload(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPLY_CHARACTER_CHANGE":
            mutation = _execute_character_change(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_STORYBOARD_SHOT_REGENERATION":
            mutation = _execute_storyboard_shot_regeneration_request(
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
        elif command.command_type == "CREATE_DIRECTOR_PROPOSAL":
            mutation = _execute_create_director_proposal(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPLY_DIRECTOR_PROPOSAL":
            mutation = _execute_apply_director_proposal(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "DECIDE_DIRECTOR_PROPOSAL":
            mutation = _execute_decide_director_proposal(
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
        _run_rollback_action(mutation)
        winner = session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.scope == scope,
                IdempotencyKey.key == command.idempotency_key,
            )
        )
        if winner is None:
            raise
        return _replay(winner, command=command, request_hash=request_hash)
    except Exception:
        session.rollback()
        _run_rollback_action(mutation)
        raise
    _run_post_commit_action(mutation)
    return execution
