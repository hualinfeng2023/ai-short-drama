from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import SERVER_ROOT


def test_v1_upgrade_recovers_from_partial_sqlite_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "migration-recovery"
    data_dir.mkdir()
    database_url = f"sqlite:///{data_dir / 'app.db'}"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(SERVER_ROOT / "alembic.ini"))

    command.upgrade(config, "0001_read_only_baseline")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN target_platform VARCHAR(40) "
                "DEFAULT 'douyin' NOT NULL"
            )
        )
        connection.execute(
            text("ALTER TABLE projects ADD COLUMN lock_version INTEGER DEFAULT 1 NOT NULL")
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {item["name"] for item in inspector.get_columns("projects")} >= {
        "target_platform",
        "lock_version",
        "created_at",
    }
    assert {
        "assets",
        "brief_versions",
        "characters",
        "event_log",
        "idempotency_keys",
        "proposal_versions",
        "story_versions",
        "timeline_versions",
        "worker_state",
        "proposal_batches",
        "story_bible_versions",
        "episode_outline_versions",
        "script_versions",
        "script_scenes",
        "script_lines",
        "script_excerpt_revisions",
        "dependency_edges",
        "character_look_versions",
        "voice_profiles",
        "location_versions",
        "prop_versions",
        "visual_bible_versions",
        "workflow_runs",
        "workflow_nodes",
        "job_dependencies",
        "review_gates",
        "storyboard_versions",
        "shot_specs",
        "generation_records",
        "quality_checks",
        "review_records",
        "sound_brief_versions",
        "audio_cues",
        "audio_takes",
        "lip_sync_takes",
        "timeline_tracks",
        "timeline_clips",
        "whole_film_quality_checks",
        "export_profiles",
        "rights_preflights",
        "export_artifacts",
        "relationship_graph_versions",
        "relationship_edges",
        "relationship_beats",
        "character_visual_profile_versions",
        "character_candidate_batches",
        "character_identity_versions",
        "character_identity_assets",
        "character_story_state_versions",
        "character_family_resemblance_constraints",
    } <= set(inspector.get_table_names())
    assert {item["name"] for item in inspector.get_columns("assets")} >= {
        "original_filename",
        "metadata_json",
        "rights_status",
    }
    assert {item["name"] for item in inspector.get_columns("takes")} >= {
        "identity_review_decision",
        "identity_review_issues_json",
        "identity_review_actor",
        "identity_reviewed_at",
        "generation_record_id",
        "quality_status",
    }
    assert {item["name"] for item in inspector.get_columns("brief_versions")} >= {
        "narrative_protagonist",
        "target_audience",
        "emotional_rewards_json",
        "audience_profile",
        "production_format",
        "primary_audience",
        "secondary_audiences_json",
        "primary_market",
        "secondary_markets_json",
        "canonical_language",
        "localization_targets_json",
        "platform_targets_json",
        "content_requirements_json",
        "content_avoidances_json",
        "creative_defaults_json",
        "blocking_questions_json",
        "payload_schema_version",
    }
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        platform_targets = connection.execute(
            text("SELECT platform_targets_json FROM brief_versions LIMIT 1")
        ).scalar_one_or_none()
    assert {item["name"] for item in inspector.get_columns("timeline_versions")} >= {
        "stems_manifest_asset_id",
        "qc_report_asset_id",
    }
    assert {item["name"] for item in inspector.get_columns("exports")} >= {
        "export_profile_id",
        "language",
        "rights_preflight_id",
        "picture_master_asset_id",
        "cover_asset_id",
        "stems_manifest_asset_id",
        "qc_report_asset_id",
    }
    assert {item["name"] for item in inspector.get_columns("relationship_graph_versions")} >= {
        "story_bible_version_id",
        "parent_version_id",
        "schema_version",
        "content_hash",
        "lock_version",
        "approved_at",
        "approved_by",
    }
    assert {item["name"] for item in inspector.get_columns("relationship_edges")} >= {
        "relationship_key",
        "character_pair_key",
        "source_character_key",
        "target_character_key",
        "surface_relationship",
        "true_relationship",
        "conflict_intensity",
        "locked",
        "family_kinship_json",
    }
    assert {
        "relationship_graph_version_id",
        "source_identity_version_ids_json",
        "inherited_features_json",
        "similarity_level",
        "temperament_affinity_json",
        "independence_constraints_json",
        "status",
    } <= {
        item["name"] for item in inspector.get_columns("character_family_resemblance_constraints")
    }
    assert "family_constraint_version_id" in {
        item["name"] for item in inspector.get_columns("character_candidate_batches")
    }
    assert {item["name"] for item in inspector.get_columns("relationship_beats")} >= {
        "relationship_edge_id",
        "episode_ordinal",
        "sequence",
        "trigger_type",
        "before_state_json",
        "after_state_json",
        "audience_visibility",
    }
    assert {item["name"] for item in inspector.get_columns("episode_outline_versions")} >= {
        "relationship_graph_version_id"
    }
    assert {item["name"] for item in inspector.get_columns("script_versions")} >= {
        "relationship_graph_version_id"
    }
    assert {item["name"] for item in inspector.get_columns("script_excerpt_revisions")} >= {
        "base_script_version_id",
        "base_line_id",
        "parent_revision_id",
        "applied_script_version_id",
        "selection_start",
        "selection_end",
        "original_text",
        "proposed_text",
        "action",
        "status",
    }
    assert {item["name"] for item in inspector.get_columns("change_sets")} >= {
        "base_relationship_graph_id",
        "result_relationship_graph_id",
    }
    assert {item["name"] for item in inspector.get_columns("dependency_edges")} >= {
        "project_id",
        "change_set_id",
        "source_type",
        "source_id",
        "source_version_id",
        "target_type",
        "target_id",
        "target_version_id",
        "relation",
        "evidence",
        "inferred",
    }
    assert {
        "ix_dependency_edges_change_set_id",
        "ix_dependency_edges_project_source",
        "ix_dependency_edges_project_target",
    } <= {item["name"] for item in inspector.get_indexes("dependency_edges")}
    assert revision == "0026_dependency_edges"
    if platform_targets is not None:
        assert '"priority":"PRIMARY"' in platform_targets
    command.check(config)


def test_dependency_edge_migration_backfills_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "dependency-edge-migration"
    data_dir.mkdir()
    database_url = f"sqlite:///{data_dir / 'app.db'}"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(SERVER_ROOT / "alembic.ini"))
    command.upgrade(config, "0025_script_excerpt_revisions")
    engine = create_engine(database_url)
    project_id = "10000000-0000-4000-8000-000000000001"
    change_set_id = "20000000-0000-4000-8000-000000000001"
    impact = (
        '{"impact":{"dependency_edges":[{"source":{"type":"ScriptScene",'
        '"id":"30000000-0000-4000-8000-000000000001"},'
        '"target":{"type":"ShotSpec","id":"40000000-0000-4000-8000-000000000001"},'
        '"relation":"SPECIFIES","evidence":"shot_specs.script_scene_id","inferred":false}]}}'
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (
                    id, name, idea, genre, style, target_duration_sec, aspect_ratio,
                    target_platform, status, lock_version, available_points,
                    timeline_version, preview_approved, export_ready, created_at, updated_at
                ) VALUES (
                    :id, '迁移测试项目', '验证依赖边回填', 'drama', 'cinematic', 60, '9:16',
                    'douyin', 'DRAFT', 1, 100, 1, 0, 0,
                    '2026-07-26T00:00:00Z', '2026-07-26T00:00:00Z'
                )
                """
            ),
            {"id": project_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO change_sets (
                    id, project_id, scope_json, instruction, impact_json, estimate_json,
                    status, created_at
                ) VALUES (
                    :id, :project_id, '{}', '迁移回填测试', :impact, '{}',
                    'PROPOSED', '2026-07-26T00:00:00Z'
                )
                """
            ),
            {"id": change_set_id, "project_id": project_id, "impact": impact},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        edge = connection.execute(
            text(
                """
                SELECT project_id, change_set_id, source_type, target_type, relation,
                       evidence, inferred
                FROM dependency_edges
                """
            )
        ).mappings().one()
    assert dict(edge) == {
        "project_id": project_id,
        "change_set_id": change_set_id,
        "source_type": "ScriptScene",
        "target_type": "ShotSpec",
        "relation": "SPECIFIES",
        "evidence": "shot_specs.script_scene_id",
        "inferred": 0,
    }

    command.downgrade(config, "0025_script_excerpt_revisions")
    assert "dependency_edges" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM dependency_edges")).scalar_one() == 1
