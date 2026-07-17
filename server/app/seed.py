import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Asset,
    BriefVersion,
    Character,
    CharacterCandidate,
    Episode,
    Job,
    Project,
    ProposalVersion,
    Scene,
    Shot,
    Take,
)
from app.db.session import get_engine
from app.services.media import deterministic_png_bytes

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EPISODE_ID = "22222222-2222-4222-8222-222222222222"
SCENE_IDS = [f"30000000-0000-4000-8000-{index:012d}" for index in range(1, 4)]
SHOT_IDS = [f"40000000-0000-4000-8000-{index:012d}" for index in range(1, 9)]
CHARACTER_ID = "70000000-0000-4000-8000-000000000101"
CHARACTER_ASSET_ID = "70000000-0000-4000-8000-000000000102"
CHARACTER_CANDIDATE_ID = "70000000-0000-4000-8000-000000000103"
SISTER_CHARACTER_ID = "70000000-0000-4000-8000-000000000201"
SISTER_ASSET_ID = "70000000-0000-4000-8000-000000000202"
SISTER_CANDIDATE_ID = "70000000-0000-4000-8000-000000000203"
CUSTOMER_CHARACTER_ID = "70000000-0000-4000-8000-000000000301"
CUSTOMER_ASSET_ID = "70000000-0000-4000-8000-000000000302"
CUSTOMER_CANDIDATE_ID = "70000000-0000-4000-8000-000000000303"

SHOT_CHARACTER_IDS = {
    SHOT_IDS[2]: [CHARACTER_ID, SISTER_CHARACTER_ID],
    SHOT_IDS[5]: [CHARACTER_ID, CUSTOMER_CHARACTER_ID],
}


def add_if_missing(session: Session, item: Any) -> None:
    if session.get(type(item), item.id) is None:
        session.add(item)


def current_shot_asset_id(session: Session, shot_id: str) -> str | None:
    shot = session.get(Shot, shot_id)
    if shot is None or shot.current_take_id is None:
        return None
    take = session.get(Take, shot.current_take_id)
    if take is None or session.get(Asset, take.asset_id) is None:
        return None
    return take.asset_id


