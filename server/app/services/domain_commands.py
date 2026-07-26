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
    AudioTake,
    AuditLog,
    BriefVersion,
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
    ProposalVersion,
    RelationshipGraphVersion,
    ReviewRecord,
    Scene,
    ScriptExcerptRevision,
    ScriptLine,
    ScriptScene,
    ScriptVersion,
    Shot,
    ShotSpec,
    StoryBibleVersion,
    StoryboardVersion,
    StoryVersion,
    Take,
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
    CharacterLockRequest,
    CharacterRevisionCreateRequest,
    CharacterVisualProfileConfirmRequest,
    CharacterVisualProfileUpdateRequest,
    ExportCreateRequest,
    ExportMatrixRequest,
    ExportProfileCreate,
    GenericReviewDecisionRequest,
    IdentityReviewRequest,
    JobRecoveryRequest,
    ProjectCreate,
    ProjectUpdate,
    ProposalApprovalRequest,
    ProposalGenerateRequest,
    ReferenceAssetUploadCommandPayload,
    RelationshipGraphActionRequest,
    RelationshipGraphCreateRequest,
    RelationshipGraphRejectRequest,
    RelationshipGraphRevisionRequest,
    RelationshipGraphUpdateRequest,
    RelationshipRevisionCreateRequest,
    RevisionCreateRequest,
    SceneShotOrderRequest,
    ScriptEpisodeUpdateRequest,
    ScriptExcerptRewriteApplyRequest,
    ScriptExcerptRewriteRequest,
    ScriptLineUpdateRequest,
    ScriptSceneUpdateRequest,
    ShotCharacterBindingUpdate,
    ShotImageGenerateRequest,
    ShotSpecUpdateRequest,
    ShotVideoGenerateRequest,
    StoryboardShotRegenerateRequest,
    StoryPackageGenerateRequest,
)
from app.services.assets import (
    asset_to_read,
    cleanup_unreferenced_asset_files,
    delete_reference_asset,
    sha256_file,
)
from app.services.character_revisions import create_character_revision
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
from app.services.creative_story import (
    approve_script,
    merge_story_directions,
    proposal_set_state_hash,
    proposal_state_hash,
    request_story_directions,
    request_story_structure,
    revise_script,
)
from app.services.delivery import create_export_matrix, create_export_profile
from app.services.dependency_analysis import (
    apply_dependency_invalidation,
    persist_dependency_edges,
)
from app.services.director_proposals import director_proposal_to_read
from app.services.events import append_event
from app.services.exports import create_export
from app.services.jobs import (
    job_state_hash,
    job_to_read,
    request_cancel,
    request_job_recovery,
    request_retry,
)
from app.services.media_production_v2 import decide_review
from app.services.preproduction import approve_preproduction
from app.services.production import (
    approve_proposal,
    lock_character,
    request_character_candidates,
)
from app.services.projects import (
    canonical_json,
    content_hash,
    create_project,
    prepare_project_deletion,
    update_project,
    version_conflict,
)
from app.services.proposals import create_proposal_job
from app.services.relationship_graph_workflow import (
    approve_relationship_graph,
    create_confirmed_relationship_revision,
    create_relationship_graph,
    create_relationship_graph_revision,
    reject_relationship_graph,
    set_relationship_lock,
    submit_relationship_graph,
    update_relationship_graph,
    withdraw_relationship_graph,
)
from app.services.revisions import approve_timeline, create_revision, rollback_timeline
from app.services.script_rewrites import (
    apply_script_excerpt_rewrite,
    persist_script_excerpt_rewrite,
    script_excerpt_revision_state_hash,
)
from app.services.storyboards_v2 import approve_storyboard, regenerate_storyboard_shot
from app.services.takes import (
    apply_candidate_take,
    approve_candidate_identity,
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


def replay_domain_command(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
    request_fingerprint: str | None = None,
) -> CommandExecution | None:
    request_hash = _request_hash(project_id, command, request_fingerprint)
    existing = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == _command_scope(project_id),
            IdempotencyKey.key == command.idempotency_key,
        )
    )
    if existing is None:
        return None
    return _replay(existing, command=command, request_hash=request_hash)


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
            (lambda path=asset_path: _delete_local_asset(path)) if asset_path is not None else None
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


def _job_command_state_hash(job: Job, project: Project) -> str:
    return content_hash(
        {
            "job": job_state_hash(job),
            "project": {
                "id": project.id,
                "lock_version": project.lock_version,
                "status": project.status,
                "available_points": project.available_points,
                "preview_approved": project.preview_approved,
                "export_ready": project.export_ready,
                "current_timeline_version_id": project.current_timeline_version_id,
            },
        }
    )


def _job_command_target(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Job, Project]:
    job = session.get(Job, command.target_object_id)
    project = session.get(Project, project_id)
    if (
        job is None
        or project is None
        or job.project_id != project.id
        or command.target_version_id != job.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标任务不存在"},
        )
    _validate_target(command, object_id=job.id, version_id=job.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "任务操作命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != job_state_hash(job)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "任务状态已经变化，请刷新后重试",
            },
        )
    return job, project


def _execute_job_operation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    job, project = _job_command_target(
        session,
        project_id=project_id,
        command=command,
    )
    before_hash = _job_command_state_hash(job, project)
    if command.command_type == "CANCEL_JOB":
        result = request_cancel(session, job.id, commit=False)
    elif command.command_type == "RETRY_JOB":
        result = request_retry(session, job.id, commit=False)
    else:
        validated = JobRecoveryRequest(
            **{key: value for key, value in command.payload.items() if key != "confirmed"}
        )
        result = request_job_recovery(
            session,
            job.id,
            validated,
            commit=False,
        )
    return MutationResult(
        result=result.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_job_command_state_hash(job, project),
    )


def _story_structure_request_state_hash(
    session: Session,
    project: Project,
    proposal: ProposalVersion,
) -> str:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.job_type == "GENERATE_STORY_STRUCTURE",
                Job.entity_id == proposal.id,
            )
            .order_by(Job.created_at, Job.id)
        ).all()
    )
    return content_hash(
        {
            "project": {
                "id": project.id,
                "lock_version": project.lock_version,
                "status": project.status,
            },
            "proposal": {
                "id": proposal.id,
                "version": proposal.version,
                "status": proposal.status,
                "content_hash": proposal_state_hash(proposal),
            },
            "jobs": [job_state_hash(job) for job in jobs],
        }
    )


def _story_direction_request_state_hash(
    session: Session,
    project: Project,
    brief: BriefVersion,
) -> str:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.project_id == project.id,
                Job.job_type == "GENERATE_STORY_DIRECTIONS",
                Job.entity_id == brief.id,
            )
            .order_by(Job.created_at, Job.id)
        ).all()
    )
    return content_hash(
        {
            "project": {
                "id": project.id,
                "lock_version": project.lock_version,
                "status": project.status,
            },
            "brief": {
                "id": brief.id,
                "version": brief.version,
                "content_hash": brief.content_hash,
                "status": brief.status,
            },
            "jobs": [job_state_hash(job) for job in jobs],
        }
    )


