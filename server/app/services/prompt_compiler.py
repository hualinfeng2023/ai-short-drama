import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from app.domain.shot_spec import ShotSpec

COMPILER_VERSION = "prompt-compiler-v1"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptIR:
    shot_spec: ShotSpec
    project_lock: dict[str, object]
    scene_lock: dict[str, object]
    character_locks: list[dict[str, object]]
    field_locks: list[dict[str, object]]

    def source_manifest(self) -> dict[str, object]:
        return {
            "project_lock": self.project_lock,
            "scene_lock": self.scene_lock,
            "character_locks": self.character_locks,
            "field_locks": self.field_locks,
        }


@dataclass(frozen=True)
class CompiledPrompt:
    prompt: str
    adapter: str
    adapter_version: str
    compiler_version: str
    compiler_input_hash: str
    prompt_hash: str
    provenance: dict[str, object]


class PromptAdapter(Protocol):
    name: str
    version: str

    def compile(self, ir: PromptIR) -> str: ...


def _state_line(label: str, state: object) -> str:
    payload = state.model_dump(mode="json") if hasattr(state, "model_dump") else state
    return f"{label}：{_canonical(payload)}"


def _base_sections(ir: PromptIR) -> list[str]:
    shot = ir.shot_spec
    character_lock_lines = [
        (
            f"- {item.get('name') or item.get('character_id')}："
            f"identity={item.get('identity_version_id') or '未锁定'}，"
            f"look={item.get('look_version_id') or '未锁定'}，"
            f"story_state={item.get('story_state_version_id') or '未锁定'}"
        )
        for item in ir.character_locks
    ]
    sections = [
        f"镜头时长：{shot.duration_sec:g} 秒",
        f"叙事目标：{shot.narrative_goal}",
        f"画面内容：{shot.visual_content.description}",
        f"主体与动作：{shot.visual_content.action}",
        f"环境与构图：{shot.visual_content.environment}；{shot.visual_content.composition}",
        (
            "摄影机："
            f"{shot.camera.shot_size}，{shot.camera.movement}，{shot.camera.angle}，"
            f"{shot.camera.lens_mm}mm，{shot.camera.framing}，焦点 {shot.camera.focus}"
        ),
        (
            "灯光："
            f"{shot.lighting.style}；主光 {shot.lighting.key_light}；"
            f"色温 {shot.lighting.color_temperature}；对比 {shot.lighting.contrast}；"
            f"氛围 {shot.lighting.atmosphere}"
        ),
        (
            "美术："
            f"{shot.art_direction.visual_style}；色板 {'、'.join(shot.art_direction.palette)}；"
            f"材质 {shot.art_direction.texture}；"
            f"场景设计 {shot.art_direction.production_design}；"
            f"服装 {shot.art_direction.wardrobe or '沿用角色锁定造型'}"
        ),
        (
            "导演手法："
            f"节奏 {shot.technique.pacing}；入镜 {shot.technique.transition_in}；"
            f"出镜 {shot.technique.transition_out}；{shot.technique.notes}"
        ),
        f"表演：{shot.performance.ensemble_blocking}；{shot.performance.emotion_arc}",
        (
            "声音："
            f"对白 {shot.audio.dialogue or '无'}；画外音 {shot.audio.voice_over or '无'}；"
            f"环境声 {'、'.join(shot.audio.ambience) or '无'}；"
            f"音效 {'、'.join(shot.audio.sfx) or '无'}；音乐 {shot.audio.music or '无'}"
        ),
        _state_line("镜头起始状态", shot.start_state),
        _state_line("镜头结束状态", shot.end_state),
        (
            "连续性硬约束："
            f"人物 {','.join(shot.continuity.character_ids) or '无'}；"
            f"道具 {','.join(shot.continuity.prop_version_ids) or '无'}；"
            f"轴线 {shot.camera.axis_id or '未指定'}/{shot.camera.axis_side}；"
            f"{'；'.join(shot.continuity.must_match)}"
        ),
        (
            "生成约束："
            f"画幅 {shot.generation.aspect_ratio}；分辨率 {shot.generation.resolution}；"
            f"{shot.generation.fps} fps；负面约束 {shot.generation.negative_prompt or '无'}"
        ),
    ]
    if character_lock_lines:
        sections.append("角色锁（不得改写身份与造型）：\n" + "\n".join(character_lock_lines))
    if ir.project_lock:
        sections.append("Project Global Lock：" + _canonical(ir.project_lock))
    if ir.scene_lock:
        sections.append("Scene Lock：" + _canonical(ir.scene_lock))
    return sections


