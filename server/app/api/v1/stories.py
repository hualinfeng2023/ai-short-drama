from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.trace import success
from app.config import get_settings
from app.db.models import (
    BriefVersion,
    Project,
    ProposalVersion,
    ScriptExcerptRevision,
    ScriptVersion,
    StoryBibleVersion,
)
from app.db.session import get_session
from app.domain.commands import CommandActor, DirectorCommand, ExpectedVersion
from app.schemas import (
    CharacterRevisionCreateRequest,
    CharacterRevisionReviewRequest,
    ProposalGenerateRequest,
    ScriptApprovalRequest,
    ScriptEpisodeUpdateRequest,
    ScriptExcerptRewriteApplyRequest,
    ScriptExcerptRewriteRequest,
    ScriptLineUpdateRequest,
    ScriptSceneUpdateRequest,
    StoryDirectionMergeRequest,
    StoryPackageGenerateRequest,
)
from app.services.character_revisions import review_character_revision
from app.services.creative_story import (
    list_story_directions,
    proposal_set_state_hash,
    proposal_state_hash,
    story_package_estimate,
    story_workspace,
)
from app.services.domain_commands import dispatch_domain_command, replay_domain_command
from app.services.projects import content_hash
from app.services.script_rewrites import (
    generate_script_excerpt_rewrite_candidate,
    list_script_excerpt_rewrites,
    script_excerpt_revision_state_hash,
)

router = APIRouter(prefix="/api/v1", tags=["story"])