def _execute_story_direction_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    brief = session.get(BriefVersion, command.target_version_id)
    latest_brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    if (
        project is None
        or brief is None
        or brief.project_id != project.id
        or latest_brief is None
        or latest_brief.id != brief.id
        or command.target_object_id != project.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标创作简报不存在"},
        )
    _validate_target(command, object_id=project.id, version_id=brief.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "故事方向生成命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != brief.content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "创作简报已经变化，请刷新后重试",
            },
        )
    client_expected_version = command.payload.get("client_expected_version")
    request_idempotency_key = command.payload.get("request_idempotency_key")
    if (
        not isinstance(client_expected_version, int)
        or request_idempotency_key != command.idempotency_key
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "故事方向生成参数与命令目标不一致",
            },
        )
    before_hash = _story_direction_request_state_hash(session, project, brief)
    job, _business_replayed = request_story_directions(
        session,
        project_id=project.id,
        expected_version=client_expected_version,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    persisted_job = session.get(Job, job.id)
    if persisted_job is None:
        raise RuntimeError("故事方向生成任务创建后无法读取")
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_story_direction_request_state_hash(session, project, brief),
    )


def _execute_story_direction_merge(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    if project is None or command.target_object_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标项目不存在"},
        )
    _validate_target(command, object_id=project.id, version_id=project.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "故事方向合并命令必须提供项目锁版本",
            },
        )
    source_ids = command.payload.get("source_proposal_ids")
    title = command.payload.get("title")
    if (
        not isinstance(source_ids, list)
        or not all(isinstance(item, str) for item in source_ids)
        or (title is not None and not isinstance(title, str))
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "故事方向合并参数无效",
            },
        )
    unique_ids = list(dict.fromkeys(source_ids))
    sources = list(
        session.scalars(
            select(ProposalVersion).where(
                ProposalVersion.project_id == project_id,
                ProposalVersion.id.in_(unique_ids),
                ProposalVersion.status == "READY",
            )
        ).all()
    )
    source_hash = proposal_set_state_hash(sources)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != source_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "待合并的故事方向已经变化，请刷新后重试",
            },
        )
    before_hash = content_hash(
        {
            "project_lock_version": project.lock_version,
            "sources": source_hash,
        }
    )
    result = merge_story_directions(
        session,
        project_id=project_id,
        expected_version=expected_version,
        source_proposal_ids=source_ids,
        title=title,
        commit=False,
    )
    proposal = session.get(ProposalVersion, result.id)
    if proposal is None:
        raise RuntimeError("故事方向合并后无法读取")
    return MutationResult(
        result=result.model_dump(mode="json"),
        entity_type="proposal_version",
        entity_id=proposal.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "project_lock_version": project.lock_version,
                "proposal": proposal_state_hash(proposal),
            }
        ),
    )


def _character_revision_source_state_hash(
    project: Project,
    bible: StoryBibleVersion,
    graph: RelationshipGraphVersion,
) -> str:
    return content_hash(
        {
            "project": {
                "id": project.id,
                "lock_version": project.lock_version,
                "preview_approved": project.preview_approved,
                "export_ready": project.export_ready,
            },
            "story_bible": {
                "id": bible.id,
                "status": bible.status,
                "content_hash": bible.content_hash,
            },
            "relationship_graph": {
                "id": graph.id,
                "story_bible_version_id": graph.story_bible_version_id,
                "status": graph.status,
                "content_hash": graph.content_hash,
                "lock_version": graph.lock_version,
            },
        }
    )


def _execute_character_revision_creation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "角色修改命令必须提供项目锁版本",
            },
        )
    validated = CharacterRevisionCreateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **command.payload,
    )
    project = session.get(Project, project_id)
    bible = session.get(StoryBibleVersion, validated.base_story_bible_id)
    graph = session.get(RelationshipGraphVersion, validated.base_relationship_graph_id)
    if (
        project is None
        or command.target_object_id != project.id
        or bible is None
        or bible.project_id != project.id
        or graph is None
        or graph.project_id != project.id
        or graph.story_bible_version_id != bible.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "角色修改引用的故事版本不存在"},
        )
    _validate_target(command, object_id=project.id, version_id=bible.id)
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != bible.content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "基础故事设定已经变化，请重新审核角色修改",
            },
        )
    if command.expected_version.impact_hash != validated.impact_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHARACTER_REVISION_IMPACT_STALE",
                "message": "角色修改影响范围与命令不一致，请重新审核",
            },
        )
    before_hash = _character_revision_source_state_hash(project, bible, graph)
    result = create_character_revision(
        session,
        project_id=project.id,
        base_story_bible_id=bible.id,
        base_relationship_graph_id=graph.id,
        character_key=validated.character_key,
        changes=validated.changes,
        expected_version=expected_version,
        confirmed=validated.confirmed,
        impact_hash=validated.impact_hash,
        actor=command.actor.id,
        commit=False,
    )
    change_set_data = result.get("change_set")
    story_bible_data = result.get("story_bible")
    relationship_graph_data = result.get("relationship_graph")
    if (
        not isinstance(change_set_data, dict)
        or not isinstance(story_bible_data, dict)
        or not isinstance(relationship_graph_data, dict)
    ):
        raise RuntimeError("角色修改结果缺少版本信息")
    change_set_id = change_set_data.get("id")
    result_bible_id = story_bible_data.get("id")
    result_graph_id = relationship_graph_data.get("id")
    if not all(isinstance(item, str) for item in (change_set_id, result_bible_id, result_graph_id)):
        raise RuntimeError("角色修改结果版本标识无效")
    change_set = session.get(ChangeSet, change_set_id)
    result_bible = session.get(StoryBibleVersion, result_bible_id)
    result_graph = session.get(RelationshipGraphVersion, result_graph_id)
    if change_set is None or result_bible is None or result_graph is None:
        raise RuntimeError("角色修改版本创建后无法读取")
    return MutationResult(
        result=result,
        entity_type="change_set",
        entity_id=change_set.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "project_lock_version": project.lock_version,
                "source_story_bible_status": bible.status,
                "source_relationship_graph_status": graph.status,
                "story_bible": {
                    "id": result_bible.id,
                    "content_hash": result_bible.content_hash,
                    "status": result_bible.status,
                },
                "relationship_graph": {
                    "id": result_graph.id,
                    "content_hash": result_graph.content_hash,
                    "status": result_graph.status,
                },
                "change_set": {
                    "id": change_set.id,
                    "status": change_set.status,
                    "impact": change_set.impact_json,
                },
            }
        ),
    )


def _execute_script_excerpt_rewrite_creation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    script = session.get(ScriptVersion, command.target_version_id)
    project = session.get(Project, project_id)
    if (
        script is None
        or script.project_id != project_id
        or project is None
        or command.target_object_id != script.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "改写建议引用的剧本不存在"},
        )
    _validate_target(command, object_id=script.id, version_id=script.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "生成剧本改写建议必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != script.content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "基础剧本已经变化，请重新选择改写片段",
            },
        )
    line_id = command.payload.get("line_id")
    generated = command.payload.get("generated")
    if not isinstance(line_id, str) or not isinstance(generated, dict):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "剧本改写建议缺少目标台词或生成结果",
            },
        )
    validated = ScriptExcerptRewriteRequest(
        expected_version=expected_version,
        **{
            key: value
            for key, value in command.payload.items()
            if key
            in {
                "selection_start",
                "selection_end",
                "action",
                "custom_instruction",
                "tone",
                "parent_revision_id",
            }
        },
    )
    required_generated = {
        "original_text",
        "proposed_text",
        "rationale",
        "provider",
        "model",
    }
    if not required_generated <= generated.keys() or not all(
        isinstance(generated[key], str) for key in required_generated
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "剧本改写生成结果不完整",
            },
        )
    before_hash = content_hash(
        {
            "script_id": script.id,
            "script_hash": script.content_hash,
            "project_lock_version": project.lock_version,
        }
    )
    result = persist_script_excerpt_rewrite(
        session,
        script_id=script.id,
        line_id=line_id,
        expected_version=expected_version,
        selection_start=validated.selection_start,
        selection_end=validated.selection_end,
        action=validated.action,
        custom_instruction=validated.custom_instruction,
        tone=validated.tone,
        parent_revision_id=validated.parent_revision_id,
        original_text=str(generated["original_text"]),
        proposed_text=str(generated["proposed_text"]),
        rationale=str(generated["rationale"]),
        provider=str(generated["provider"]),
        model=str(generated["model"]),
        commit=False,
    )
    revision = session.get(ScriptExcerptRevision, result["id"])
    if revision is None:
        raise RuntimeError("剧本改写建议创建后无法读取")
    return MutationResult(
        result=result,
        entity_type="script_excerpt_revision",
        entity_id=revision.id,
        before_hash=before_hash,
        after_hash=script_excerpt_revision_state_hash(revision),
    )


