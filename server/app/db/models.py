from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    idea: Mapped[str] = mapped_column(Text)
    genre: Mapped[str] = mapped_column(String(80))
    style: Mapped[str] = mapped_column(String(80))
    target_duration_sec: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(8))
    target_platform: Mapped[str] = mapped_column(String(40), default="douyin")
    status: Mapped[str] = mapped_column(String(32), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    available_points: Mapped[int] = mapped_column(Integer)
    timeline_version: Mapped[int] = mapped_column(Integer, default=1)
    preview_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    export_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    current_story_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_timeline_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    episodes: Mapped[list["Episode"]] = relationship(back_populates="project")
    jobs: Mapped[list["Job"]] = relationship(back_populates="project")
    brief_versions: Mapped[list["BriefVersion"]] = relationship(back_populates="project")
    proposal_versions: Mapped[list["ProposalVersion"]] = relationship(back_populates="project")


class BriefVersion(Base):
    __tablename__ = "brief_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    project_name: Mapped[str] = mapped_column(String(120))
    raw_input: Mapped[str] = mapped_column(Text)
    genre: Mapped[str] = mapped_column(String(80))
    style: Mapped[str] = mapped_column(String(80))
    target_duration_sec: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(8))
    target_platform: Mapped[str] = mapped_column(String(40))
    reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    assumptions_json: Mapped[str] = mapped_column(Text, default="[]")
    narrative_protagonist: Mapped[str] = mapped_column(String(24), default="unspecified")
    target_audience: Mapped[str] = mapped_column(String(24), default="general")
    emotional_rewards_json: Mapped[str] = mapped_column(Text, default="[]")
    audience_profile: Mapped[str] = mapped_column(String(240), default="")
    production_format: Mapped[str] = mapped_column(String(32), default="live_action")
    primary_audience: Mapped[str] = mapped_column(String(80), default="general")
    secondary_audiences_json: Mapped[str] = mapped_column(Text, default="[]")
    primary_market: Mapped[str] = mapped_column(String(16), default="CN")
    secondary_markets_json: Mapped[str] = mapped_column(Text, default="[]")
    canonical_language: Mapped[str] = mapped_column(String(24), default="zh-CN")
    localization_targets_json: Mapped[str] = mapped_column(Text, default="[]")
    platform_targets_json: Mapped[str] = mapped_column(Text, default="[]")
    content_requirements_json: Mapped[str] = mapped_column(Text, default="[]")
    content_avoidances_json: Mapped[str] = mapped_column(Text, default="[]")
    creative_defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    blocking_questions_json: Mapped[str] = mapped_column(Text, default="[]")
    payload_schema_version: Mapped[str] = mapped_column(String(32), default="brief-v3")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="brief_versions")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), index=True)
    key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProposalVersion(Base):
    __tablename__ = "proposal_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    brief_version: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    direction_key: Mapped[str] = mapped_column(String(40), default="legacy")
    source_proposal_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="story-direction-v1")
    generation_evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-v1")
    config_version: Mapped[str] = mapped_column(String(48), default="proposal-v1")
    status: Mapped[str] = mapped_column(String(32), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="proposal_versions")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(120))
    target_duration_sec: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)

    project: Mapped[Project] = relationship(back_populates="episodes")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="episode")


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    code: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)

    episode: Mapped[Episode] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(back_populates="scene")


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True)
    code: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    dialogue: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    shot_size: Mapped[str] = mapped_column(String(24))
    camera_movement: Mapped[str] = mapped_column(String(24))
    current_take: Mapped[int] = mapped_column(Integer, default=1)
    candidate_take: Mapped[int | None] = mapped_column(Integer, nullable=True)
    continuity: Mapped[str] = mapped_column(String(24))
    location: Mapped[str] = mapped_column(String(120))
    time_of_day: Mapped[str] = mapped_column(String(40))
    current_take_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    character_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    character_look_version: Mapped[str] = mapped_column(String(40), default="Look V1")
    character_identity_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    character_look_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    character_story_state_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    scene: Mapped[Scene] = relationship(back_populates="shots")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "available_at", "priority"),
        Index("ix_jobs_idempotency_key", "idempotency_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(220))
    request_hash: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(160))
    entity: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), index=True)
    progress: Mapped[float] = mapped_column(Float)
    stage: Mapped[str] = mapped_column(String(160))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_label: Mapped[str] = mapped_column("created_at", String(40), default="")
    created_at: Mapped[datetime] = mapped_column("created_at_utc", DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(36))
    estimated_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="jobs")


