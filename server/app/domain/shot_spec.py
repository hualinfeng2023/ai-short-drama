from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictShotModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class CharacterState(StrictShotModel):
    character_id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=120)
    position: str = Field(min_length=1, max_length=240)
    pose: str = Field(min_length=1, max_length=240)
    emotion: str = Field(min_length=1, max_length=160)
    wardrobe: str = Field(default="", max_length=300)
    screen_direction: Literal["LEFT", "RIGHT", "CENTER", "NONE"] = "NONE"


class PropState(StrictShotModel):
    prop_id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=120)
    state: str = Field(min_length=1, max_length=240)
    position: str = Field(default="", max_length=240)


class ShotState(StrictShotModel):
    location: str = Field(min_length=1, max_length=240)
    time_of_day: str = Field(min_length=1, max_length=80)
    characters: list[CharacterState] = Field(default_factory=list, max_length=12)
    props: list[PropState] = Field(default_factory=list, max_length=20)
    action_state: str = Field(min_length=1, max_length=600)
    environment_state: str = Field(min_length=1, max_length=600)


class VisualContent(StrictShotModel):
    description: str = Field(min_length=1, max_length=4000)
    subjects: list[str] = Field(default_factory=list, max_length=16)
    action: str = Field(min_length=1, max_length=1200)
    environment: str = Field(min_length=1, max_length=1200)
    composition: str = Field(min_length=1, max_length=600)
    visible_props: list[str] = Field(default_factory=list, max_length=20)


class CameraSpec(StrictShotModel):
    shot_size: Literal["EWS", "WS", "MS", "MCU", "CU", "ECU", "OTS", "POV"]
    movement: Literal[
        "STATIC",
        "PAN",
        "TILT",
        "DOLLY_IN",
        "DOLLY_OUT",
        "TRACK",
        "CRANE",
        "HANDHELD",
        "ZOOM",
    ]
    angle: str = Field(min_length=1, max_length=160)
    framing: str = Field(min_length=1, max_length=400)
    lens_mm: int = Field(ge=8, le=300)
    focus: str = Field(min_length=1, max_length=300)
    axis_id: str = Field(default="", max_length=80)
    axis_side: Literal["LEFT", "RIGHT", "ON_AXIS", "NEUTRAL"] = "NEUTRAL"


class LightingSpec(StrictShotModel):
    style: str = Field(min_length=1, max_length=240)
    key_light: str = Field(min_length=1, max_length=400)
    fill_light: str = Field(default="", max_length=400)
    color_temperature: str = Field(min_length=1, max_length=120)
    contrast: str = Field(min_length=1, max_length=160)
    atmosphere: str = Field(min_length=1, max_length=400)


class ArtDirectionSpec(StrictShotModel):
    visual_style: str = Field(min_length=1, max_length=400)
    palette: list[str] = Field(min_length=1, max_length=12)
    texture: str = Field(min_length=1, max_length=240)
    production_design: str = Field(min_length=1, max_length=600)
    wardrobe: str = Field(default="", max_length=600)
    references: list[str] = Field(default_factory=list, max_length=12)


class TechniqueSpec(StrictShotModel):
    pacing: str = Field(min_length=1, max_length=240)
    transition_in: str = Field(min_length=1, max_length=160)
    transition_out: str = Field(min_length=1, max_length=160)
    practical_effects: list[str] = Field(default_factory=list, max_length=12)
    vfx: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=800)


class CharacterPerformance(StrictShotModel):
    character_id: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=600)
    emotion: str = Field(min_length=1, max_length=240)
    blocking: str = Field(min_length=1, max_length=400)
    dialogue: str = Field(default="", max_length=2000)


class PerformanceSpec(StrictShotModel):
    characters: list[CharacterPerformance] = Field(default_factory=list, max_length=12)
    ensemble_blocking: str = Field(min_length=1, max_length=600)
    emotion_arc: str = Field(min_length=1, max_length=600)
    notes: str = Field(default="", max_length=800)


class AudioSpec(StrictShotModel):
    dialogue: str = Field(default="", max_length=4000)
    voice_over: str = Field(default="", max_length=4000)
    ambience: list[str] = Field(default_factory=list, max_length=16)
    sfx: list[str] = Field(default_factory=list, max_length=24)
    music: str = Field(default="", max_length=600)
    sync_notes: str = Field(default="", max_length=600)


class ContinuitySpec(StrictShotModel):
    character_ids: list[str] = Field(default_factory=list, max_length=12)
    prop_version_ids: list[str] = Field(default_factory=list, max_length=20)
    location_version_id: str | None = Field(default=None, max_length=80)
    previous_shot_id: str | None = Field(default=None, max_length=80)
    must_match: list[str] = Field(default_factory=list, max_length=24)
    axis_notes: str = Field(default="", max_length=400)


class GenerationSpec(StrictShotModel):
    adapter: Literal["generic", "veo", "kling", "seedance"] = "generic"
    model: str = Field(default="", max_length=160)
    aspect_ratio: Literal[
        "1:1",
        "4:3",
        "3:4",
        "16:9",
        "9:16",
        "3:2",
        "2:3",
        "21:9",
    ]
    resolution: str = Field(default="2K", min_length=1, max_length=40)
    fps: int = Field(default=24, ge=1, le=120)
    negative_prompt: str = Field(default="", max_length=2000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=24)
    risk_flags: list[str] = Field(default_factory=list, max_length=24)
    model_parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ShotSource(StrictShotModel):
    scene_ordinal: int = Field(ge=1)
    script_scene_id: str = Field(min_length=1, max_length=80)
    script_line_ids: list[str] = Field(min_length=1, max_length=20)
    code: str = Field(min_length=1, max_length=24)
    title: str = Field(min_length=1, max_length=160)


class ShotSpec(StrictShotModel):
    schema_version: Literal["shot-spec-v1"] = "shot-spec-v1"
    duration_sec: float = Field(ge=0.5, le=30)
    narrative_goal: str = Field(min_length=1, max_length=1200)
    visual_content: VisualContent
    start_state: ShotState
    end_state: ShotState
    camera: CameraSpec
    lighting: LightingSpec
    art_direction: ArtDirectionSpec
    technique: TechniqueSpec
    performance: PerformanceSpec
    audio: AudioSpec
    continuity: ContinuitySpec
    generation: GenerationSpec
    source: ShotSource

    @model_validator(mode="after")
    def validate_state_subjects(self) -> "ShotSpec":
        continuity_ids = set(self.continuity.character_ids)
        start_ids = {item.character_id for item in self.start_state.characters}
        end_ids = {item.character_id for item in self.end_state.characters}
        performance_ids = {item.character_id for item in self.performance.characters}
        unknown = (start_ids | end_ids | performance_ids) - continuity_ids
        if unknown:
            raise ValueError(
                "人物状态与表演只能引用 continuity.character_ids 中的角色："
                + "、".join(sorted(unknown))
            )
        return self


class StoryboardShotPlan(StrictShotModel):
    shots: list[ShotSpec] = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_source_order(self) -> "StoryboardShotPlan":
        codes = [item.source.code for item in self.shots]
        if len(codes) != len(set(codes)):
            raise ValueError("镜头 code 不能重复")
        return self


SHOT_SPEC_EDITABLE_FIELDS: tuple[str, ...] = (
    "duration_sec",
    "narrative_goal",
    "visual_content",
    "start_state",
    "end_state",
    "camera",
    "lighting",
    "art_direction",
    "technique",
    "performance",
    "audio",
    "continuity",
    "generation",
)
