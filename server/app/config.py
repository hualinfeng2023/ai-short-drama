import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.services.provider_settings import load_provider_overrides

SERVER_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(SERVER_ROOT / ".env", override=False)

ARK_IMAGE_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("doubao-seedream-5-0-260128", "Seedream 5.0 Pro"),
    ("doubao-seedream-5-0-lite-260128", "Seedream 5.0 Lite"),
    ("doubao-seedream-4-5-251128", "Seedream 4.5"),
    ("doubao-seedream-4-0-250828", "Seedream 4.0"),
)

ARK_IMAGE_RESOLUTIONS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "doubao-seedream-5-0-260128": ("1K", "2K"),
    "doubao-seedream-5-0-lite-260128": ("2K", "3K", "4K"),
    "doubao-seedream-4-5-251128": ("2K", "4K"),
    "doubao-seedream-4-0-250828": ("2K", "3K", "4K"),
}

ARK_IMAGE_ASPECT_RATIOS: tuple[str, ...] = (
    "1:1",
    "4:3",
    "3:4",
    "16:9",
    "9:16",
    "3:2",
    "2:3",
    "21:9",
)

FEATURE_FLAG_ENV: dict[str, str] = {
    "creative_text_v2": "CREATIVE_TEXT_V2",
    "brief_targeting_v2": "BRIEF_TARGETING_V2",
    "workflow_dag_v1": "WORKFLOW_DAG_V1",
    "preproduction_v2": "PREPRODUCTION_V2",
    "storyboard_animatic_v2": "STORYBOARD_ANIMATIC_V2",
    "generation_qc_v2": "GENERATION_QC_V2",
    "audio_pipeline_v1": "AUDIO_PIPELINE_V1",
    "multitrack_timeline_v1": "MULTITRACK_TIMELINE_V1",
    "export_profiles_v2": "EXPORT_PROFILES_V2",
    "provider_media_staging_v1": "PROVIDER_MEDIA_STAGING_V1",
}


@dataclass(frozen=True)
class Settings:
    app_name: str
    environment: str
    data_dir: Path
    database_url: str
    job_worker_enabled: bool
    worker_poll_interval: float
    worker_lease_seconds: int
    worker_heartbeat_stale_seconds: int
    ark_api_key: str | None
    ark_images_url: str
    ark_image_model: str
    ark_request_timeout_seconds: float
    ark_responses_url: str
    ark_prompt_model: str
    ark_identity_qc_enabled: bool
    ark_identity_auto_pass_threshold: float
    ark_video_tasks_url: str
    ark_video_model: str
    ark_video_poll_interval_seconds: float
    ark_video_timeout_seconds: float
    seedream_source_url_fast_path_seconds: int
    tos_access_key: str | None
    tos_secret_key: str | None
    tos_security_token: str | None
    tos_endpoint: str
    tos_region: str
    tos_bucket: str | None
    tos_presign_ttl_seconds: int
    tos_object_prefix: str
    tos_object_expires_days: int
    tos_cleanup_on_completion: bool
    feature_flags: dict[str, bool]


def available_ark_image_models(settings: Settings) -> list[dict[str, str]]:
    options = [{"id": model_id, "label": label} for model_id, label in ARK_IMAGE_MODEL_OPTIONS]
    if settings.ark_image_model not in {option["id"] for option in options}:
        options.insert(0, {"id": settings.ark_image_model, "label": "自定义方舟模型"})
    return options


def available_ark_image_resolutions(model: str) -> tuple[str, ...]:
    return ARK_IMAGE_RESOLUTIONS_BY_MODEL.get(model, ("2K",))


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", SERVER_ROOT / "data")).expanduser().resolve()
    provider_overrides = load_provider_overrides(data_dir)

    def configured(name: str, default: object | None = None) -> object | None:
        if name in provider_overrides:
            return provider_overrides[name]
        return os.getenv(name, default)

    def configured_text(name: str, default: str = "") -> str:
        value = configured(name, default)
        return str(value) if value is not None else ""

    def configured_bool(name: str, default: bool) -> bool:
        value = configured(name)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    default_database_url = f"sqlite:///{data_dir / 'app.db'}"
    return Settings(
        app_name="AI Short Drama API",
        environment=os.getenv("APP_ENV", "development"),
        data_dir=data_dir,
        database_url=os.getenv("DATABASE_URL", default_database_url),
        job_worker_enabled=env_bool("JOB_WORKER_ENABLED", True),
        worker_poll_interval=float(os.getenv("WORKER_POLL_INTERVAL", "0.25")),
        worker_lease_seconds=int(os.getenv("WORKER_LEASE_SECONDS", "15")),
        worker_heartbeat_stale_seconds=int(os.getenv("WORKER_HEARTBEAT_STALE_SECONDS", "5")),
        ark_api_key=configured_text("ARK_API_KEY") or None,
        ark_images_url=configured_text(
            "ARK_IMAGES_URL",
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        ),
        ark_image_model=configured_text("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
        # 深度思考模式下长结构化生成单次可能超过 180 秒，默认放宽到 300 秒
        ark_request_timeout_seconds=float(configured("ARK_REQUEST_TIMEOUT_SECONDS", "300")),
        ark_responses_url=configured_text(
            "ARK_RESPONSES_URL",
            "https://ark.cn-beijing.volces.com/api/v3/responses",
        ),
        ark_prompt_model=configured_text("ARK_PROMPT_MODEL", "doubao-seed-2-0-lite-260215"),
        ark_identity_qc_enabled=configured_bool("ARK_IDENTITY_QC_ENABLED", True),
        ark_identity_auto_pass_threshold=float(
            configured("ARK_IDENTITY_AUTO_PASS_THRESHOLD", "0.88")
        ),
        ark_video_tasks_url=configured_text(
            "ARK_VIDEO_TASKS_URL",
            "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
        ),
        ark_video_model=configured_text("ARK_VIDEO_MODEL", "doubao-seedance-1-5-pro-251215"),
        ark_video_poll_interval_seconds=float(configured("ARK_VIDEO_POLL_INTERVAL_SECONDS", "5")),
        ark_video_timeout_seconds=float(configured("ARK_VIDEO_TIMEOUT_SECONDS", "900")),
        seedream_source_url_fast_path_seconds=max(
            60,
            min(int(configured("SEEDREAM_SOURCE_URL_FAST_PATH_SECONDS", "600")), 3600),
        ),
        tos_access_key=configured_text("TOS_ACCESS_KEY") or None,
        tos_secret_key=configured_text("TOS_SECRET_KEY") or None,
        tos_security_token=configured_text("TOS_SECURITY_TOKEN") or None,
        tos_endpoint=configured_text("TOS_ENDPOINT", "tos-cn-beijing.volces.com"),
        tos_region=configured_text("TOS_REGION", "cn-beijing"),
        tos_bucket=configured_text("TOS_BUCKET") or None,
        tos_presign_ttl_seconds=max(
            900,
            min(int(configured("TOS_PRESIGN_TTL_SECONDS", "7200")), 86400),
        ),
        tos_object_prefix=configured_text(
            "TOS_OBJECT_PREFIX",
            "ai-short-drama/media-staging",
        ).strip("/"),
        tos_object_expires_days=max(
            1,
            min(int(configured("TOS_OBJECT_EXPIRES_DAYS", "1")), 7),
        ),
        tos_cleanup_on_completion=configured_bool("TOS_CLEANUP_ON_COMPLETION", True),
        feature_flags={
            key: configured_bool(environment_name, False)
            for key, environment_name in FEATURE_FLAG_ENV.items()
        },
    )