def _execute_script_excerpt_rewrite_apply(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    revision = session.get(ScriptExcerptRevision, command.target_version_id)
    script_id = command.payload.get("script_id")
    line_id = command.payload.get("line_id")
    script = session.get(ScriptVersion, script_id) if isinstance(script_id, str) else None
    project = session.get(Project, project_id)
    if (
        revision is None
        or revision.project_id != project_id
        or script is None
        or script.project_id != project_id
        or project is None
        or command.target_object_id != script.id
        or not isinstance(line_id, str)
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "待应用的剧本改写不存在"},
        )
    _validate_target(command, object_id=script.id, version_id=revision.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "应用剧本改写必须提供项目锁版本",
            },
        )
    validated = ScriptExcerptRewriteApplyRequest(
        expected_version=expected_version,
        script_id=script.id,
        line_id=line_id,
    )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    revision_hash = script_excerpt_revision_state_hash(revision)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != revision_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "改写建议状态已经变化，请刷新后重试",
            },
        )
    before_hash = content_hash(
        {
            "script_hash": script.content_hash,
            "revision_hash": revision_hash,
            "project_lock_version": project.lock_version,
        }
    )
    result = apply_script_excerpt_rewrite(
        session,
        revision_id=revision.id,
        script_id=validated.script_id,
        line_id=validated.line_id,
        expected_version=expected_version,
        commit=False,
    )
    script_result = result.get("script")
    if not isinstance(script_result, dict) or not isinstance(script_result.get("id"), str):
        raise RuntimeError("剧本改写应用结果缺少新版本")
    revised = session.get(ScriptVersion, script_result["id"])
    if revised is None:
        raise RuntimeError("剧本改写应用后无法读取新版本")
    return MutationResult(
        result=result,
        entity_type="script_version",
        entity_id=revised.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "script_id": revised.id,
                "script_hash": revised.content_hash,
                "revision": script_excerpt_revision_state_hash(revision),
                "project_lock_version": project.lock_version,
            }
        ),
    )


def _relationship_graph_state_hash(
    project: Project,
    graph: RelationshipGraphVersion,
) -> str:
    return content_hash(
        {
            "project_id": project.id,
            "project_lock_version": project.lock_version,
            "project_status": project.status,
            "graph_id": graph.id,
            "graph_version": graph.version,
            "graph_lock_version": graph.lock_version,
            "graph_status": graph.status,
            "graph_content_hash": graph.content_hash,
            "story_bible_version_id": graph.story_bible_version_id,
            "parent_version_id": graph.parent_version_id,
            "approved_by": graph.approved_by,
            "approved_at": (
                graph.approved_at.isoformat() if graph.approved_at is not None else None
            ),
        }
    )


def _relationship_expected_project_version(command: DirectorCommand) -> int:
    expected = command.expected_version.project_lock_version
    if expected is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "关系网命令必须提供项目锁版本",
            },
        )
    return expected


def _relationship_expected_graph_version(command: DirectorCommand) -> int:
    expected = command.expected_version.object_lock_version
    if expected is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OBJECT_VERSION_REQUIRED",
                "message": "关系网命令必须提供关系版本锁",
            },
        )
    return expected


def _execute_relationship_graph_command(
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
    expected_project_version = _relationship_expected_project_version(command)

    if command.command_type == "CREATE_RELATIONSHIP_GRAPH":
        bible = session.get(StoryBibleVersion, command.target_version_id)
        if (
            bible is None
            or bible.project_id != project.id
            or command.target_object_id != project.id
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "关系网来源故事设定不存在"},
            )
        _validate_target(command, object_id=project.id, version_id=bible.id)
        if project.lock_version != expected_project_version:
            raise version_conflict(project, expected_project_version)
        if (
            command.expected_version.target_hash is not None
            and command.expected_version.target_hash != bible.content_hash
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TARGET_CONTENT_CHANGED",
                    "message": "来源故事设定已经变化，请刷新后重试",
                },
            )
        validated_create = RelationshipGraphCreateRequest(
            expected_project_version=expected_project_version,
            actor=command.actor.id,
            **{key: value for key, value in command.payload.items() if key != "confirmed"},
        )
        before_hash = content_hash(
            {
                "project_lock_version": project.lock_version,
                "story_bible_id": bible.id,
                "story_bible_hash": bible.content_hash,
            }
        )
        result = create_relationship_graph(
            session,
            project_id=project.id,
            expected_project_version=expected_project_version,
            story_bible_version_id=validated_create.story_bible_version_id,
            payload=validated_create.graph,
            actor=command.actor.id,
            commit=False,
        )
        graph = session.get(RelationshipGraphVersion, result["id"])
        if graph is None:
            raise RuntimeError("关系网创建后无法读取")
        return MutationResult(
            result=result,
            entity_type="relationship_graph",
            entity_id=graph.id,
            before_hash=before_hash,
            after_hash=_relationship_graph_state_hash(project, graph),
        )

    graph = session.get(RelationshipGraphVersion, command.target_version_id)
    if graph is None or graph.project_id != project.id or command.target_object_id != graph.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标关系网不存在"},
        )
    _validate_target(command, object_id=graph.id, version_id=graph.id)
    if project.lock_version != expected_project_version:
        raise version_conflict(project, expected_project_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != graph.content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "关系网内容已经变化，请刷新后重试",
            },
        )
    before_hash = _relationship_graph_state_hash(project, graph)
    payload = {key: value for key, value in command.payload.items() if key != "confirmed"}

    if command.command_type == "UPDATE_RELATIONSHIP_GRAPH":
        graph_version = _relationship_expected_graph_version(command)
        validated_update = RelationshipGraphUpdateRequest(
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            **payload,
        )
        result = update_relationship_graph(
            session,
            graph_id=graph.id,
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            payload=validated_update.graph_payload(),
            actor=command.actor.id,
            commit=False,
        )
        entity_type, entity_id = "relationship_graph", graph.id
    elif command.command_type in {
        "SUBMIT_RELATIONSHIP_GRAPH",
        "WITHDRAW_RELATIONSHIP_GRAPH",
        "APPROVE_RELATIONSHIP_GRAPH",
    }:
        graph_version = _relationship_expected_graph_version(command)
        validated_action = RelationshipGraphActionRequest(
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            **payload,
        )
        if command.command_type == "SUBMIT_RELATIONSHIP_GRAPH":
            result = submit_relationship_graph(
                session,
                graph_id=graph.id,
                expected_project_version=expected_project_version,
                expected_graph_version=graph_version,
                actor=command.actor.id,
                note=validated_action.note,
                commit=False,
            )
        elif command.command_type == "WITHDRAW_RELATIONSHIP_GRAPH":
            result = withdraw_relationship_graph(
                session,
                graph_id=graph.id,
                expected_project_version=expected_project_version,
                expected_graph_version=graph_version,
                actor=command.actor.id,
                note=validated_action.note,
                commit=False,
            )
        else:
            result = approve_relationship_graph(
                session,
                graph_id=graph.id,
                expected_project_version=expected_project_version,
                expected_graph_version=graph_version,
                actor=command.actor.id,
                note=validated_action.note,
                trace_id=command.command_id,
                commit=False,
            )
        entity_type, entity_id = "relationship_graph", graph.id
    elif command.command_type == "REJECT_RELATIONSHIP_GRAPH":
        graph_version = _relationship_expected_graph_version(command)
        validated_reject = RelationshipGraphRejectRequest(
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            **payload,
        )
        result = reject_relationship_graph(
            session,
            graph_id=graph.id,
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            note=validated_reject.note,
            issues=validated_reject.issues,
            commit=False,
        )
        entity_type, entity_id = "relationship_graph", graph.id
    elif command.command_type == "CREATE_RELATIONSHIP_GRAPH_REVISION":
        validated_revision = RelationshipGraphRevisionRequest(
            expected_project_version=expected_project_version,
            actor=command.actor.id,
            **payload,
        )
        result = create_relationship_graph_revision(
            session,
            graph_id=graph.id,
            expected_project_version=expected_project_version,
            actor=command.actor.id,
            note=validated_revision.note,
            commit=False,
        )
        entity_type, entity_id = "relationship_graph", str(result["id"])
    elif command.command_type == "CREATE_CONFIRMED_RELATIONSHIP_REVISION":
        validated_confirmed = RelationshipRevisionCreateRequest(
            expected_version=expected_project_version,
            actor=command.actor.id,
            confirmed=command.payload.get("confirmed") is True,
            **payload,
        )
        result = create_confirmed_relationship_revision(
            session,
            project_id=project.id,
            base_relationship_graph_id=graph.id,
            relationship_keys=validated_confirmed.relationship_keys,
            intent=validated_confirmed.intent,
            expected_version=expected_project_version,
            confirmed=validated_confirmed.confirmed,
            impact_hash=validated_confirmed.impact_hash,
            actor=command.actor.id,
            commit=False,
        )
        change_set = result.get("change_set")
        if not isinstance(change_set, dict) or not isinstance(change_set.get("id"), str):
            raise RuntimeError("关系修改版创建后缺少变更集")
        entity_type, entity_id = "change_set", str(change_set["id"])
    elif command.command_type == "SET_RELATIONSHIP_LOCK":
        graph_version = _relationship_expected_graph_version(command)
        relationship_key = payload.get("relationship_key")
        locked = payload.get("locked")
        if not isinstance(relationship_key, str) or not isinstance(locked, bool):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "COMMAND_PAYLOAD_INVALID",
                    "message": "关系锁定命令参数无效",
                },
            )
        validated_lock = RelationshipGraphActionRequest(
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            note=payload.get("note"),
        )
        result = set_relationship_lock(
            session,
            graph_id=graph.id,
            relationship_key=relationship_key,
            expected_project_version=expected_project_version,
            expected_graph_version=graph_version,
            actor=command.actor.id,
            locked=locked,
            commit=False,
        )
        _ = validated_lock
        entity_type, entity_id = "relationship_graph", graph.id
    else:  # pragma: no cover - guarded by dispatcher
        raise RuntimeError("不支持的关系网命令")

    after_graph = session.get(RelationshipGraphVersion, graph.id)
    after_hash = (
        _relationship_graph_state_hash(project, after_graph)
        if after_graph is not None
        else content_hash(result)
    )
    return MutationResult(
        result=result,
        entity_type=entity_type,
        entity_id=entity_id,
        before_hash=before_hash,
        after_hash=after_hash,
    )