class EventLog(Base):
    __tablename__ = "event_log"
    __table_args__ = (Index("ix_event_log_project_sequence", "project_id", "sequence"),)

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WorkerState(Base):
    __tablename__ = "worker_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class StoryVersion(Base):
    __tablename__ = "story_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    proposal_version: Mapped[int] = mapped_column(Integer)
    source_proposal_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="story-dna-v1")
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-text-v2")
    config_version: Mapped[str] = mapped_column(String(48), default="story-package-v1")
    title: Mapped[str] = mapped_column(String(160))
    logline: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProposalBatch(Base):
    __tablename__ = "proposal_batches"
    __table_args__ = (UniqueConstraint("project_id", "brief_version", "config_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    brief_version: Mapped[int] = mapped_column(Integer)
    config_version: Mapped[str] = mapped_column(String(48))
    provider: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoryBibleVersion(Base):
    __tablename__ = "story_bible_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    story_version_id: Mapped[str] = mapped_column(ForeignKey("story_versions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="story-bible-v1")
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-text-v2")
    config_version: Mapped[str] = mapped_column(String(48), default="story-package-v1")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipGraphVersion(Base):
    __tablename__ = "relationship_graph_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    story_bible_version_id: Mapped[str] = mapped_column(
        ForeignKey("story_bible_versions.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="relationship-graph-v1")
    config_version: Mapped[str] = mapped_column(String(48), default="relationship-graph-v1")
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-text-v2")
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RelationshipEdge(Base):
    __tablename__ = "relationship_edges"
    __table_args__ = (
        UniqueConstraint("graph_version_id", "relationship_key"),
        UniqueConstraint("graph_version_id", "character_pair_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("relationship_graph_versions.id", ondelete="CASCADE"), index=True
    )
    relationship_key: Mapped[str] = mapped_column(String(80))
    character_pair_key: Mapped[str] = mapped_column(String(161))
    source_character_key: Mapped[str] = mapped_column(String(80), index=True)
    target_character_key: Mapped[str] = mapped_column(String(80), index=True)
    directionality: Mapped[str] = mapped_column(String(24))
    relationship_types_json: Mapped[str] = mapped_column(Text, default="[]")
    family_kinship_json: Mapped[str] = mapped_column(Text, default="{}")
    surface_relationship: Mapped[str] = mapped_column(Text)
    true_relationship: Mapped[str] = mapped_column(Text)
    source_view_json: Mapped[str] = mapped_column(Text, default="{}")
    target_view_json: Mapped[str] = mapped_column(Text, default="{}")
    trust_level: Mapped[int] = mapped_column(Integer, default=0)
    emotional_temperature: Mapped[int] = mapped_column(Integer, default=0)
    power_balance: Mapped[int] = mapped_column(Integer, default=0)
    conflict_intensity: Mapped[int] = mapped_column(Integer, default=0)
    story_function: Mapped[str] = mapped_column(Text)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    ordinal: Mapped[int] = mapped_column(Integer)


class RelationshipBeat(Base):
    __tablename__ = "relationship_beats"
    __table_args__ = (UniqueConstraint("relationship_edge_id", "episode_ordinal", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("relationship_graph_versions.id", ondelete="CASCADE"), index=True
    )
    relationship_edge_id: Mapped[str] = mapped_column(
        ForeignKey("relationship_edges.id", ondelete="CASCADE"), index=True
    )
    episode_ordinal: Mapped[int] = mapped_column(Integer, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    scene_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    trigger_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    before_state_json: Mapped[str] = mapped_column(Text)
    after_state_json: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    emotional_consequence: Mapped[str] = mapped_column(Text)
    audience_visibility: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer)


class EpisodeOutlineVersion(Base):
    __tablename__ = "episode_outline_versions"
    __table_args__ = (UniqueConstraint("project_id", "episode_ordinal", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    story_bible_version_id: Mapped[str] = mapped_column(
        ForeignKey("story_bible_versions.id"), index=True
    )
    relationship_graph_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), nullable=True, index=True
    )
    episode_ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="episode-outline-v1")
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-text-v2")
    config_version: Mapped[str] = mapped_column(String(48), default="story-package-v1")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("project_id", "episode_ordinal", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    outline_version_id: Mapped[str] = mapped_column(
        ForeignKey("episode_outline_versions.id"), index=True
    )
    relationship_graph_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), nullable=True, index=True
    )
    episode_ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    critic_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="script-v1")
    canonical_language: Mapped[str] = mapped_column(String(24), default="zh-CN")
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    model: Mapped[str] = mapped_column(String(80), default="deterministic-text-v2")
    config_version: Mapped[str] = mapped_column(String(48), default="script-v1")
    estimated_duration_ms: Mapped[int] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScriptScene(Base):
    __tablename__ = "script_scenes"
    __table_args__ = (UniqueConstraint("script_version_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(180))
    location: Mapped[str] = mapped_column(String(120))
    time_of_day: Mapped[str] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str] = mapped_column(String(80))
    duration_ms: Mapped[int] = mapped_column(Integer)
    bgm_intent: Mapped[str] = mapped_column(Text, default="")
    sfx_intent_json: Mapped[str] = mapped_column(Text, default="[]")


class ScriptLine(Base):
    __tablename__ = "script_lines"
    __table_args__ = (UniqueConstraint("script_scene_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    script_scene_id: Mapped[str] = mapped_column(ForeignKey("script_scenes.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    speaker_key: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    line_type: Mapped[str] = mapped_column(String(32), default="DIALOGUE")
    emotion: Mapped[str] = mapped_column(String(80), default="neutral")
    speech_rate: Mapped[float] = mapped_column(Float, default=1.0)
    pause_after_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_duration_ms: Mapped[int] = mapped_column(Integer)
    pronunciation_json: Mapped[str] = mapped_column(Text, default="{}")
    localization_json: Mapped[str] = mapped_column(Text, default="{}")


class ScriptExcerptRevision(Base):
    __tablename__ = "script_excerpt_revisions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "episode_ordinal",
            "scene_ordinal",
            "line_ordinal",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_script_version_id: Mapped[str] = mapped_column(
        ForeignKey("script_versions.id"), index=True
    )
    base_line_id: Mapped[str] = mapped_column(ForeignKey("script_lines.id"), index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("script_excerpt_revisions.id"), nullable=True, index=True
    )
    applied_script_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("script_versions.id"), nullable=True, index=True
    )
    episode_ordinal: Mapped[int] = mapped_column(Integer)
    scene_ordinal: Mapped[int] = mapped_column(Integer)
    line_ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    selection_start: Mapped[int] = mapped_column(Integer)
    selection_end: Mapped[int] = mapped_column(Integer)
    original_text: Mapped[str] = mapped_column(Text)
    proposed_text: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(32), index=True)
    custom_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("project_id", "sha256", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    storage_key: Mapped[str] = mapped_column(String(320))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(48))
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    rights_status: Mapped[str] = mapped_column(String(32), default="RESTRICTED_DEMO")
    source_entity_type: Mapped[str] = mapped_column(String(48))
    source_entity_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("project_id", "character_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    visual_brief: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    locked_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_story_bible_version_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    source_relationship_graph_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    current_profile_version_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    locked_identity_version_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    active_look_version_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    active_story_state_version_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterVisualProfileVersion(Base):
    __tablename__ = "character_visual_profile_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source_story_bible_version_id: Mapped[str] = mapped_column(
        ForeignKey("story_bible_versions.id"), index=True
    )
    source_relationship_graph_id: Mapped[str] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), index=True
    )
    identity_fields_json: Mapped[str] = mapped_column(Text, default="{}")
    appearance_fields_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_visualization_json: Mapped[str] = mapped_column(Text, default="{}")
    styling_fields_json: Mapped[str] = mapped_column(Text, default="{}")
    project_style_json: Mapped[str] = mapped_column(Text, default="{}")
    negative_constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    conflict_report_json: Mapped[str] = mapped_column(Text, default="[]")
    recommended_directions_json: Mapped[str] = mapped_column(Text, default="[]")
    selected_direction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_content_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterFamilyResemblanceConstraint(Base):
    __tablename__ = "character_family_resemblance_constraints"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    relationship_graph_version_id: Mapped[str] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    source_character_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_identity_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    relationship_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    inherited_features_json: Mapped[str] = mapped_column(Text, default="[]")
    similarity_level: Mapped[str] = mapped_column(String(32))
    temperament_affinity_json: Mapped[str] = mapped_column(Text, default="{}")
    independence_constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterCandidateBatch(Base):
    __tablename__ = "character_candidate_batches"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("character_visual_profile_versions.id"), index=True
    )
    family_constraint_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("character_family_resemblance_constraints.id"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    requested_count: Mapped[int] = mapped_column(Integer, default=3)
    composition: Mapped[str] = mapped_column(String(40), default="FRONT_BUST")
    status: Mapped[str] = mapped_column(String(32), index=True)
    prompt_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterIdentityVersion(Base):
    __tablename__ = "character_identity_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("character_candidates.id"), index=True
    )
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("character_visual_profile_versions.id"), index=True
    )
    stable_traits_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterIdentityAsset(Base):
    __tablename__ = "character_identity_assets"
    __table_args__ = (UniqueConstraint("identity_version_id", "view_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    identity_version_id: Mapped[str] = mapped_column(
        ForeignKey("character_identity_versions.id"), index=True
    )
    view_type: Mapped[str] = mapped_column(String(40))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterStoryStateVersion(Base):
    __tablename__ = "character_story_state_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    identity_version_id: Mapped[str] = mapped_column(
        ForeignKey("character_identity_versions.id"), index=True
    )
    look_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("character_look_versions.id"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CharacterLookVersion(Base):
    __tablename__ = "character_look_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    identity_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("character_identity_versions.id"), nullable=True, index=True
    )
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))
    usage_scope: Mapped[str] = mapped_column(String(80), default="GLOBAL")
    payload_json: Mapped[str] = mapped_column(Text)
    reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    change_reason: Mapped[str] = mapped_column(String(160), default="角色身份锁定后的基础造型")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (UniqueConstraint("character_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(48), default="mock")
    voice_key: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str] = mapped_column(Text)
    pronunciation_json: Mapped[str] = mapped_column(Text, default="{}")
    consent_status: Mapped[str] = mapped_column(String(32), default="SYNTHETIC_ALLOWED")
    cloning_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LocationVersion(Base):
    __tablename__ = "location_versions"
    __table_args__ = (UniqueConstraint("project_id", "location_key", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    location_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str] = mapped_column(Text)
    reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PropVersion(Base):
    __tablename__ = "prop_versions"
    __table_args__ = (UniqueConstraint("project_id", "prop_key", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    prop_key: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str] = mapped_column(Text)
    reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VisualBibleVersion(Base):
    __tablename__ = "visual_bible_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    character_look_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    location_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    prop_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    voice_profile_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workflow_type: Mapped[str] = mapped_column(String(64))
    source_entity_type: Mapped[str] = mapped_column(String(48))
    source_entity_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_gate: Mapped[str | None] = mapped_column(String(40), nullable=True)
    config_version: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowNode(Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (UniqueConstraint("workflow_run_id", "node_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(80))
    node_type: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    dependency_keys_json: Mapped[str] = mapped_column(Text, default="[]")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobDependency(Base):
    __tablename__ = "job_dependencies"
    __table_args__ = (UniqueConstraint("job_id", "depends_on_job_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    depends_on_job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    dependency_type: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewGate(Base):
    __tablename__ = "review_gates"
    __table_args__ = (UniqueConstraint("workflow_run_id", "gate_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    gate_key: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoryboardVersion(Base):
    __tablename__ = "storyboard_versions"
    __table_args__ = (UniqueConstraint("project_id", "episode_ordinal", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"), index=True)
    visual_bible_version_id: Mapped[str] = mapped_column(
        ForeignKey("visual_bible_versions.id"), index=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    episode_ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    animatic_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShotSpec(Base):
    __tablename__ = "shot_specs"
    __table_args__ = (UniqueConstraint("storyboard_version_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storyboard_version_id: Mapped[str] = mapped_column(
        ForeignKey("storyboard_versions.id"), index=True
    )
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id"), unique=True, index=True)
    script_scene_id: Mapped[str] = mapped_column(ForeignKey("script_scenes.id"), index=True)
    script_line_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    ordinal: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    dialogue: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    shot_size: Mapped[str] = mapped_column(String(24))
    camera_movement: Mapped[str] = mapped_column(String(24))
    character_look_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    location_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prop_version_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    prompt_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)


class CharacterCandidate(Base):
    __tablename__ = "character_candidates"
    __table_args__ = (UniqueConstraint("character_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("character_candidate_batches.id"), nullable=True, index=True
    )
    profile_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("character_visual_profile_versions.id"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    seed: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), index=True)
    prompt_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    review_status: Mapped[str] = mapped_column(String(32), default="PENDING_SELECTION")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Take(Base):
    __tablename__ = "takes"
    __table_args__ = (
        UniqueConstraint("shot_id", "kind", "version"),
        Index(
            "ix_takes_one_current_per_shot_kind",
            "shot_id",
            "kind",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    status: Mapped[str] = mapped_column(String(32), index=True)
    approval: Mapped[str] = mapped_column(String(32), index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_take_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    quality_status: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED")
    identity_status: Mapped[str] = mapped_column(String(32), default="NOT_APPLICABLE")
    identity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    identity_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    identity_review_decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identity_review_issues_json: Mapped[str] = mapped_column(Text, default="[]")
    identity_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_review_actor: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identity_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    identity_review_look_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GenerationRecord(Base):
    __tablename__ = "generation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    capability: Mapped[str] = mapped_column(String(48), index=True)
    provider: Mapped[str] = mapped_column(String(48))
    model: Mapped[str] = mapped_column(String(80))
    config_version: Mapped[str] = mapped_column(String(48))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    seed: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_asset_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QualityCheck(Base):
    __tablename__ = "quality_checks"
    __table_args__ = (UniqueConstraint("generation_record_id", "check_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    generation_record_id: Mapped[str] = mapped_column(
        ForeignKey("generation_records.id"), index=True
    )
    check_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewRecord(Base):
    __tablename__ = "review_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(48), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    gate_key: Mapped[str] = mapped_column(String(40), index=True)
    risk_level: Mapped[str] = mapped_column(String(24), default="LOW")
    status: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SoundBriefVersion(Base):
    __tablename__ = "sound_brief_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    storyboard_version_id: Mapped[str] = mapped_column(
        ForeignKey("storyboard_versions.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    rights_status: Mapped[str] = mapped_column(String(32))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AudioCue(Base):
    __tablename__ = "audio_cues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    storyboard_version_id: Mapped[str] = mapped_column(
        ForeignKey("storyboard_versions.id"), index=True
    )
    script_line_id: Mapped[str | None] = mapped_column(
        ForeignKey("script_lines.id"), nullable=True, index=True
    )
    script_scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("script_scenes.id"), nullable=True, index=True
    )
    shot_id: Mapped[str | None] = mapped_column(ForeignKey("shots.id"), nullable=True, index=True)
    voice_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_profiles.id"), nullable=True
    )
    cue_type: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AudioTake(Base):
    __tablename__ = "audio_takes"
    __table_args__ = (
        UniqueConstraint("audio_cue_id", "version"),
        Index(
            "ix_audio_takes_one_current_per_cue",
            "audio_cue_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    audio_cue_id: Mapped[str] = mapped_column(ForeignKey("audio_cues.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    generation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_records.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    approval: Mapped[str] = mapped_column(String(32), index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_status: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LipSyncTake(Base):
    __tablename__ = "lip_sync_takes"
    __table_args__ = (UniqueConstraint("shot_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    video_take_id: Mapped[str] = mapped_column(ForeignKey("takes.id"))
    audio_take_id: Mapped[str] = mapped_column(ForeignKey("audio_takes.id"))
    version: Mapped[int] = mapped_column(Integer)
    output_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_records.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    approval: Mapped[str] = mapped_column(String(32), index=True)
    fallback_strategy: Mapped[str | None] = mapped_column(String(48), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(32), default="NOT_CHECKED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TimelineVersion(Base):
    __tablename__ = "timeline_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    mp4_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    srt_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    vtt_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    manifest_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    stems_manifest_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    qc_report_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    baseline_hash: Mapped[str] = mapped_column(String(64))
    parent_timeline_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TimelineTrack(Base):
    __tablename__ = "timeline_tracks"
    __table_args__ = (UniqueConstraint("timeline_id", "track_type", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"), index=True)
    track_type: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    gain_db: Mapped[float] = mapped_column(Float, default=0.0)
    stem_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TimelineClip(Base):
    __tablename__ = "timeline_clips"
    __table_args__ = (UniqueConstraint("track_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("timeline_tracks.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_entity_type: Mapped[str] = mapped_column(String(48))
    source_entity_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    source_in_ms: Mapped[int] = mapped_column(Integer, default=0)
    source_out_ms: Mapped[int] = mapped_column(Integer)
    gain_db: Mapped[float] = mapped_column(Float, default=0.0)
    transition_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WholeFilmQualityCheck(Base):
    __tablename__ = "whole_film_quality_checks"
    __table_args__ = (UniqueConstraint("timeline_id", "check_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"), index=True)
    check_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TimelineItem(Base):
    __tablename__ = "timeline_items"
    __table_args__ = (UniqueConstraint("timeline_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id"), index=True)
    take_id: Mapped[str] = mapped_column(ForeignKey("takes.id"))
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)


class ChangeSet(Base):
    __tablename__ = "change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_timeline_id: Mapped[str | None] = mapped_column(
        ForeignKey("timeline_versions.id"), nullable=True
    )
    base_relationship_graph_id: Mapped[str | None] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), nullable=True, index=True
    )
    scope_json: Mapped[str] = mapped_column(Text)
    instruction: Mapped[str] = mapped_column(Text)
    impact_json: Mapped[str] = mapped_column(Text)
    estimate_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    result_timeline_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_relationship_graph_id: Mapped[str | None] = mapped_column(
        ForeignKey("relationship_graph_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExportRecord(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"))
    status: Mapped[str] = mapped_column(String(32), index=True)
    profile: Mapped[str] = mapped_column(String(48))
    export_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    language: Mapped[str] = mapped_column(String(24), default="zh-CN", index=True)
    rights_preflight_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    picture_master_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mp4_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    srt_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vtt_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    manifest_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cover_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stems_manifest_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    qc_report_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rights_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExportProfile(Base):
    __tablename__ = "export_profiles"
    __table_args__ = (UniqueConstraint("project_id", "name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(40), index=True)
    aspect_ratio: Mapped[str] = mapped_column(String(8))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    caption_mode: Mapped[str] = mapped_column(String(24))
    languages_json: Mapped[str] = mapped_column(Text, default="[]")
    audio_tracks_json: Mapped[str] = mapped_column(Text, default="[]")
    watermark_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RightsPreflight(Base):
    __tablename__ = "rights_preflights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timeline_versions.id"), index=True)
    export_profile_id: Mapped[str] = mapped_column(ForeignKey("export_profiles.id"), index=True)
    language: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(32), index=True)
    blockers_json: Mapped[str] = mapped_column(Text, default="[]")
    checks_json: Mapped[str] = mapped_column(Text, default="[]")
    policy_version: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"
    __table_args__ = (UniqueConstraint("export_id", "artifact_type", "language"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    export_id: Mapped[str] = mapped_column(ForeignKey("exports.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40), index=True)
    language: Mapped[str] = mapped_column(String(24), default="und")
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    reused_from_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageLedger(Base):
    __tablename__ = "usage_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(32), index=True)
    points: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(36))
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
