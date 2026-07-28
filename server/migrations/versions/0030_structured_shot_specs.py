"""Make structured ShotSpec the source of truth for storyboard generation.

Revision ID: 0030_structured_shot_specs
Revises: 0029_legacy_take_provenance
Create Date: 2026-07-28
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0030_structured_shot_specs"
down_revision: str | None = "0029_legacy_take_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json(raw: object, fallback: object) -> object:
    if not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _legacy_structured_spec(row: sa.RowMapping) -> tuple[dict[str, object], dict[str, object]]:
    prompt = _json(row["prompt_json"], {})
    prompt = prompt if isinstance(prompt, dict) else {}
    character_ids = _json(row["character_ids_json"], [])
    character_ids = (
        [item for item in character_ids if isinstance(item, str)]
        if isinstance(character_ids, list)
        else []
    )
    prop_ids = _json(row["prop_version_ids_json"], [])
    prop_ids = (
        [item for item in prop_ids if isinstance(item, str)]
        if isinstance(prop_ids, list)
        else []
    )
    reference_ids = prompt.get("reference_asset_ids", [])
    reference_ids = (
        [item for item in reference_ids if isinstance(item, str)]
        if isinstance(reference_ids, list)
        else []
    )
    location = str(row["location"] or row["script_location"] or "未标注场景")
    time_of_day = str(row["time_of_day"] or row["script_time_of_day"] or "未标注时段")
    description = str(row["description"] or row["shot_description"] or "历史镜头画面")
    dialogue = str(row["dialogue"] or "")
    action = str(prompt.get("visual_action") or description)
    delivery = str(prompt.get("delivery") or ("DIALOGUE" if dialogue else "ACTION"))
    subjects = prompt.get("bound_character_names", [])
    subjects = [str(item) for item in subjects] if isinstance(subjects, list) else []
    audio_cues = prompt.get("audio_cues", [])
    audio_cues = [str(item) for item in audio_cues] if isinstance(audio_cues, list) else []
    creative_bible = prompt.get("creative_bible", {})
    creative_bible = creative_bible if isinstance(creative_bible, dict) else {}
    palette_value = creative_bible.get("palette") or prompt.get("palette") or "沿用项目主色"
    palette = (
        [str(item) for item in palette_value]
        if isinstance(palette_value, list)
        else [str(palette_value)]
    )
    script_line_ids = _json(row["script_line_ids_json"], [])
    script_line_ids = (
        [item for item in script_line_ids if isinstance(item, str)]
        if isinstance(script_line_ids, list)
        else []
    )
    if not script_line_ids:
        script_line_ids = [f"legacy:{row['shot_id']}"]
    character_states = [
        {
            "character_id": character_id,
            "name": "",
            "position": "沿用历史镜头构图",
            "pose": "沿用历史镜头动作",
            "emotion": "沿用历史镜头表演",
            "wardrobe": "沿用锁定造型",
            "screen_direction": "NONE",
        }
        for character_id in character_ids
    ]
    prop_states = [
        {
            "prop_id": prop_id,
            "name": "",
            "state": "沿用历史镜头状态",
            "position": "沿用历史镜头位置",
        }
        for prop_id in prop_ids
    ]
    state = {
        "location": location,
        "time_of_day": time_of_day,
        "characters": character_states,
        "props": prop_states,
        "action_state": action,
        "environment_state": str(prompt.get("environment") or location),
    }
    spec = {
        "schema_version": "shot-spec-v1",
        "duration_sec": max(0.5, float(row["duration_ms"] or 1000) / 1000),
        "narrative_goal": str(row["purpose"] or "延续历史镜头叙事意图"),
        "visual_content": {
            "description": description,
            "subjects": subjects,
            "action": action,
            "environment": str(prompt.get("environment") or location),
            "composition": str(prompt.get("composition") or f"{row['shot_size']} 景别"),
            "visible_props": [str(item) for item in prop_ids],
        },
        "start_state": state,
        "end_state": {**state, "action_state": str(prompt.get("end_state") or action)},
        "camera": {
            "shot_size": str(row["shot_size"] or "MS"),
            "movement": str(row["camera_movement"] or "STATIC"),
            "angle": str(prompt.get("camera_angle") or "平视"),
            "framing": str(prompt.get("framing") or f"{row['shot_size']} 构图"),
            "lens_mm": 50,
            "focus": str(prompt.get("focus") or "主体清晰"),
            "axis_id": "",
            "axis_side": "NEUTRAL",
        },
        "lighting": {
            "style": str(prompt.get("lighting") or f"{time_of_day}叙事光线"),
            "key_light": str(prompt.get("key_light") or "沿用历史画面主光方向"),
            "fill_light": str(prompt.get("fill_light") or ""),
            "color_temperature": str(prompt.get("color_temperature") or "沿用历史色温"),
            "contrast": str(prompt.get("contrast") or "电影感对比"),
            "atmosphere": str(prompt.get("atmosphere") or "沿用历史场景氛围"),
        },
        "art_direction": {
            "visual_style": str(row["project_style"] or "沿用项目画风"),
            "palette": palette or ["沿用项目主色"],
            "texture": str(prompt.get("texture") or "电影质感"),
            "production_design": str(
                creative_bible.get("production_design") or "沿用历史场景美术"
            ),
            "wardrobe": "沿用角色锁定造型",
            "references": [],
        },
        "technique": {
            "pacing": f"{float(row['duration_ms'] or 1000) / 1000:g} 秒单镜节奏",
            "transition_in": "直接切入",
            "transition_out": "直接切出",
            "practical_effects": [],
            "vfx": [],
            "notes": "由旧镜头兼容迁移",
        },
        "performance": {
            "characters": [
                {
                    "character_id": character_id,
                    "action": action,
                    "emotion": "沿用历史表演",
                    "blocking": "沿用历史站位",
                    "dialogue": dialogue,
                }
                for character_id in character_ids
            ],
            "ensemble_blocking": "沿用历史镜头调度",
            "emotion_arc": "延续当前场景情绪",
            "notes": f"历史 delivery={delivery}",
        },
        "audio": {
            "dialogue": dialogue if delivery == "DIALOGUE" else "",
            "voice_over": dialogue if delivery == "VOICE_OVER" else "",
            "ambience": [],
            "sfx": audio_cues,
            "music": "",
            "sync_notes": "",
        },
        "continuity": {
            "character_ids": character_ids,
            "prop_version_ids": prop_ids,
            "location_version_id": row["location_version_id"],
            "previous_shot_id": None,
            "must_match": ["历史镜头人物、服装、场景和道具"],
            "axis_notes": "",
        },
        "generation": {
            "adapter": "generic",
            "model": "",
            "aspect_ratio": str(row["aspect_ratio"] or "9:16"),
            "resolution": "2K",
            "fps": 24,
            "negative_prompt": "文字、字幕、边框、拼贴、身份漂移、闪烁",
            "reference_asset_ids": reference_ids,
            "risk_flags": ["legacy_backfill"],
            "model_parameters": {},
        },
        "source": {
            "scene_ordinal": int(row["scene_ordinal"] or 1),
            "script_scene_id": str(row["script_scene_id"]),
            "script_line_ids": script_line_ids,
            "code": str(row["shot_code"] or f"S{int(row['ordinal']):02d}"),
            "title": str(row["shot_title"] or row["heading"] or "历史镜头"),
        },
    }
    provenance = {
        "source": "legacy_backfill",
        "migrated_at": datetime.now(UTC).isoformat(),
        "original_prompt_hash": _sha(prompt),
        "original_prompt_preserved_in": "shot_specs.prompt_json",
        "provisional_fields": [
            "start_state",
            "end_state",
            "lighting",
            "art_direction",
            "technique",
            "performance",
        ],
    }
    return spec, provenance


def _add_columns_and_lock_table() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("shot_specs")}
    definitions: tuple[tuple[str, sa.types.TypeEngine, object], ...] = (
        ("structured_spec_json", sa.Text(), "{}"),
        ("prompt_compiled", sa.Text(), ""),
        ("prompt_adapter", sa.String(32), "generic"),
        ("compiler_version", sa.String(48), "prompt-compiler-v1"),
        ("compiler_input_hash", sa.String(64), ""),
        ("prompt_compiled_hash", sa.String(64), ""),
        ("validation_report_json", sa.Text(), "{}"),
        ("lock_snapshot_json", sa.Text(), "{}"),
        ("migration_provenance_json", sa.Text(), "{}"),
        ("review_status", sa.String(32), "VALID"),
        ("repair_attempts", sa.Integer(), 0),
    )
    for name, column_type, default in definitions:
        if name not in columns:
            op.add_column(
                "shot_specs",
                sa.Column(name, column_type, nullable=False, server_default=str(default)),
            )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("shot_specs")}
    if "ix_shot_specs_review_status" not in indexes:
        op.create_index("ix_shot_specs_review_status", "shot_specs", ["review_status"])

    tables = set(sa.inspect(bind).get_table_names())
    if "shot_constraint_locks" not in tables:
        op.create_table(
            "shot_constraint_locks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("scope", sa.String(24), nullable=False),
            sa.Column("target_id", sa.String(36), nullable=False),
            sa.Column("field_path", sa.String(120), nullable=False),
            sa.Column("value_json", sa.Text(), nullable=False),
            sa.Column("owner", sa.String(80), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_id", "scope", "target_id", "field_path"),
        )
        op.create_index(
            "ix_shot_constraint_locks_project_id",
            "shot_constraint_locks",
            ["project_id"],
        )
        op.create_index("ix_shot_constraint_locks_scope", "shot_constraint_locks", ["scope"])
        op.create_index(
            "ix_shot_constraint_locks_target",
            "shot_constraint_locks",
            ["scope", "target_id"],
        )


def _backfill() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT ss.*, sh.code AS shot_code, sh.title AS shot_title,
                   sh.description AS shot_description, sh.character_ids_json,
                   sh.location, sh.time_of_day, sc.ordinal AS scene_ordinal,
                   ssc.heading, ssc.location AS script_location,
                   ssc.time_of_day AS script_time_of_day, ssc.purpose,
                   p.style AS project_style, p.aspect_ratio
            FROM shot_specs ss
            JOIN shots sh ON sh.id = ss.shot_id
            JOIN scenes sc ON sc.id = sh.scene_id
            JOIN storyboard_versions sb ON sb.id = ss.storyboard_version_id
            JOIN projects p ON p.id = sb.project_id
            JOIN script_scenes ssc ON ssc.id = ss.script_scene_id
            """
        )
    ).mappings()
    for row in rows:
        current = _json(row["structured_spec_json"], {})
        if isinstance(current, dict) and current:
            continue
        spec, provenance = _legacy_structured_spec(row)
        prompt = _json(row["prompt_json"], {})
        compiled = (
            str(prompt.get("image_prompt") or "")
            if isinstance(prompt, dict)
            else ""
        )
        report = {
            "valid": True,
            "needs_review": False,
            "total_duration_sec": spec["duration_sec"],
            "issues": [
                {
                    "code": "LEGACY_BACKFILL_INFERRED_FIELDS",
                    "severity": "WARNING",
                    "field_path": "shot",
                    "message": "部分导演字段由历史镜头兼容映射生成，请在下次编辑时确认",
                    "repairable": False,
                    "details": {"provisional_fields": provenance["provisional_fields"]},
                }
            ],
        }
        compiler_input = {
            "shot_spec": spec,
            "adapter": "generic",
            "compiler_version": "prompt-compiler-v1",
            "legacy": True,
        }
        bind.execute(
            sa.text(
                """
                UPDATE shot_specs
                SET structured_spec_json=:structured_spec_json,
                    prompt_compiled=:prompt_compiled,
                    prompt_adapter='generic',
                    compiler_version='prompt-compiler-v1',
                    compiler_input_hash=:compiler_input_hash,
                    prompt_compiled_hash=:prompt_compiled_hash,
                    validation_report_json=:validation_report_json,
                    lock_snapshot_json='{}',
                    migration_provenance_json=:migration_provenance_json,
                    review_status='VALID',
                    repair_attempts=0
                WHERE id=:id
                """
            ),
            {
                "id": row["id"],
                "structured_spec_json": _canonical(spec),
                "prompt_compiled": compiled,
                "compiler_input_hash": _sha(compiler_input),
                "prompt_compiled_hash": hashlib.sha256(compiled.encode("utf-8")).hexdigest(),
                "validation_report_json": _canonical(report),
                "migration_provenance_json": _canonical(provenance),
            },
        )


def upgrade() -> None:
    _add_columns_and_lock_table()
    _backfill()


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "shot_constraint_locks" in tables:
        op.drop_table("shot_constraint_locks")
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("shot_specs")}
    if "ix_shot_specs_review_status" in indexes:
        op.drop_index("ix_shot_specs_review_status", table_name="shot_specs")
    columns = {item["name"] for item in sa.inspect(bind).get_columns("shot_specs")}
    for name in (
        "repair_attempts",
        "review_status",
        "migration_provenance_json",
        "lock_snapshot_json",
        "validation_report_json",
        "prompt_compiled_hash",
        "compiler_input_hash",
        "compiler_version",
        "prompt_adapter",
        "prompt_compiled",
        "structured_spec_json",
    ):
        if name in columns:
            op.drop_column("shot_specs", name)