def _execute_story_structure_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project = session.get(Project, project_id)
    proposal = session.get(ProposalVersion, command.target_version_id)
    if (
        project is None
        or proposal is None
        or proposal.project_id != project.id
        or command.target_object_id != project.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标故事方向不存在"},
        )
    _validate_target(command, object_id=project.id, version_id=proposal.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "故事结构生成命令必须提供项目锁版本",
            },
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != proposal_state_hash(proposal)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_CONTENT_CHANGED",
                "message": "目标故事方向已经变化，请刷新后重试",
            },
        )
    proposal_version = command.payload.get("proposal_version")
    client_expected_version = command.payload.get("client_expected_version")
    request_idempotency_key = command.payload.get("request_idempotency_key")
    if (
        proposal_version != proposal.version
        or not isinstance(client_expected_version, int)
        or request_idempotency_key != command.idempotency_key
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "故事结构生成参数与命令目标不一致",
            },
        )
    before_hash = _story_structure_request_state_hash(session, project, proposal)
    job, _business_replayed = request_story_structure(
        session,
        project_id=project.id,
        proposal_version=proposal.version,
        expected_version=client_expected_version,
        actor=command.actor.id,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    persisted_job = session.get(Job, job.id)
    if persisted_job is None:
        raise RuntimeError("故事结构生成任务创建后无法读取")
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=_story_structure_request_state_hash(session, project, proposal),
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


def _shot_spec_state_hash(shot: Shot, spec: ShotSpec | None) -> str:
    return content_hash(
        {
            "shot": {
                "id": shot.id,
                "ordinal": shot.ordinal,
                "description": shot.description,
                "dialogue": shot.dialogue,
                "shot_size": shot.shot_size,
                "camera_movement": shot.camera_movement,
                "status": shot.status,
                "lock_version": shot.lock_version,
            },
            "shot_spec": (
                {
                    "id": spec.id,
                    "storyboard_version_id": spec.storyboard_version_id,
                    "ordinal": spec.ordinal,
                    "description": spec.description,
                    "dialogue": spec.dialogue,
                    "shot_size": spec.shot_size,
                    "camera_movement": spec.camera_movement,
                    "status": spec.status,
                    "content_hash": spec.content_hash,
                }
                if spec is not None
                else None
            ),
        }
    )


def _shot_spec_content_hash(spec: ShotSpec) -> str:
    return content_hash(
        {
            "shot_id": spec.shot_id,
            "script_scene_id": spec.script_scene_id,
            "script_line_ids_json": spec.script_line_ids_json,
            "ordinal": spec.ordinal,
            "description": spec.description,
            "dialogue": spec.dialogue,
            "duration_ms": spec.duration_ms,
            "shot_size": spec.shot_size,
            "camera_movement": spec.camera_movement,
            "character_look_ids_json": spec.character_look_ids_json,
            "location_version_id": spec.location_version_id,
            "prop_version_ids_json": spec.prop_version_ids_json,
            "prompt_json": spec.prompt_json,
        }
    )


def _project_for_shot(session: Session, shot: Shot) -> Project:
    project = session.scalar(
        select(Project)
        .join(Episode, Episode.project_id == Project.id)
        .join(Scene, Scene.episode_id == Episode.id)
        .where(Scene.id == shot.scene_id)
    )
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标镜头不存在"},
        )
    return project


def _assert_shot_is_editable(
    session: Session,
    *,
    shot: Shot,
    spec: ShotSpec | None,
) -> None:
    storyboard = session.get(StoryboardVersion, spec.storyboard_version_id) if spec else None
    approved_take = session.scalar(
        select(Take.id).where(Take.shot_id == shot.id, Take.approval == "APPROVED").limit(1)
    )
    if (
        shot.status == "APPROVED"
        or (spec is not None and spec.status == "APPROVED")
        or (storyboard is not None and storyboard.status == "APPROVED")
        or approved_take is not None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "APPROVED_SHOT_EDIT_REQUIRES_REVISION",
                "message": "已批准镜头不能原地修改，请通过修订流程创建新版本",
                "user_action": "创建局部修订并确认影响范围；现有 Approved 资产会被保留",
                "retryable": False,
            },
        )