def _dispatch_script_revision(
    session: Session,
    *,
    script_id: str,
    expected_version: int,
    scope: str,
    entity_id: str,
    changes: dict[str, object],
    idempotency_key: str | None,
) -> tuple[dict[str, object], bool]:
    source = session.get(ScriptVersion, script_id)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "剧本不存在"},
        )
    command_id = (
        str(uuid5(NAMESPACE_URL, f"{source.project_id}:script-command:{idempotency_key}"))
        if idempotency_key
        else str(uuid4())
    )
    execution = dispatch_domain_command(
        session,
        project_id=source.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="REVISE_SCRIPT",
            actor=CommandActor(type="USER", id="demo-user"),
            target_object_id=source.id,
            target_version_id=source.id,
            expected_version=ExpectedVersion(
                project_lock_version=expected_version,
                target_version_id=source.id,
                target_hash=source.content_hash,
            ),
            payload={
                "scope": scope,
                "entity_id": entity_id,
                "changes": changes,
            },
            idempotency_key=idempotency_key or f"script-adapter:{command_id}",
        ),
        request_fingerprint=content_hash(
            {
                "route": f"script-revision:{script_id}:{scope}:{entity_id}",
                "expected_version": expected_version,
                "changes": changes,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


def _dispatch_script_approval(
    session: Session,
    *,
    script_id: str,
    expected_version: int,
    actor: str,
    idempotency_key: str,
) -> tuple[dict[str, object], bool]:
    script = session.get(ScriptVersion, script_id)
    if script is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "剧本不存在"},
        )
    command_id = str(uuid5(NAMESPACE_URL, f"{script.project_id}:domain-command:{idempotency_key}"))
    execution = dispatch_domain_command(
        session,
        project_id=script.project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="APPROVE_SCRIPT",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=script.id,
            target_version_id=script.id,
            expected_version=ExpectedVersion(
                project_lock_version=expected_version,
                target_version_id=script.id,
                target_hash=script.content_hash,
            ),
            payload={"confirmed": True},
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"script-approval:{script.id}",
                "expected_version": expected_version,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    return execution.result, execution.idempotency_replayed


@router.post(
    "/projects/{project_id}/story-directions",
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_story_directions(
    project_id: str,
    payload: ProposalGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="demo-user", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    brief = session.scalar(
        select(BriefVersion)
        .where(BriefVersion.project_id == project_id)
        .order_by(BriefVersion.version.desc())
    )
    if project is None or brief is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "项目或创作简报不存在"},
        )
    command_id = str(
        uuid5(NAMESPACE_URL, f"{project_id}:story-direction-command:{idempotency_key}")
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="REQUEST_STORY_DIRECTION_GENERATION",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=project.id,
            target_version_id=brief.id,
            expected_version=ExpectedVersion(
                project_lock_version=project.lock_version,
                target_version_id=brief.id,
                target_hash=brief.content_hash,
            ),
            payload={
                "client_expected_version": payload.expected_version,
                "request_idempotency_key": idempotency_key,
                "confirmed": True,
            },
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"story-directions:{project_id}",
                "expected_version": payload.expected_version,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/story-package-estimate")
def get_story_package_estimate(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(story_package_estimate(session, project_id))


@router.get("/projects/{project_id}/story-directions")
def story_directions(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return success(list_story_directions(session, project_id))


@router.post("/projects/{project_id}/story-directions/merge")
def merge_directions(
    project_id: str,
    payload: StoryDirectionMergeRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    actor: str = Header(default="demo-user", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "项目不存在"},
        )
    source_ids = list(dict.fromkeys(payload.source_proposal_ids))
    sources = list(
        session.scalars(
            select(ProposalVersion).where(
                ProposalVersion.project_id == project_id,
                ProposalVersion.id.in_(source_ids),
                ProposalVersion.status == "READY",
            )
        ).all()
    )
    command_id = str(uuid5(NAMESPACE_URL, f"{project_id}:story-merge-command:{idempotency_key}"))
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="MERGE_STORY_DIRECTIONS",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=project.id,
            target_version_id=project.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=project.id,
                target_hash=proposal_set_state_hash(sources),
            ),
            payload={
                "source_proposal_ids": payload.source_proposal_ids,
                "title": payload.title,
                "confirmed": True,
            },
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"story-directions-merge:{project_id}",
                "expected_version": payload.expected_version,
                "source_proposal_ids": payload.source_proposal_ids,
                "title": payload.title,
                "actor": actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.post(
    "/projects/{project_id}/story-dna/{proposal_version}/approve",
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_story_dna(
    project_id: str,
    proposal_version: int,
    payload: StoryPackageGenerateRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    proposal = session.scalar(
        select(ProposalVersion).where(
            ProposalVersion.project_id == project_id,
            ProposalVersion.version == proposal_version,
        )
    )
    if project is None or proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "故事方向不存在"},
        )
    command_id = str(uuid5(NAMESPACE_URL, f"{project.id}:domain-command:{idempotency_key}"))
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="REQUEST_STORY_STRUCTURE_GENERATION",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=proposal.id,
            expected_version=ExpectedVersion(
                project_lock_version=project.lock_version,
                target_version_id=proposal.id,
                target_hash=proposal_state_hash(proposal),
            ),
            payload={
                "proposal_version": proposal.version,
                "client_expected_version": payload.expected_version,
                "request_idempotency_key": idempotency_key,
                "confirmed": True,
            },
            idempotency_key=idempotency_key,
        ),
        request_fingerprint=content_hash(
            {
                "route": f"story-structure-request:{project.id}:{proposal.id}",
                "client_expected_version": payload.expected_version,
                "actor": payload.actor,
                "confirmed": True,
            }
        ),
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/projects/{project_id}/story-workspace")
def get_story_workspace(
    project_id: str, session: Session = Depends(get_session)
) -> dict[str, object]:
    return success(story_workspace(session, project_id))


@router.post("/projects/{project_id}/character-revision-review")
async def review_story_character_revision(
    project_id: str,
    payload: CharacterRevisionReviewRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        await review_character_revision(
            session,
            get_settings(),
            project_id=project_id,
            base_story_bible_id=payload.base_story_bible_id,
            base_relationship_graph_id=payload.base_relationship_graph_id,
            character_key=payload.character_key,
            changes=payload.changes,
            expected_version=payload.expected_version,
        )
    )


@router.post("/projects/{project_id}/character-revisions", status_code=status.HTTP_201_CREATED)
def confirm_story_character_revision(
    project_id: str,
    payload: CharacterRevisionCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    project = session.get(Project, project_id)
    bible = session.get(StoryBibleVersion, payload.base_story_bible_id)
    if project is None or bible is None or bible.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "项目或基础故事设定不存在"},
        )
    request_payload = payload.model_dump(mode="json")
    request_fingerprint = content_hash(
        {
            "route": f"character-revisions:{project_id}",
            "payload": request_payload,
        }
    )
    effective_idempotency_key = idempotency_key or f"character-revision-{request_fingerprint}"
    command_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{project_id}:character-revision-command:{effective_idempotency_key}",
        )
    )
    execution = dispatch_domain_command(
        session,
        project_id=project_id,
        command=DirectorCommand(
            command_id=command_id,
            command_type="CREATE_CHARACTER_REVISION",
            actor=CommandActor(type="USER", id=payload.actor),
            target_object_id=project.id,
            target_version_id=bible.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=bible.id,
                target_hash=bible.content_hash,
                impact_hash=payload.impact_hash,
            ),
            payload={
                "base_story_bible_id": payload.base_story_bible_id,
                "base_relationship_graph_id": payload.base_relationship_graph_id,
                "character_key": payload.character_key,
                "changes": payload.changes.model_dump(mode="json", exclude_none=True),
                "confirmed": payload.confirmed,
                "impact_hash": payload.impact_hash,
            },
            idempotency_key=effective_idempotency_key,
        ),
        request_fingerprint=request_fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.post("/scripts/{script_id}/approve", status_code=status.HTTP_202_ACCEPTED)
