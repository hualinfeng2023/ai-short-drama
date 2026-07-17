from app.domain.statuses import (
    PROJECT_STATUS_TRANSITIONS,
    ProjectStatus,
    can_transition_project_status,
)


def test_every_project_status_has_an_explicit_transition_contract() -> None:
    assert set(PROJECT_STATUS_TRANSITIONS) == set(ProjectStatus)
    assert PROJECT_STATUS_TRANSITIONS[ProjectStatus.ARCHIVED] == frozenset()


def test_current_happy_path_and_recovery_transitions_are_declared() -> None:
    assert can_transition_project_status("DRAFT", "PROPOSAL_RUNNING")
    assert can_transition_project_status("PROPOSAL_RUNNING", "PROPOSAL_READY")
    assert can_transition_project_status("PROPOSAL_RUNNING", "DRAFT")
    assert can_transition_project_status("PROPOSAL_READY", "STORY_STRUCTURE_RUNNING")
    assert can_transition_project_status("STORY_STRUCTURE_RUNNING", "RELATIONSHIP_READY")
    assert can_transition_project_status("RELATIONSHIP_READY", "SCRIPT_PACKAGE_RUNNING")
    assert can_transition_project_status("SCRIPT_PACKAGE_RUNNING", "SCRIPT_READY")
    assert can_transition_project_status("PROPOSAL_READY", "STORY_APPROVED")
    assert can_transition_project_status("STORY_APPROVED", "CHARACTER_LOCKED")
    assert can_transition_project_status("CHARACTER_LOCKED", "PRODUCING")
    assert can_transition_project_status("PRODUCING", "PREVIEW_READY")
    assert can_transition_project_status("PREVIEW_READY", "APPROVED")
    assert can_transition_project_status("APPROVED", "EXPORTING")
    assert can_transition_project_status("EXPORTING", "EXPORTED")


def test_unknown_or_backward_transition_is_rejected() -> None:
    assert not can_transition_project_status("DRAFT", "APPROVED")
    assert not can_transition_project_status("ARCHIVED", "DRAFT")
    assert not can_transition_project_status("NOT_REAL", "DRAFT")