def _execute_shot_spec_update(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    shot = shot_or_404(session, command.target_object_id)
    project = _project_for_shot(session, shot)
    if project.id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标镜头不存在"},
        )
    spec = session.scalar(select(ShotSpec).where(ShotSpec.shot_id == shot.id))
    version_id = spec.id if spec is not None else shot.id
    _validate_target(command, object_id=shot.id, version_id=version_id)
    expected_version = _object_lock_version(command)
    if shot.lock_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SHOT_VERSION_CONFLICT",
                "message": "镜头已被其他操作修改，请刷新后重试",
                "details": {
                    "expected_version": expected_version,
                    "current_version": shot.lock_version,
                },
            },
        )
    _assert_shot_is_editable(session, shot=shot, spec=spec)
    before_hash = _shot_spec_state_hash(shot, spec)
    if (
        command.expected_version.target_hash is not None
        and command.expected_version.target_hash != before_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_CONTENT_CHANGED", "message": "镜头内容已经变化，请刷新后重试"},
        )
    validated = ShotSpecUpdateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    changes = validated.model_dump(
        exclude={"expected_version", "actor"},
        exclude_none=True,
    )
    for field, value in changes.items():
        setattr(shot, field, value)
        if spec is not None:
            setattr(spec, field, value)
    shot.lock_version += 1
    shot.status = "DRAFT"
    if spec is not None:
        spec.status = "DRAFT"
        spec.content_hash = _shot_spec_content_hash(spec)

    takes = session.scalars(select(Take).where(Take.shot_id == shot.id)).all()
    asset_ids = {take.asset_id for take in takes}
    for take in takes:
        take.status = "SUSPECT"
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
    session.flush()
    return MutationResult(
        result=shot_to_read(session, shot).model_dump(mode="json"),
        entity_type="shot_spec" if spec is not None else "shot",
        entity_id=version_id,
        before_hash=before_hash,
        after_hash=_shot_spec_state_hash(shot, spec),
    )


def _execute_scene_shot_reorder(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    scene = session.get(Scene, command.target_object_id)
    if scene is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标场景不存在"},
        )
    project = session.scalar(
        select(Project)
        .join(Episode, Episode.project_id == Project.id)
        .where(Episode.id == scene.episode_id)
    )
    if project is None or project.id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "COMMAND_TARGET_NOT_FOUND", "message": "命令目标场景不存在"},
        )
    _validate_target(command, object_id=scene.id, version_id=scene.id)
    expected_version = command.expected_version.project_lock_version
    if expected_version is None or project.lock_version != expected_version:
        raise version_conflict(project, expected_version or 0)
    validated = SceneShotOrderRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **{key: value for key, value in command.payload.items() if key != "confirmed"},
    )
    shots = session.scalars(
        select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.ordinal)
    ).all()
    existing_ids = [shot.id for shot in shots]
    if set(validated.shot_ids) != set(existing_ids) or len(validated.shot_ids) != len(existing_ids):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SHOT_ORDER_SCOPE_INVALID",
                "message": "镜头排序必须且只能包含当前场景的全部镜头",
            },
        )
    specs = session.scalars(select(ShotSpec).where(ShotSpec.shot_id.in_(existing_ids))).all()
    spec_by_shot = {spec.shot_id: spec for spec in specs}
    for shot in shots:
        _assert_shot_is_editable(session, shot=shot, spec=spec_by_shot.get(shot.id))
    before_hash = content_hash(
        {
            "scene_id": scene.id,
            "project_lock_version": project.lock_version,
            "shot_ids": existing_ids,
        }
    )
    ordinal_by_id = {shot_id: index + 1 for index, shot_id in enumerate(validated.shot_ids)}
    for index, spec in enumerate(specs, start=1):
        spec.ordinal = -(100_000 + index)
    session.flush()
    for shot in shots:
        shot.ordinal = ordinal_by_id[shot.id]
        shot.lock_version += 1
        spec = spec_by_shot.get(shot.id)
        if spec is not None:
            spec.ordinal = shot.ordinal
            spec.content_hash = _shot_spec_content_hash(spec)
    for timeline in session.scalars(
        select(TimelineVersion).where(TimelineVersion.project_id == project.id)
    ):
        if timeline.status != "SUPERSEDED":
            timeline.status = "SUSPECT"
    project.preview_approved = False
    project.lock_version += 1
    project.updated_at = datetime.now(UTC)
    session.flush()
    ordered = sorted(shots, key=lambda item: item.ordinal)
    result = {
        "scene_id": scene.id,
        "project_lock_version": project.lock_version,
        "shot_ids": [shot.id for shot in ordered],
        "shots": [shot_to_read(session, shot).model_dump(mode="json") for shot in ordered],
    }
    return MutationResult(
        result=result,
        entity_type="scene",
        entity_id=scene.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "scene_id": scene.id,
                "project_lock_version": project.lock_version,
                "shot_ids": result["shot_ids"],
            }
        ),
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
    before_hash = _storyboard_regeneration_state_hash(session, project, storyboard, spec, shot)
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
        after_hash=_storyboard_regeneration_state_hash(session, project, storyboard, spec, shot),
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


