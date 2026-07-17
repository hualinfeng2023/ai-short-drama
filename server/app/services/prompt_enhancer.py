from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Project, Shot
from app.schemas import PromptEnhanceRead
from app.services.takes import _shot_project
from app.services.workspace import shot_or_404


@dataclass(frozen=True)
class EnhancedPrompt:
    text: str
    provider: str
    model: str


class PromptEnhancerError(Exception):
    pass


def _extract_output_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _provider_input(project: Project, shot: Shot, description: str) -> str:
    dialogue = shot.dialogue.strip() or "无对白"
    return f"""你是一名电影分镜导演和 Seedream 提示词工程师。
请把下面的短剧画面描述改写成一段可直接用于文生图的中文提示词。

硬性要求：
1. 保留原剧情事实、人物身份、地点和动作，不新增角色、品牌、文字、字幕或关键剧情。
2. 补足主体姿态与微表情、前中后景层次、环境材质、光线方向、色温、色彩层次、镜头焦段感、景深和构图。
3. 结合景别和运镜，让画面具有明确视觉焦点和电影叙事感，同时保持物理真实。
4. 对人物手部、五官、服装、空间关系和光影连续性给出自然约束，避免畸变、重复肢体、漂浮物和画面文字。
5. 只输出最终改写结果，不要标题、解释、列表或 Markdown；控制在 180 至 320 个中文字符。

项目风格：{project.style}
画幅：{project.aspect_ratio}
镜头：{shot.shot_size}，{shot.camera_movement}
地点与时间：{shot.location}，{shot.time_of_day}
对白状态：{dialogue}
原画面描述：{description}
"""


def _local_enhancement(project: Project, shot: Shot, description: str) -> str:
    shot_language = {
        "WS": "广角全景构图，清楚交代人物与环境的空间关系",
        "MS": "中景构图，人物动作与周围环境信息保持平衡",
        "MCU": "中近景构图，突出人物上半身姿态与细微表情",
        "CU": "近景特写，视觉焦点落在人物眼神、呼吸和面部情绪",
    }.get(shot.shot_size, f"{shot.shot_size} 景别")
    movement = {
        "STATIC": "机位稳定，构图克制",
        "PAN": "镜头沿环境自然横移，画面边缘保留运动空间",
        "DOLLY_IN": "镜头缓慢推进，透视层次逐步压缩并强化情绪",
        "TRACK": "镜头贴近主体平稳跟随，背景形成连续视差",
        "HANDHELD": "轻微手持呼吸感，保持主体清晰且不过度晃动",
    }.get(shot.camera_movement, shot.camera_movement)
    time_lower = shot.time_of_day.lower()
    if any(token in time_lower for token in ("夜", "night", "晚")):
        lighting = "冷蓝环境光与室内暖色实景光形成层次，湿润反射和柔和轮廓光勾勒主体"
    else:
        lighting = "自然主光从侧前方塑造面部和衣料纹理，柔和环境反光补足暗部层次"
    dialogue_hint = (
        "人物处于自然说话间隙，口型、眼神和呼吸状态真实"
        if shot.dialogue.strip()
        else "人物表情克制自然，以眼神和身体重心传递情绪"
    )
    return (
        f"{description.rstrip('。')}。场景位于{shot.location}，时间为{shot.time_of_day}；"
        f"{shot_language}，{movement}。{dialogue_hint}。前景保留少量虚化环境元素形成遮挡，"
        "中景承载人物和核心动作，背景以建筑结构、空气透视和细小生活痕迹建立真实空间深度。"
        f"{lighting}；整体延续{project.style}，色彩克制但层次丰富，真实材质、电影级景深、"
        "自然动态瞬间和细腻颗粒。保持人物身份、服装、发型、手部、五官、视线方向及空间关系一致，"
        "避免多余人物、重复肢体、畸变手指、漂浮物、画面文字、字幕、边框和拼贴。"
    )


async def _call_ark(
    settings: Settings,
    project: Project,
    shot: Shot,
    description: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EnhancedPrompt:
    if not settings.ark_api_key:
        raise PromptEnhancerError("ARK_API_KEY 未配置")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ark_request_timeout_seconds),
            transport=transport,
        ) as client:
            response = await client.post(
                settings.ark_responses_url,
                headers={
                    "Authorization": f"Bearer {settings.ark_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.ark_prompt_model,
                    "input": _provider_input(project, shot, description),
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PromptEnhancerError("火山方舟智能改写暂时不可用") from exc
    text = _extract_output_text(payload)
    if text is None:
        raise PromptEnhancerError("火山方舟未返回可用的改写文本")
    return EnhancedPrompt(text=text, provider="volcengine-ark", model=settings.ark_prompt_model)


async def enhance_shot_description(
    session: Session,
    *,
    shot_id: str,
    description: str,
    settings: Settings | None = None,
) -> PromptEnhanceRead:
    resolved_settings = settings or get_settings()
    shot = shot_or_404(session, shot_id)
    project = _shot_project(session, shot)
    try:
        result = await _call_ark(resolved_settings, project, shot, description)
        warning = None
    except PromptEnhancerError as exc:
        result = EnhancedPrompt(
            text=_local_enhancement(project, shot, description),
            provider="local-fallback",
            model="cinematic-prompt-enhancer-v1",
        )
        warning = str(exc)
    return PromptEnhanceRead(
        original=description,
        enhanced=result.text,
        provider=result.provider,
        model=result.model,
        warning=warning,
    )