def approve_script_version(
    script_id: str,
    payload: ScriptApprovalRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_script_approval(
        session,
        script_id=script_id,
        expected_version=payload.expected_version,
        actor=payload.actor,
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.patch("/scripts/{script_id}")
def update_script_episode(
    script_id: str,
    payload: ScriptEpisodeUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_script_revision(
        session,
        script_id=script_id,
        expected_version=payload.expected_version,
        scope="EPISODE",
        entity_id=script_id,
        changes={"title": payload.title},
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.patch("/scripts/{script_id}/scenes/{scene_id}")
def update_script_scene(
    script_id: str,
    scene_id: str,
    payload: ScriptSceneUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_script_revision(
        session,
        script_id=script_id,
        expected_version=payload.expected_version,
        scope="SCENE",
        entity_id=scene_id,
        changes=payload.model_dump(exclude={"expected_version"}, exclude_none=True),
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.patch("/scripts/{script_id}/lines/{line_id}")
def update_script_line(
    script_id: str,
    line_id: str,
    payload: ScriptLineUpdateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    result, replayed = _dispatch_script_revision(
        session,
        script_id=script_id,
        expected_version=payload.expected_version,
        scope="LINE",
        entity_id=line_id,
        changes=payload.model_dump(exclude={"expected_version"}, exclude_none=True),
        idempotency_key=idempotency_key,
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return success(result)


@router.post(
    "/scripts/{script_id}/lines/{line_id}/rewrites",
    status_code=status.HTTP_201_CREATED,
)
async def generate_script_excerpt_rewrite(
    script_id: str,
    line_id: str,
    payload: ScriptExcerptRewriteRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    actor: str = Header(default="demo-user", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    script = session.get(ScriptVersion, script_id)
    if script is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "剧本不存在"},
        )
    request_data = payload.model_dump(mode="json")
    request_fingerprint = content_hash(
        {
            "route": f"script-excerpt-rewrite:{script_id}:{line_id}",
            "payload": request_data,
            "actor": actor,
        }
    )
    effective_idempotency_key = idempotency_key or f"script-excerpt-rewrite-{request_fingerprint}"
    command = DirectorCommand(
        command_id=str(
            uuid5(
                NAMESPACE_URL,
                f"{script.project_id}:script-excerpt-command:{effective_idempotency_key}",
            )
        ),
        command_type="CREATE_SCRIPT_EXCERPT_REWRITE",
        actor=CommandActor(type="USER", id=actor),
        target_object_id=script.id,
        target_version_id=script.id,
        expected_version=ExpectedVersion(
            project_lock_version=payload.expected_version,
            target_version_id=script.id,
            target_hash=script.content_hash,
        ),
        payload={
            **request_data,
            "line_id": line_id,
            "confirmed": True,
        },
        idempotency_key=effective_idempotency_key,
    )
    replayed = replay_domain_command(
        session,
        project_id=script.project_id,
        command=command,
        request_fingerprint=request_fingerprint,
    )
    if replayed is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return success(replayed.result)
    generated = await generate_script_excerpt_rewrite_candidate(
        session,
        get_settings(),
        script_id=script.id,
        line_id=line_id,
        expected_version=payload.expected_version,
        selection_start=payload.selection_start,
        selection_end=payload.selection_end,
        action=payload.action,
        custom_instruction=payload.custom_instruction,
        tone=payload.tone,
        parent_revision_id=payload.parent_revision_id,
    )
    execution = dispatch_domain_command(
        session,
        project_id=script.project_id,
        command=command.model_copy(
            update={
                "payload": {
                    **command.payload,
                    "generated": generated,
                }
            }
        ),
        request_fingerprint=request_fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)


@router.get("/scripts/{script_id}/lines/{line_id}/rewrites")
def get_script_excerpt_rewrites(
    script_id: str,
    line_id: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return success(
        list_script_excerpt_rewrites(
            session,
            script_id=script_id,
            line_id=line_id,
        )
    )


@router.post("/script-excerpt-rewrites/{revision_id}/apply")
def use_script_excerpt_rewrite(
    revision_id: str,
    payload: ScriptExcerptRewriteApplyRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
    ),
    actor: str = Header(default="demo-user", alias="X-Actor", min_length=1, max_length=80),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    revision = session.get(ScriptExcerptRevision, revision_id)
    script = session.get(ScriptVersion, payload.script_id)
    if revision is None or script is None or revision.project_id != script.project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "剧本或改写建议不存在"},
        )
    request_data = payload.model_dump(mode="json")
    request_fingerprint = content_hash(
        {
            "route": f"script-excerpt-rewrite-apply:{revision.id}",
            "payload": request_data,
            "actor": actor,
        }
    )
    effective_idempotency_key = idempotency_key or f"script-rewrite-apply-{revision.id}"
    execution = dispatch_domain_command(
        session,
        project_id=script.project_id,
        command=DirectorCommand(
            command_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"{script.project_id}:script-rewrite-apply:{effective_idempotency_key}",
                )
            ),
            command_type="APPLY_SCRIPT_EXCERPT_REWRITE",
            actor=CommandActor(type="USER", id=actor),
            target_object_id=script.id,
            target_version_id=revision.id,
            expected_version=ExpectedVersion(
                project_lock_version=payload.expected_version,
                target_version_id=revision.id,
                target_hash=script_excerpt_revision_state_hash(revision),
            ),
            payload={
                "script_id": script.id,
                "line_id": payload.line_id,
                "confirmed": True,
            },
            idempotency_key=effective_idempotency_key,
        ),
        request_fingerprint=request_fingerprint,
    )
    response.headers["Idempotency-Replayed"] = str(execution.idempotency_replayed).lower()
    return success(execution.result)