def _execute_shot_take_identity_confirmation(
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
    if shot.lock_version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "这个镜头刚刚发生了变化，请刷新后再确认",
                "details": {"expected": expected_version, "actual": shot.lock_version},
            },
        )
    before_hash = _shot_take_state_hash(session, project, shot)
    updated = approve_candidate_identity(
        session,
        shot.id,
        actor=command.actor.id,
        commit=False,
    )
    return MutationResult(
        result=shot_to_read(session, updated).model_dump(mode="json"),
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
    stored_impact["dependency_edge_count"] = persist_dependency_edges(
        session,
        project_id=project.id,
        change_set_id=change_set.id,
        edges=impact_payload.get("dependency_edges"),
    )
    change_set.impact_json = canonical_json(stored_impact)
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


def _audio_take_state_hash(take: AudioTake) -> str:
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
        object_type = expected.get("type")
        if object_type == "Take":
            record = session.get(Take, str(expected["id"]))
            state_hash = _take_state_hash(record) if record is not None else None
        elif object_type == "AudioTake":
            record = session.get(AudioTake, str(expected["id"]))
            state_hash = _audio_take_state_hash(record) if record is not None else None
        else:
            continue
        if record is None or state_hash != expected.get("state_hash"):
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


def _director_scene_timing(
    session: Session,
    *,
    script_id: str,
    scene_ordinal: int,
) -> dict[str, object]:
    cursor_ms = 0
    scenes = list(
        session.scalars(
            select(ScriptScene)
            .where(ScriptScene.script_version_id == script_id)
            .order_by(ScriptScene.ordinal)
        )
    )
    for scene in scenes:
        lines = list(
            session.scalars(
                select(ScriptLine)
                .where(ScriptLine.script_scene_id == scene.id)
                .order_by(ScriptLine.ordinal)
            )
        )
        dialogue_window_ms = sum(
            line.estimated_duration_ms + line.pause_after_ms for line in lines
        )
        projected_scene_window_ms = max(scene.duration_ms, dialogue_window_ms)
        if scene.ordinal == scene_ordinal:
            return {
                "script_version_id": script_id,
                "script_scene_id": scene.id,
                "scene_start_ms": cursor_ms,
                "duration_budget_ms": scene.duration_ms,
                "dialogue_window_ms": dialogue_window_ms,
                "projected_scene_window_ms": projected_scene_window_ms,
                "overflow_ms": max(0, dialogue_window_ms - scene.duration_ms),
            }
        cursor_ms += projected_scene_window_ms
    raise RuntimeError("Director Timeline Preview 目标场景丢失")


def _director_timeline_preview(
    session: Session,
    *,
    project: Project,
    base_script: ScriptVersion,
    revised_script_id: str,
    scene_ordinal: int,
) -> dict[str, object]:
    before = _director_scene_timing(
        session,
        script_id=base_script.id,
        scene_ordinal=scene_ordinal,
    )
    after = _director_scene_timing(
        session,
        script_id=revised_script_id,
        scene_ordinal=scene_ordinal,
    )
    delta_ms = int(after["projected_scene_window_ms"]) - int(
        before["projected_scene_window_ms"]
    )
    after_overflow_ms = int(after["overflow_ms"])
    risk = (
        "DURATION_BUDGET_EXCEEDED"
        if after_overflow_ms > 0
        else "DOWNSTREAM_TIMING_SHIFT"
        if delta_ms != 0
        else "NO_TIMING_CHANGE"
    )
    return {
        "schema_version": "director-timeline-preview-v1",
        "projection_mode": "READ_ONLY",
        "canonical_source": "SCRIPT",
        "scene_logical_id": (
            f"script-scene:{project.id}:{base_script.episode_ordinal}:{scene_ordinal}"
        ),
        "formal_timeline_version_id": project.current_timeline_version_id,
        "formal_timeline_unchanged": True,
        "media_generation": False,
        "affected_tracks": ["DIALOGUE", "SUBTITLE"],
        "before": before,
        "after": after,
        "downstream_shift_ms": delta_ms,
        "risk": risk,
        "validation_status": "REVIEW_REQUIRED" if after_overflow_ms > 0 else "PASS",
    }


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
    invalidation_result = apply_dependency_invalidation(
        session,
        project_id=project.id,
        impact=downstream,
    )
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
    impact["comparison"]["timeline_preview"] = _director_timeline_preview(
        session,
        project=project,
        base_script=base_script,
        revised_script_id=str(result["id"]),
        scene_ordinal=int(proposal["scene_ordinal"]),
    )
    impact["invalidated"] = downstream.get("affected_objects", [])
    impact["invalidation_result"] = invalidation_result
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


def _project_brief_state_hash(project: Project, brief: BriefVersion | None) -> str:
    return content_hash(
        {
            "project": {
                "id": project.id,
                "name": project.name,
                "idea": project.idea,
                "genre": project.genre,
                "style": project.style,
                "target_duration_sec": project.target_duration_sec,
                "aspect_ratio": project.aspect_ratio,
                "target_platform": project.target_platform,
                "status": project.status,
                "lock_version": project.lock_version,
            },
            "brief": (
                {
                    "id": brief.id,
                    "version": brief.version,
                    "content_hash": brief.content_hash,
                    "status": brief.status,
                }
                if brief is not None
                else None
            ),
        }
    )


def _latest_brief(session: Session, project_id: str) -> BriefVersion | None:
    return session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )


def _execute_project_brief_update(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    project = session.get(Project, project_id)
    if project is None or command.target_object_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "PROJECT_VERSION_REQUIRED", "message": "项目修改必须提供项目锁版本"},
        )
    latest_brief = _latest_brief(session, project.id)
    target_version_id = latest_brief.id if latest_brief is not None else project.id
    if command.target_version_id != target_version_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_VERSION_CONFLICT",
                "message": "项目简报版本已变化，请刷新后重试",
                "retryable": False,
                "details": {
                    "expected_target_version_id": command.target_version_id,
                    "latest_target_version_id": target_version_id,
                },
            },
        )
    before_hash = _project_brief_state_hash(project, latest_brief)
    if command.expected_version.target_hash not in {None, before_hash}:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_HASH_CONFLICT", "message": "项目简报内容已变化，请刷新后重试"},
        )
    _require_explicit_user_confirmation(command)
    changes = command.payload.get("changes")
    if not isinstance(changes, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "项目修改内容无效"},
        )
    validated = ProjectUpdate(expected_version=expected_version, **changes)
    updated = update_project(
        session,
        project.id,
        validated,
        commit=False,
    )
    next_brief = _latest_brief(session, project.id)
    session.refresh(project)
    return MutationResult(
        result=updated.model_dump(mode="json"),
        entity_type="project_brief",
        entity_id=next_brief.id if next_brief is not None else project.id,
        before_hash=before_hash,
        after_hash=_project_brief_state_hash(project, next_brief),
    )


def _execute_director_proposal_generation_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    project = session.get(Project, project_id)
    if project is None or command.target_object_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    latest_brief = _latest_brief(session, project.id)
    if latest_brief is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "BRIEF_REQUIRED", "message": "生成导演方案前需要先保存项目简报"},
        )
    if command.target_version_id != latest_brief.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TARGET_VERSION_CONFLICT",
                "message": "项目简报版本已变化，请刷新后重试",
            },
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "生成导演方案必须提供项目锁版本",
            },
        )
    before_hash = _project_brief_state_hash(project, latest_brief)
    if command.expected_version.target_hash not in {None, before_hash}:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_HASH_CONFLICT", "message": "项目简报内容已变化，请刷新后重试"},
        )
    _require_explicit_user_confirmation(command)
    ProposalGenerateRequest(expected_version=expected_version)
    job, _ = create_proposal_job(
        session,
        project_id=project.id,
        expected_version=expected_version,
        request_idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=content_hash(
            {
                "project_lock_version": project.lock_version,
                "project_status": project.status,
                "job": job.model_dump(mode="json"),
            }
        ),
    )


def _review_state_hash(session: Session, review: ReviewRecord) -> str:
    take = session.get(Take, review.entity_id) if review.entity_type == "take" else None
    return content_hash(
        {
            "review": {
                "id": review.id,
                "status": review.status,
                "decision": review.decision,
                "issues_json": review.issues_json,
                "note": review.note,
                "actor": review.actor,
                "decided_at": review.decided_at.isoformat() if review.decided_at else None,
            },
            "take": (
                {
                    "id": take.id,
                    "approval": take.approval,
                    "is_current": take.is_current,
                    "version": take.version,
                }
                if take is not None
                else None
            ),
        }
    )


def _execute_review_decision(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    review = session.get(ReviewRecord, command.target_object_id)
    if review is None or review.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "REVIEW_NOT_FOUND", "message": "审核记录不存在"},
        )
    if command.target_version_id != review.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "审核目标已变化，请刷新后重试"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "PROJECT_VERSION_REQUIRED", "message": "审核决策必须提供项目锁版本"},
        )
    before_hash = _review_state_hash(session, review)
    if command.expected_version.target_hash not in {None, before_hash}:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_HASH_CONFLICT", "message": "审核状态已变化，请刷新后重试"},
        )
    _require_explicit_user_confirmation(command)
    validated = GenericReviewDecisionRequest(
        expected_version=expected_version,
        decision=command.payload.get("decision"),
        issues=command.payload.get("issues", []),
        note=command.payload.get("note"),
        actor=command.actor.id,
    )
    review_result, next_job = decide_review(
        session,
        review_id=review.id,
        expected_version=validated.expected_version,
        decision=validated.decision,
        issues=validated.issues,
        note=validated.note,
        actor=validated.actor,
        commit=False,
    )
    session.refresh(review)
    return MutationResult(
        result={
            "review": review_result,
            "job": job_to_read(next_job).model_dump(mode="json") if next_job else None,
        },
        entity_type="review",
        entity_id=review.id,
        before_hash=before_hash,
        after_hash=_review_state_hash(session, review),
    )


