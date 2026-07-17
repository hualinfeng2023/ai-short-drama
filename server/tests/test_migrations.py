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
        item["name"]
        for item in inspector.get_columns("character_family_resemblance_constraints")
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
    assert {item["name"] for item in inspector.get_columns("change_sets")} >= {
        "base_relationship_graph_id",
        "result_relationship_graph_id",
    }
    assert revision == "0024_family_resemblance_constraints"
    if platform_targets is not None:
        assert '"priority":"PRIMARY"' in platform_targets
    command.check(config)
