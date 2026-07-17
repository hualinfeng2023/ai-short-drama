from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSAL_RUNNING = "PROPOSAL_RUNNING"
    PROPOSAL_READY = "PROPOSAL_READY"
    STORY_STRUCTURE_RUNNING = "STORY_STRUCTURE_RUNNING"
    RELATIONSHIP_READY = "RELATIONSHIP_READY"
    CHARACTER_VISUAL_READY = "CHARACTER_VISUAL_READY"
    SCRIPT_PACKAGE_RUNNING = "SCRIPT_PACKAGE_RUNNING"
    STORY_PACKAGE_RUNNING = "STORY_PACKAGE_RUNNING"
    SCRIPT_READY = "SCRIPT_READY"
    STORY_APPROVED = "STORY_APPROVED"
    PREPRODUCTION_READY = "PREPRODUCTION_READY"
    PREPRODUCTION_APPROVED = "PREPRODUCTION_APPROVED"
    STORYBOARD_READY = "STORYBOARD_READY"
    STORYBOARD_APPROVED = "STORYBOARD_APPROVED"
    CHARACTER_LOCKED = "CHARACTER_LOCKED"
    PRODUCING = "PRODUCING"
    PREVIEW_READY = "PREVIEW_READY"
    APPROVED = "APPROVED"
    EXPORTING = "EXPORTING"
    EXPORTED = "EXPORTED"
    BLOCKED = "BLOCKED"
    ARCHIVED = "ARCHIVED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RETRY_WAIT = "RETRY_WAIT"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VersionStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class TakeStatus(StrEnum):
    GENERATED = "GENERATED"
    QC_REQUIRED = "QC_REQUIRED"
    QC_PASSED = "QC_PASSED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class TakeApproval(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class IdentityStatus(StrEnum):
    PASSED = "PASSED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


PROJECT_STATUS_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.DRAFT: frozenset(
        {ProjectStatus.PROPOSAL_RUNNING, ProjectStatus.BLOCKED, ProjectStatus.ARCHIVED}
    ),
    ProjectStatus.PROPOSAL_RUNNING: frozenset(
        {ProjectStatus.DRAFT, ProjectStatus.PROPOSAL_READY, ProjectStatus.BLOCKED}
    ),
    ProjectStatus.PROPOSAL_READY: frozenset(
        {
            ProjectStatus.PROPOSAL_RUNNING,
            ProjectStatus.STORY_STRUCTURE_RUNNING,
            ProjectStatus.STORY_PACKAGE_RUNNING,
            ProjectStatus.STORY_APPROVED,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.STORY_STRUCTURE_RUNNING: frozenset(
        {
            ProjectStatus.PROPOSAL_READY,
            ProjectStatus.RELATIONSHIP_READY,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.RELATIONSHIP_READY: frozenset(
        {
            ProjectStatus.STORY_STRUCTURE_RUNNING,
            ProjectStatus.CHARACTER_VISUAL_READY,
            ProjectStatus.SCRIPT_PACKAGE_RUNNING,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.CHARACTER_VISUAL_READY: frozenset(
        {
            ProjectStatus.RELATIONSHIP_READY,
            ProjectStatus.SCRIPT_PACKAGE_RUNNING,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.SCRIPT_PACKAGE_RUNNING: frozenset(
        {
            ProjectStatus.CHARACTER_VISUAL_READY,
            ProjectStatus.RELATIONSHIP_READY,
            ProjectStatus.SCRIPT_READY,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.STORY_PACKAGE_RUNNING: frozenset(
        {ProjectStatus.PROPOSAL_READY, ProjectStatus.SCRIPT_READY, ProjectStatus.BLOCKED}
    ),
    ProjectStatus.SCRIPT_READY: frozenset(
        {
            ProjectStatus.STORY_PACKAGE_RUNNING,
            ProjectStatus.STORY_APPROVED,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.STORY_APPROVED: frozenset(
        {
            ProjectStatus.PREPRODUCTION_READY,
            ProjectStatus.CHARACTER_LOCKED,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.PREPRODUCTION_READY: frozenset(
        {
            ProjectStatus.STORY_APPROVED,
            ProjectStatus.PREPRODUCTION_APPROVED,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.PREPRODUCTION_APPROVED: frozenset(
        {
            ProjectStatus.STORYBOARD_READY,
            ProjectStatus.CHARACTER_LOCKED,
            ProjectStatus.PRODUCING,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.STORYBOARD_READY: frozenset(
        {
            ProjectStatus.PREPRODUCTION_APPROVED,
            ProjectStatus.STORYBOARD_APPROVED,
            ProjectStatus.BLOCKED,
        }
    ),
    ProjectStatus.STORYBOARD_APPROVED: frozenset({ProjectStatus.PRODUCING, ProjectStatus.BLOCKED}),
    ProjectStatus.CHARACTER_LOCKED: frozenset({ProjectStatus.PRODUCING, ProjectStatus.BLOCKED}),
    ProjectStatus.PRODUCING: frozenset({ProjectStatus.PREVIEW_READY, ProjectStatus.BLOCKED}),
    ProjectStatus.PREVIEW_READY: frozenset(
        {ProjectStatus.PRODUCING, ProjectStatus.APPROVED, ProjectStatus.BLOCKED}
    ),
    ProjectStatus.APPROVED: frozenset(
        {ProjectStatus.PRODUCING, ProjectStatus.EXPORTING, ProjectStatus.BLOCKED}
    ),
    ProjectStatus.EXPORTING: frozenset(
        {ProjectStatus.APPROVED, ProjectStatus.EXPORTED, ProjectStatus.BLOCKED}
    ),
    ProjectStatus.EXPORTED: frozenset(
        {ProjectStatus.PRODUCING, ProjectStatus.EXPORTING, ProjectStatus.ARCHIVED}
    ),
    ProjectStatus.BLOCKED: frozenset(
        {
            ProjectStatus.DRAFT,
            ProjectStatus.PROPOSAL_READY,
            ProjectStatus.RELATIONSHIP_READY,
            ProjectStatus.PRODUCING,
            ProjectStatus.PREVIEW_READY,
            ProjectStatus.APPROVED,
            ProjectStatus.ARCHIVED,
        }
    ),
    ProjectStatus.ARCHIVED: frozenset(),
}


def can_transition_project_status(current: str, target: str) -> bool:
    try:
        current_status = ProjectStatus(current)
        target_status = ProjectStatus(target)
    except ValueError:
        return False
    return target_status in PROJECT_STATUS_TRANSITIONS[current_status]