def _project_target_state_hash(
    project: Project,
    *,
    target_type: str,
    target_id: str,
    target_status: str | None,
    target_version: int | None = None,
) -> str:
    return content_hash(
        {
            "project": {
                "id": project.id,
                "status": project.status,
                "lock_version": project.lock_version,
                "current_story_version_id": project.current_story_version_id,
                "current_timeline_version_id": project.current_timeline_version_id,
                "preview_approved": project.preview_approved,
                "export_ready": project.export_ready,
                "available_points": project.available_points,
            },
            "target": {
                "type": target_type,
                "id": target_id,
                "status": target_status,
                "version": target_version,
            },
        }
    )


def _command_project(
    session: Session,
    project_id: str,
    command: DirectorCommand,
) -> tuple[Project, int]:
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
            detail={"code": "PROJECT_VERSION_REQUIRED", "message": "该领域命令必须提供项目锁版本"},
        )
    if project.lock_version != expected_version:
        raise version_conflict(project, expected_version)
    return project, expected_version


def _execute_proposal_approval(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    proposal = session.get(ProposalVersion, command.target_object_id)
    if proposal is None or proposal.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROPOSAL_NOT_FOUND", "message": "导演方案版本不存在"},
        )
    if command.target_version_id != proposal.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "导演方案版本已变化"},
        )
    validated = ProposalApprovalRequest(
        expected_version=expected_version,
        assumptions_confirmed=command.payload.get("assumptions_confirmed"),
        actor=command.actor.id,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="proposal",
        target_id=proposal.id,
        target_status=proposal.status,
        target_version=proposal.version,
    )
    story, job, _ = approve_proposal(
        session,
        project_id=project.id,
        proposal_version=proposal.version,
        expected_version=validated.expected_version,
        assumptions_confirmed=validated.assumptions_confirmed,
        actor=validated.actor,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    session.refresh(proposal)
    return MutationResult(
        result={
            "story": story.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
        },
        entity_type="story_version",
        entity_id=story.id,
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="story",
            target_id=story.id,
            target_status=story.status,
            target_version=story.version,
        ),
    )


def _execute_character_candidates_request(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, _ = _command_project(session, project_id, command)
    story = (
        session.get(StoryVersion, project.current_story_version_id)
        if project.current_story_version_id
        else None
    )
    if (
        story is None
        or command.target_object_id != story.id
        or command.target_version_id != story.id
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "APPROVED_STORY_REQUIRED", "message": "当前批准故事版本不存在或已变化"},
        )
    before_hash = _project_target_state_hash(
        project,
        target_type="story",
        target_id=story.id,
        target_status=story.status,
        target_version=story.version,
    )
    job, _ = request_character_candidates(
        session,
        project_id=project.id,
        trace_id=command.command_id,
        commit=False,
    )
    return MutationResult(
        result=job.model_dump(mode="json"),
        entity_type="job",
        entity_id=job.id,
        before_hash=before_hash,
        after_hash=content_hash(job.model_dump(mode="json")),
    )


def _execute_character_candidate_lock(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    character = session.get(Character, command.target_object_id)
    candidate = session.get(CharacterCandidate, command.target_version_id)
    if (
        character is None
        or character.project_id != project.id
        or candidate is None
        or candidate.character_id != character.id
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "CHARACTER_CANDIDATE_NOT_FOUND", "message": "角色或候选不存在"},
        )
    validated = CharacterLockRequest(
        expected_version=expected_version,
        candidate_id=candidate.id,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="character",
        target_id=character.id,
        target_status=character.status,
        target_version=character.lock_version,
    )
    character_result, job, _ = lock_character(
        session,
        project_id=project.id,
        character_id=character.id,
        candidate_id=validated.candidate_id,
        expected_version=validated.expected_version,
        actor=command.actor.id,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    session.refresh(character)
    return MutationResult(
        result={
            "character": character_result.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
        },
        entity_type="character",
        entity_id=character.id,
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="character",
            target_id=character.id,
            target_status=character.status,
            target_version=character.lock_version,
        ),
    )


def _execute_preproduction_approval(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    target_version_id = project.current_story_version_id or project.id
    if command.target_object_id != project.id or command.target_version_id != target_version_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "前期制作基线已变化"},
        )
    validated = StoryPackageGenerateRequest(
        expected_version=expected_version,
        actor=command.actor.id,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="preproduction",
        target_id=target_version_id,
        target_status=project.status,
    )
    visual_bible, job, _ = approve_preproduction(
        session,
        project_id=project.id,
        expected_version=validated.expected_version,
        actor=validated.actor,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    return MutationResult(
        result={
            "visual_bible": visual_bible,
            "job": job.model_dump(mode="json"),
        },
        entity_type="visual_bible_version",
        entity_id=str(visual_bible["id"]),
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="visual_bible",
            target_id=str(visual_bible["id"]),
            target_status=str(visual_bible["status"]),
            target_version=int(visual_bible["version"]),
        ),
    )


def _execute_export_creation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    timeline_id = project.current_timeline_version_id or project.id
    if command.target_object_id != project.id or command.target_version_id != timeline_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "导出时间线基线已变化"},
        )
    validated = ExportCreateRequest(
        expected_version=expected_version,
        profile=command.payload.get("profile", "hybrid_720p"),
        rights_confirmed=command.payload.get("rights_confirmed"),
        actor=command.actor.id,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="timeline",
        target_id=timeline_id,
        target_status="APPROVED" if project.preview_approved else "UNAPPROVED",
    )
    export, job, _ = create_export(
        session,
        project_id=project.id,
        expected_version=validated.expected_version,
        profile=validated.profile,
        rights_confirmed=validated.rights_confirmed,
        actor=validated.actor,
        idempotency_key=command.idempotency_key,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    return MutationResult(
        result={
            "export": export.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
        },
        entity_type="export",
        entity_id=export.id,
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="export",
            target_id=export.id,
            target_status=export.status,
        ),
    )


def _execute_export_profile_creation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    if command.target_object_id != project.id or command.target_version_id != project.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "项目导出规格基线已变化"},
        )
    profile_payload = command.payload.get("profile")
    if not isinstance(profile_payload, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "导出规格内容无效"},
        )
    validated = ExportProfileCreate(
        expected_version=expected_version,
        actor=command.actor.id,
        **profile_payload,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="export_profile_set",
        target_id=project.id,
        target_status=project.status,
    )
    profile = create_export_profile(
        session,
        project_id=project.id,
        payload=validated,
        commit=False,
    )
    session.refresh(project)
    return MutationResult(
        result=profile,
        entity_type="export_profile",
        entity_id=str(profile["id"]),
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="export_profile",
            target_id=str(profile["id"]),
            target_status=str(profile["status"]),
            target_version=int(profile["version"]),
        ),
    )


def _execute_export_matrix_creation(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
) -> MutationResult:
    _require_explicit_user_confirmation(command)
    project, expected_version = _command_project(session, project_id, command)
    timeline_id = project.current_timeline_version_id or project.id
    if command.target_object_id != project.id or command.target_version_id != timeline_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "TARGET_VERSION_CONFLICT", "message": "交付时间线基线已变化"},
        )
    matrix_payload = command.payload.get("matrix")
    if not isinstance(matrix_payload, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "交付矩阵内容无效"},
        )
    validated = ExportMatrixRequest(
        expected_version=expected_version,
        actor=command.actor.id,
        **matrix_payload,
    )
    before_hash = _project_target_state_hash(
        project,
        target_type="timeline",
        target_id=timeline_id,
        target_status="APPROVED" if project.preview_approved else "UNAPPROVED",
    )
    matrix = create_export_matrix(
        session,
        project_id=project.id,
        payload=validated,
        trace_id=command.command_id,
        commit=False,
    )
    session.refresh(project)
    return MutationResult(
        result={"exports": matrix},
        entity_type="export_matrix",
        entity_id=timeline_id,
        before_hash=before_hash,
        after_hash=_project_target_state_hash(
            project,
            target_type="export_matrix",
            target_id=timeline_id,
            target_status=project.status,
        ),
    )