def seed_database(session: Session) -> None:
    settings = get_settings()
    project = Project(
        id=PROJECT_ID,
        name="她的第二人生",
        idea="女主在职业和情感双重打击后，重新找回人生方向。",
        genre="都市情感 · 女性成长",
        style="现实电影感",
        target_duration_sec=60,
        aspect_ratio="9:16",
        target_platform="douyin",
        status="PRODUCING",
        lock_version=1,
        available_points=49760,
        timeline_version=2,
        preview_approved=False,
        export_ready=False,
        created_at=datetime.fromisoformat("2026-07-13T19:10:00+08:00").astimezone(UTC),
        updated_at=datetime.fromisoformat("2026-07-13T19:42:00+08:00").astimezone(UTC),
    )
    brief_payload = {
        "project_name": project.name,
        "raw_input": project.idea,
        "genre": project.genre,
        "style": project.style,
        "target_duration_sec": project.target_duration_sec,
        "aspect_ratio": project.aspect_ratio,
        "target_platform": project.target_platform,
        "reference_asset_ids": [],
        "assumptions": [],
        "narrative_protagonist": "female",
        "target_audience": "female_frequency",
        "emotional_rewards": ["identity", "career"],
        "audience_profile": "25—40岁女性",
        "production_format": "live_action",
        "payload_schema_version": "brief-v3",
    }
    brief_json = json.dumps(
        brief_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    brief = BriefVersion(
        id="60000000-0000-4000-8000-000000000001",
        project_id=PROJECT_ID,
        version=1,
        project_name=project.name,
        raw_input=project.idea,
        genre=project.genre,
        style=project.style,
        target_duration_sec=project.target_duration_sec,
        aspect_ratio=project.aspect_ratio,
        target_platform=project.target_platform,
        reference_asset_ids_json="[]",
        assumptions_json="[]",
        narrative_protagonist="female",
        target_audience="female_frequency",
        emotional_rewards_json='["identity","career"]',
        audience_profile="25—40岁女性",
        production_format="live_action",
        payload_schema_version="brief-v3",
        content_hash=sha256(brief_json.encode()).hexdigest(),
        status="APPROVED",
        created_at=project.created_at,
    )
    episode = Episode(
        id=EPISODE_ID,
        project_id=PROJECT_ID,
        code="S01E01",
        title="她的第二人生",
        target_duration_sec=60,
        status="IN_PROGRESS",
    )
    scenes = [
        Scene(
            id=SCENE_IDS[0],
            episode_id=EPISODE_ID,
            code="01",
            ordinal=1,
            title="坠落",
            purpose="让观众看到她失去熟悉生活的瞬间",
            duration_sec=18,
            status="APPROVED",
        ),
        Scene(
            id=SCENE_IDS[1],
            episode_id=EPISODE_ID,
            code="02",
            ordinal=2,
            title="觉醒",
            purpose="用一个可执行的选择替代说教式转折",
            duration_sec=22,
            status="IN_PROGRESS",
        ),
        Scene(
            id=SCENE_IDS[2],
            episode_id=EPISODE_ID,
            code="03",
            ordinal=3,
            title="重新选择",
            purpose="用行动证明她建立了新的生活秩序",
            duration_sec=20,
            status="READY",
        ),
    ]
    shot_values = [
        (
            0,
            0,
            "S01",
            "雨夜离开公司",
            "林悦抱着纸箱从写字楼走出，雨水打在玻璃幕墙上。",
            "今天之后，我和这里再没有关系。",
            9,
            "APPROVED",
            "WS",
            "TRACK",
            2,
            None,
            "CLEAR",
            "写字楼门口",
            "夜",
        ),
        (
            1,
            0,
            "S02",
            "被删掉的语音",
            "手机屏幕停在未发送的语音，林悦按下删除。",
            "不用解释了。",
            9,
            "APPROVED",
            "CU",
            "STATIC",
            1,
            None,
            "CLEAR",
            "写字楼门口",
            "夜",
        ),
        (
            2,
            1,
            "S03",
            "妹妹递来钥匙",
            "妹妹把工作室钥匙推到桌面中央，手没有收回。",
            "你可以继续等，也可以现在就开门。",
            8,
            "PENDING_REVIEW",
            "MCU",
            "DOLLY_IN",
            2,
            3,
            "NOTICE",
            "旧咖啡馆",
            "清晨",
        ),
        (
            3,
            1,
            "S04",
            "镜中重整头发",
            "林悦对着洗手间旧镜子扎起头发，擦掉晕开的眼线。",
            "",
            7,
            "APPROVED",
            "MS",
            "STATIC",
            2,
            None,
            "CLEAR",
            "旧咖啡馆",
            "清晨",
        ),
        (
            4,
            1,
            "S05",
            "拉开卷帘门",
            "逆光里，林悦独自拉开尘封工作室的卷帘门。",
            "先从今天开始。",
            7,
            "GENERATING",
            "WS",
            "DOLLY_IN",
            1,
            2,
            "RISK",
            "工作室门口",
            "清晨",
        ),
        (
            5,
            2,
            "S06",
            "第一位客人",
            "女孩犹豫着走进工作室，林悦递出一杯水。",
            "你想从哪里重新开始？",
            7,
            "READY",
            "MS",
            "PAN",
            1,
            None,
            "CLEAR",
            "工作室内",
            "日",
        ),
        (
            6,
            2,
            "S07",
            "旧同事来电",
            "手机亮起旧公司号码，林悦看了一眼，反扣在桌上。",
            "",
            7,
            "READY",
            "CU",
            "STATIC",
            1,
            None,
            "CLEAR",
            "工作室内",
            "日",
        ),
        (
            7,
            2,
            "S08",
            "灯牌第一次亮起",
            "暮色里，工作室灯牌亮起，林悦站在门内看向街道。",
            "这一次，我选我自己。",
            6,
            "READY",
            "WS",
            "TRACK",
            1,
            None,
            "CLEAR",
            "工作室门口",
            "黄昏",
        ),
    ]
    shots = [
        Shot(
            id=SHOT_IDS[index],
            scene_id=SCENE_IDS[scene_index],
            code=code,
            ordinal=index + 1,
            title=title,
            description=description,
            dialogue=dialogue,
            duration_sec=duration,
            status=status,
            shot_size=shot_size,
            camera_movement=movement,
            current_take=current_take,
            candidate_take=candidate_take,
            continuity=continuity,
            location=location,
            time_of_day=time_of_day,
            character_ids_json=json.dumps(SHOT_CHARACTER_IDS.get(SHOT_IDS[index], [CHARACTER_ID])),
            character_look_version="Look V1",
        )
        for (
            index,
            scene_index,
            code,
            title,
            description,
            dialogue,
            duration,
            status,
            shot_size,
            movement,
            current_take,
            candidate_take,
            continuity,
            location,
            time_of_day,
        ) in shot_values
    ]
    jobs = [
        Job(
            id="50000000-0000-4000-8000-000000000001",
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="shot",
            entity_id=SHOT_IDS[4],
            idempotency_key="seed:demo-render:s05:v2",
            request_hash="seed-demo-render",
            label="S05 · 主镜头第 2 版",
            entity=f"{SCENE_IDS[1]} / {SHOT_IDS[4]}",
            status="FAILED",
            progress=72,
            stage="模拟渲染临时失败",
            priority=0,
            attempt=1,
            max_attempts=3,
            available_at=datetime.fromisoformat("2026-07-13T19:37:00+08:00").astimezone(UTC),
            lease_until=None,
            heartbeat_at=None,
            cancel_requested=False,
            input_json='{"steps":4}',
            output_json=None,
            error_code="MOCK_PROVIDER_TEMPORARY_FAILURE",
            error_message="可重试的演示任务",
            error_details_json="{}",
            created_at_label="19:37",
            created_at=datetime.fromisoformat("2026-07-13T19:37:00+08:00").astimezone(UTC),
            updated_at=datetime.fromisoformat("2026-07-13T19:38:00+08:00").astimezone(UTC),
            completed_at=datetime.fromisoformat("2026-07-13T19:38:00+08:00").astimezone(UTC),
            worker_id=None,
            trace_id="50000000-0000-4000-8000-000000000001",
            estimated_seconds=18,
            retryable=True,
        ),
        Job(
            id="50000000-0000-4000-8000-000000000002",
            project_id=PROJECT_ID,
            job_type="DEMO_RENDER",
            entity_type="episode",
            entity_id=EPISODE_ID,
            idempotency_key="seed:preview-timeline:v2",
            request_hash="seed-preview-v2",
            label="小样时间线第 2 版",
            entity=EPISODE_ID,
            status="SUCCEEDED",
            progress=100,
            stage="组装完成",
            priority=0,
            attempt=1,
            max_attempts=1,
            available_at=datetime.fromisoformat("2026-07-13T19:16:00+08:00").astimezone(UTC),
            lease_until=None,
            heartbeat_at=None,
            cancel_requested=False,
            input_json='{"steps":1}',
            output_json='{"rendered":true,"provider":"mock"}',
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at_label="19:16",
            created_at=datetime.fromisoformat("2026-07-13T19:16:00+08:00").astimezone(UTC),
            updated_at=datetime.fromisoformat("2026-07-13T19:17:00+08:00").astimezone(UTC),
            completed_at=datetime.fromisoformat("2026-07-13T19:17:00+08:00").astimezone(UTC),
            worker_id=None,
            trace_id="50000000-0000-4000-8000-000000000002",
            estimated_seconds=None,
            retryable=False,
        ),
    ]

    proposal = ProposalVersion(
        id="80000000-0000-4000-8000-000000000001",
        project_id=PROJECT_ID,
        version=1,
        brief_version=1,
        payload_json=json.dumps(
            {
                "title": project.name,
                "logline": project.idea,
                "total_duration_sec": 60,
                "scenes": [
                    {"code": "01", "title": "坠落", "duration_sec": 18},
                    {"code": "02", "title": "觉醒", "duration_sec": 22},
                    {"code": "03", "title": "重新选择", "duration_sec": 20},
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        provider="mock",
        model="deterministic-v1",
        config_version="proposal-v1",
        status="APPROVED",
        approved_at=project.created_at,
        approved_by="seed",
        created_at=project.created_at,
    )

    reference_content = deterministic_png_bytes(480, 640, "lin-yue-canonical-look-v1")
    reference_digest = sha256(reference_content).hexdigest()
    reference_key = "assets/demo/lin-yue-look-v1.png"
    reference_path = settings.data_dir / reference_key
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    if not reference_path.exists():
        reference_path.write_bytes(reference_content)
    character_asset = Asset(
        id=CHARACTER_ASSET_ID,
        project_id=PROJECT_ID,
        kind="character_candidate",
        storage_key=reference_key,
        sha256=reference_digest,
        mime="image/png",
        size_bytes=len(reference_content),
        status="READY",
        provider="mock",
        is_temporary=False,
        width=480,
        height=640,
        duration_ms=None,
        original_filename="lin-yue-look-v1.png",
        metadata_json=json.dumps({"demo": True, "look_version": "Look V1"}),
        rights_status="RESTRICTED_DEMO",
        source_entity_type="character_candidate",
        source_entity_id=CHARACTER_CANDIDATE_ID,
        created_at=project.created_at,
    )
    character = Character(
        id=CHARACTER_ID,
        project_id=PROJECT_ID,
        character_key="protagonist",
        name="林悦",
        role="PROTAGONIST",
        visual_brief=(
            "30岁左右的中国女性，椭圆脸，清晰眉眼与自然黑色中长发；"
            "神态克制坚定，现实电影感，保持年龄感、脸型和五官比例稳定。"
        ),
        status="LOCKED",
        locked_candidate_id=CHARACTER_CANDIDATE_ID,
        lock_version=1,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
    character_candidate = CharacterCandidate(
        id=CHARACTER_CANDIDATE_ID,
        project_id=PROJECT_ID,
        character_id=CHARACTER_ID,
        ordinal=1,
        asset_id=CHARACTER_ASSET_ID,
        seed="lin-yue-look-v1",
        status="READY",
        selected=True,
        created_at=project.created_at,
    )

    sister_content = deterministic_png_bytes(480, 640, "lin-xi-canonical-look-v1")
    sister_digest = sha256(sister_content).hexdigest()
    sister_key = "assets/demo/lin-xi-look-v1.png"
    sister_path = settings.data_dir / sister_key
    sister_path.parent.mkdir(parents=True, exist_ok=True)
    if not sister_path.exists():
        sister_path.write_bytes(sister_content)
    sister_asset = Asset(
        id=SISTER_ASSET_ID,
        project_id=PROJECT_ID,
        kind="character_candidate",
        storage_key=sister_key,
        sha256=sister_digest,
        mime="image/png",
        size_bytes=len(sister_content),
        status="READY",
        provider="mock",
        is_temporary=False,
        width=480,
        height=640,
        duration_ms=None,
        original_filename="lin-xi-look-v1.png",
        metadata_json=json.dumps({"demo": True, "look_version": "Look V1"}),
        rights_status="RESTRICTED_DEMO",
        source_entity_type="character_candidate",
        source_entity_id=SISTER_CANDIDATE_ID,
        created_at=project.created_at,
    )
    sister = Character(
        id=SISTER_CHARACTER_ID,
        project_id=PROJECT_ID,
        character_key="younger-sister",
        name="林溪",
        role="SUPPORTING",
        visual_brief=(
            "26岁左右的中国女性，林悦的妹妹；短发利落，神态温和但行动果断，"
            "清晨穿浅色针织外套，保持脸型、发型与年龄感稳定。"
        ),
        status="LOCKED",
        locked_candidate_id=SISTER_CANDIDATE_ID,
        lock_version=1,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
    sister_candidate = CharacterCandidate(
        id=SISTER_CANDIDATE_ID,
        project_id=PROJECT_ID,
        character_id=SISTER_CHARACTER_ID,
        ordinal=1,
        asset_id=current_shot_asset_id(session, SHOT_IDS[2]) or SISTER_ASSET_ID,
        seed="lin-xi-look-v1",
        status="READY",
        selected=True,
        created_at=project.created_at,
    )

    customer_content = deterministic_png_bytes(480, 640, "first-customer-look-v1")
    customer_digest = sha256(customer_content).hexdigest()
    customer_key = "assets/demo/first-customer-look-v1.png"
    customer_path = settings.data_dir / customer_key
    customer_path.parent.mkdir(parents=True, exist_ok=True)
    if not customer_path.exists():
        customer_path.write_bytes(customer_content)
    customer_asset = Asset(
        id=CUSTOMER_ASSET_ID,
        project_id=PROJECT_ID,
        kind="character_candidate",
        storage_key=customer_key,
        sha256=customer_digest,
        mime="image/png",
        size_bytes=len(customer_content),
        status="READY",
        provider="mock",
        is_temporary=False,
        width=480,
        height=640,
        duration_ms=None,
        original_filename="first-customer-look-v1.png",
        metadata_json=json.dumps({"demo": True, "look_version": "Look V1"}),
        rights_status="RESTRICTED_DEMO",
        source_entity_type="character_candidate",
        source_entity_id=CUSTOMER_CANDIDATE_ID,
        created_at=project.created_at,
    )
    customer = Character(
        id=CUSTOMER_CHARACTER_ID,
        project_id=PROJECT_ID,
        character_key="first-customer",
        name="第一位客人",
        role="SUPPORTING",
        visual_brief=(
            "20岁出头的中国女性，初入职场，长发低束，略显疲惫和迟疑；"
            "穿简洁通勤外套，保持五官、发型与服装连续。"
        ),
        status="LOCKED",
        locked_candidate_id=CUSTOMER_CANDIDATE_ID,
        lock_version=1,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
    customer_candidate = CharacterCandidate(
        id=CUSTOMER_CANDIDATE_ID,
        project_id=PROJECT_ID,
        character_id=CUSTOMER_CHARACTER_ID,
        ordinal=1,
        asset_id=current_shot_asset_id(session, SHOT_IDS[5]) or CUSTOMER_ASSET_ID,
        seed="first-customer-look-v1",
        status="READY",
        selected=True,
        created_at=project.created_at,
    )

    add_if_missing(session, project)
    session.flush()
    for asset in (character_asset, sister_asset, customer_asset):
        add_if_missing(session, asset)
    for stored_character in (character, sister, customer):
        add_if_missing(session, stored_character)
    session.flush()
    for candidate in (character_candidate, sister_candidate, customer_candidate):
        add_if_missing(session, candidate)
    session.flush()
    for candidate, shot_id in (
        (sister_candidate, SHOT_IDS[2]),
        (customer_candidate, SHOT_IDS[5]),
    ):
        stored_candidate = session.get(CharacterCandidate, candidate.id)
        shot_asset_id = current_shot_asset_id(session, shot_id)
        if stored_candidate is not None and shot_asset_id is not None:
            stored_candidate.asset_id = shot_asset_id
    add_if_missing(session, brief)
    add_if_missing(session, proposal)
    add_if_missing(session, episode)
    session.flush()
    for scene in scenes:
        add_if_missing(session, scene)
    session.flush()
    for shot in shots:
        add_if_missing(session, shot)
        stored_shot = session.get(Shot, shot.id)
        expected_character_ids = SHOT_CHARACTER_IDS.get(shot.id, [CHARACTER_ID])
        if stored_shot is not None:
            try:
                stored_character_ids = json.loads(stored_shot.character_ids_json or "[]")
            except json.JSONDecodeError:
                stored_character_ids = []
            if not isinstance(stored_character_ids, list):
                stored_character_ids = []
            merged_character_ids = list(
                dict.fromkeys([*stored_character_ids, *expected_character_ids])
            )
            stored_shot.character_ids_json = json.dumps(merged_character_ids)
            stored_shot.character_look_version = "Look V1"
    for job in jobs:
        add_if_missing(session, job)
    session.commit()


def main() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    factory = sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)
    with factory() as session:
        seed_database(session)
    print(f"Seed complete: {PROJECT_ID}")


if __name__ == "__main__":
    main()