class GenericPromptAdapter:
    name = "generic"
    version = "generic-v1"

    def compile(self, ir: PromptIR) -> str:
        return "\n".join(
            [
                "按以下结构化导演规格生成一个且仅一个镜头。不得增加未声明的人物、道具、"
                "对白、动作或场景，不得改写任何锁定项。",
                *_base_sections(ir),
            ]
        )


class VeoPromptAdapter:
    name = "veo"
    version = "veo-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Veo 视频生成任务。用连续时间描述单个完整镜头；优先保持时序、物理运动、"
                "角色身份和同步声音，不要切换成多镜头蒙太奇。",
                *_base_sections(ir),
                (
                    "Veo 时序指令："
                    f"0 秒严格对应 start_state；在 {shot.duration_sec:g} 秒内仅完成已声明动作；"
                    f"最后一帧严格落在 end_state。声音与可见动作按 sync_notes 同步。"
                ),
            ]
        )


class KlingPromptAdapter:
    name = "kling"
    version = "kling-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Kling 视频生成任务。把运动幅度、主体轨迹、摄影机轨迹和首尾帧稳定性写成"
                "可执行约束；避免动作叠加、身份漂移与末帧跳变。",
                *_base_sections(ir),
                (
                    "Kling 运动指令："
                    f"主体动作只执行“{shot.visual_content.action}”；"
                    f"摄影机只执行 {shot.camera.movement}；"
                    "首帧、末帧分别锁定结构化 start_state 与 end_state。"
                ),
            ]
        )


class SeedancePromptAdapter:
    name = "seedance"
    version = "seedance-v1"

    def compile(self, ir: PromptIR) -> str:
        shot = ir.shot_spec
        return "\n".join(
            [
                "Seedance 图生视频任务。参考图只确定首帧主体身份、服装、场景与构图；"
                "视频阶段只执行 ShotSpec 声明的动作和运镜，禁止新增主体、文字、字幕和转场拼贴。",
                *_base_sections(ir),
                (
                    "Seedance 稳定性指令："
                    f"总时长 {shot.duration_sec:g} 秒，{shot.camera.movement} 运镜，"
                    "人物脸型、五官、发型、服装，道具外观及背景布局全程稳定；"
                    "末帧必须符合 end_state，避免闪烁、融脸、肢体断裂和物体凭空出现。"
                ),
            ]
        )


ADAPTERS: dict[str, PromptAdapter] = {
    adapter.name: adapter
    for adapter in (
        GenericPromptAdapter(),
        VeoPromptAdapter(),
        KlingPromptAdapter(),
        SeedancePromptAdapter(),
    )
}


class PromptCompiler:
    def compile(
        self,
        shot_spec: ShotSpec,
        *,
        adapter_name: str | None = None,
        project_lock: dict[str, object] | None = None,
        scene_lock: dict[str, object] | None = None,
        character_locks: list[dict[str, object]] | None = None,
        field_locks: list[dict[str, object]] | None = None,
    ) -> CompiledPrompt:
        resolved_adapter = adapter_name or shot_spec.generation.adapter
        adapter = ADAPTERS.get(resolved_adapter)
        if adapter is None:
            raise ValueError(f"不支持的 Prompt Adapter：{resolved_adapter}")
        ir = PromptIR(
            shot_spec=shot_spec,
            project_lock=project_lock or {},
            scene_lock=scene_lock or {},
            character_locks=character_locks or [],
            field_locks=field_locks or [],
        )
        compiler_input = {
            "shot_spec": shot_spec.model_dump(mode="json"),
            "locks": ir.source_manifest(),
            "compiler_version": COMPILER_VERSION,
            "adapter": adapter.name,
            "adapter_version": adapter.version,
        }
        compiler_input_hash = _hash(compiler_input)
        prompt = adapter.compile(ir).strip()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return CompiledPrompt(
            prompt=prompt,
            adapter=adapter.name,
            adapter_version=adapter.version,
            compiler_version=COMPILER_VERSION,
            compiler_input_hash=compiler_input_hash,
            prompt_hash=prompt_hash,
            provenance={
                "compiler_input_hash": compiler_input_hash,
                "compiler_version": COMPILER_VERSION,
                "adapter": adapter.name,
                "adapter_version": adapter.version,
                "sources": ir.source_manifest(),
            },
        )