async def dispatch_project_create_command(
    session: Session,
    *,
    command: DirectorCommand,
    request_fingerprint: str | None = None,
) -> CommandExecution:
    if command.command_type != "CREATE_PROJECT":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_TYPE_UNSUPPORTED",
                "message": "项目创建边界仅接受创建项目命令",
            },
        )
    if (
        command.target_object_id != command.command_id
        or command.target_version_id != command.command_id
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROJECT_BOOTSTRAP_TARGET_INVALID",
                "message": "项目创建命令必须使用命令 ID 作为创建前目标",
            },
        )
    _require_explicit_user_confirmation(command)
    scope = "domain-command:project-create"
    request_hash = _request_hash("project-bootstrap", command, request_fingerprint)
    existing = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == command.idempotency_key,
        )
    )
    if existing is not None:
        return _replay(existing, command=command, request_hash=request_hash)
    project_payload = command.payload.get("project")
    if not isinstance(project_payload, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "COMMAND_PAYLOAD_INVALID", "message": "项目创建内容无效"},
        )
    try:
        validated = ProjectCreate.model_validate(project_payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_PAYLOAD_INVALID",
                "message": "项目创建内容不符合约束",
                "details": {
                    "issues": exc.errors(include_url=False, include_context=False),
                },
            },
        ) from exc
    created = await create_project(
        session,
        validated,
        command.idempotency_key,
        commit=False,
        manage_idempotency=False,
    )
    project_id = created.project.id
    result = created.model_dump(mode="json")
    after_hash = content_hash(result)
    session.add(
        AuditLog(
            id=command.command_id,
            project_id=project_id,
            actor=command.actor.id,
            action=command.command_type,
            entity_type="project",
            entity_id=project_id,
            before_hash=content_hash(None),
            after_hash=after_hash,
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
            "result_entity_type": "project",
            "result_entity_id": project_id,
            "actor": command.actor.model_dump(mode="json"),
        },
    )
    execution = CommandExecution(
        command_id=command.command_id,
        command_type=command.command_type,
        status="SUCCEEDED",
        result=RESULT_ADAPTER.dump_python(result, mode="json"),
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
            status_code=201,
            resource_id=project_id,
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
    except Exception:
        session.rollback()
        raise
    return execution


def dispatch_project_delete_command(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
    request_fingerprint: str | None = None,
) -> CommandExecution:
    if command.command_type != "DELETE_PROJECT":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "COMMAND_TYPE_UNSUPPORTED",
                "message": "项目删除边界仅接受删除项目命令",
            },
        )
    if command.target_object_id != project_id or command.target_version_id != project_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "PROJECT_DELETE_TARGET_INVALID", "message": "项目删除目标无效"},
        )
    _require_explicit_user_confirmation(command)
    scope = "domain-command:project-delete"
    request_hash = _request_hash(project_id, command, request_fingerprint)
    existing = session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == command.idempotency_key,
        )
    )
    if existing is not None:
        return _replay(existing, command=command, request_hash=request_hash)
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"},
        )
    expected_version = command.expected_version.project_lock_version
    if expected_version is None or expected_version != project.lock_version:
        raise version_conflict(project, expected_version or 0)
    before_hash = _project_brief_state_hash(project, _latest_brief(session, project.id))
    result, cleanup_files = prepare_project_deletion(session, project.id)
    result["audit_mode"] = "DELETE_TOMBSTONE"
    result["before_hash"] = before_hash
    execution = CommandExecution(
        command_id=command.command_id,
        command_type=command.command_type,
        status="SUCCEEDED",
        result=RESULT_ADAPTER.dump_python(result, mode="json"),
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
            resource_id=project_id,
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
    except Exception:
        session.rollback()
        raise
    try:
        cleanup_files()
    except OSError:
        logger.exception("项目已删除，但部分项目素材文件清理失败：project_id=%s", project_id)
    return execution


def dispatch_domain_command(
    session: Session,
    *,
    project_id: str,
    command: DirectorCommand,
    request_fingerprint: str | None = None,
) -> CommandExecution:
    request_hash = _request_hash(project_id, command, request_fingerprint)
    scope = _command_scope(project_id)
    replayed = replay_domain_command(
        session,
        project_id=project_id,
        command=command,
        request_fingerprint=request_fingerprint,
    )
    if replayed is not None:
        return replayed

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
        elif command.command_type == "UPDATE_SHOT_SPEC":
            mutation = _execute_shot_spec_update(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REORDER_SCENE_SHOTS":
            mutation = _execute_scene_shot_reorder(
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
        elif command.command_type == "CONFIRM_SHOT_TAKE_IDENTITY":
            mutation = _execute_shot_take_identity_confirmation(
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
        elif command.command_type in {"CANCEL_JOB", "RETRY_JOB", "RECOVER_JOB"}:
            mutation = _execute_job_operation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_STORY_DIRECTION_GENERATION":
            mutation = _execute_story_direction_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_STORY_STRUCTURE_GENERATION":
            mutation = _execute_story_structure_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "MERGE_STORY_DIRECTIONS":
            mutation = _execute_story_direction_merge(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_CHARACTER_REVISION":
            mutation = _execute_character_revision_creation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_SCRIPT_EXCERPT_REWRITE":
            mutation = _execute_script_excerpt_rewrite_creation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPLY_SCRIPT_EXCERPT_REWRITE":
            mutation = _execute_script_excerpt_rewrite_apply(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type in {
            "CREATE_RELATIONSHIP_GRAPH",
            "UPDATE_RELATIONSHIP_GRAPH",
            "SUBMIT_RELATIONSHIP_GRAPH",
            "WITHDRAW_RELATIONSHIP_GRAPH",
            "REJECT_RELATIONSHIP_GRAPH",
            "APPROVE_RELATIONSHIP_GRAPH",
            "CREATE_RELATIONSHIP_GRAPH_REVISION",
            "CREATE_CONFIRMED_RELATIONSHIP_REVISION",
            "SET_RELATIONSHIP_LOCK",
        }:
            mutation = _execute_relationship_graph_command(
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
        elif command.command_type == "UPDATE_PROJECT_BRIEF":
            mutation = _execute_project_brief_update(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_DIRECTOR_PROPOSAL_GENERATION":
            mutation = _execute_director_proposal_generation_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "DECIDE_REVIEW":
            mutation = _execute_review_decision(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPROVE_PROPOSAL":
            mutation = _execute_proposal_approval(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "REQUEST_CHARACTER_CANDIDATES":
            mutation = _execute_character_candidates_request(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "LOCK_CHARACTER_CANDIDATE":
            mutation = _execute_character_candidate_lock(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "APPROVE_PREPRODUCTION":
            mutation = _execute_preproduction_approval(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_EXPORT":
            mutation = _execute_export_creation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_EXPORT_PROFILE":
            mutation = _execute_export_profile_creation(
                session,
                project_id=project_id,
                command=command,
            )
        elif command.command_type == "CREATE_EXPORT_MATRIX":
            mutation = _execute_export_matrix_creation(
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
