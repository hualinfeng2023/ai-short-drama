import re

from app.domain.canvas import (
    CanvasEdgeProjection,
    CanvasNodeProjection,
    CanvasProjection,
    CanvasViewStateContract,
)
from app.domain.film_ir import FilmIRObject, FilmIRProjection, FilmIRReference

MAX_BEAT_LABEL_LENGTH = 18

VISIBLE_TYPES = {
    "Project",
    "Story",
    "Script",
    "Beat",
    "ScriptScene",
    "DialogueLine",
    "Scene",
    "Character",
    "Location",
    "Prop",
    "Storyboard",
    "Shot",
    "Timeline",
    "DirectorProposal",
}


def _beat_label(attributes: dict[str, object]) -> str:
    explicit_title = attributes.get("title")
    source = explicit_title if isinstance(explicit_title, str) else attributes.get("summary")
    if not isinstance(source, str) or not source.strip():
        source = attributes.get("description")
    if not isinstance(source, str) or not source.strip():
        return "剧情关键点"

    normalized = " ".join(source.split()).strip()
    first_clause = re.split(r"[，。；！？、]", normalized, maxsplit=1)[0].strip()
    candidate = first_clause or normalized
    if len(candidate) > MAX_BEAT_LABEL_LENGTH:
        semantic_prefix = re.split(r"(?=准备|随后|同时|开始|并且|并)", candidate, maxsplit=1)[0].strip()
        if 6 <= len(semantic_prefix) <= MAX_BEAT_LABEL_LENGTH:
            candidate = semantic_prefix
    return candidate[:MAX_BEAT_LABEL_LENGTH]


def _label(item: FilmIRObject) -> str:
    attributes = item.attributes
    if item.type == "Beat":
        return _beat_label(attributes)
    for field in ("name", "title", "heading", "summary", "text", "character_key"):
        value = attributes.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    ordinal = attributes.get("ordinal") or attributes.get("sequence")
    return f"{item.type} {ordinal}" if ordinal is not None else item.type


def _group_key(item: FilmIRObject) -> str:
    attributes = item.attributes
    episode = attributes.get("episode_ordinal")
    if episode is not None:
        return f"episode:{episode}"
    if item.type in {"Character", "Location", "Prop"}:
        return "world"
    if item.type in {"Timeline"}:
        return "timeline"
    if item.type == "DirectorProposal":
        return "director"
    return "project"


def _detail_route(project_id: str, item: FilmIRObject) -> str:
    if item.type == "Scene":
        return f"/projects/{project_id}/episodes/{item.attributes['episode_id']}"
    routes = {
        "Project": f"/projects/{project_id}",
        "Story": f"/projects/{project_id}/story",
        "Script": f"/projects/{project_id}/story",
        "Beat": f"/projects/{project_id}/story",
        "ScriptScene": f"/projects/{project_id}/story",
        "DialogueLine": f"/projects/{project_id}/story",
        "Character": f"/projects/{project_id}/characters",
        "Location": f"/projects/{project_id}/preproduction",
        "Prop": f"/projects/{project_id}/preproduction",
        "Storyboard": f"/projects/{project_id}/storyboard",
        "Shot": f"/projects/{project_id}/storyboard",
        "Timeline": f"/projects/{project_id}/production",
        "DirectorProposal": f"/projects/{project_id}/story",
    }
    return routes[item.type]


def _thumbnail_url(item: FilmIRObject) -> str | None:
    if item.type != "Character":
        return None
    asset_id = item.attributes.get("thumbnail_asset_id")
    return f"/api/v1/assets/{asset_id}/content" if isinstance(asset_id, str) and asset_id else None


def _content_summary(item: FilmIRObject) -> str | None:
    if item.type not in {"Beat", "DirectorProposal", "Script", "ScriptScene", "Shot"}:
        return None
    for field in ("summary", "description", "purpose"):
        value = item.attributes.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _operation_context(item: FilmIRObject) -> dict[str, object]:
    allowed_fields = {
        "Character": {"character_key", "role"},
        "DialogueLine": {"line_type", "speaker_key"},
        "Shot": {
            "camera_movement",
            "description",
            "dialogue",
            "shot_lock_version",
            "shot_size",
        },
        "Storyboard": {"episode_ordinal", "version"},
    }.get(item.type, set())
    return {
        field: item.attributes[field]
        for field in allowed_fields
        if item.attributes.get(field) is not None
    }


def get_canvas_projection(film_ir: FilmIRProjection) -> CanvasProjection:
    visible = {(item.type, item.id): item for item in film_ir.objects if item.type in VISIBLE_TYPES}
    nodes = [
        CanvasNodeProjection(
            ref=FilmIRReference(type=item.type, id=item.id, version_id=item.version_id),
            canonical_kind=item.canonical_kind,
            canonical_status=item.canonical_status,
            approval_status=item.approval_status,
            label=_label(item),
            content_summary=_content_summary(item),
            group_key=_group_key(item),
            detail_route=_detail_route(film_ir.project_id, item),
            thumbnail_url=_thumbnail_url(item),
            operation_context=_operation_context(item),
            read_only=True,
        )
        for item in visible.values()
    ]
    edges = [
        CanvasEdgeProjection(
            source=edge.source,
            target=edge.target,
            relation=edge.relation,
            inferred=edge.inferred,
        )
        for edge in film_ir.edges
        if (edge.source.type, edge.source.id) in visible
        and (edge.target.type, edge.target.id) in visible
    ]
    return CanvasProjection(
        project_id=film_ir.project_id,
        project_lock_version=film_ir.project_lock_version,
        nodes=sorted(nodes, key=lambda item: (item.ref.type, item.ref.id)),
        edges=sorted(
            edges,
            key=lambda item: (
                item.source.type,
                item.source.id,
                item.target.type,
                item.target.id,
                item.relation,
            ),
        ),
        view_state_contract=CanvasViewStateContract(
            allowed_fields=[
                "x",
                "y",
                "width",
                "height",
                "group",
                "z_index",
                "collapsed",
                "selected",
                "viewport.x",
                "viewport.y",
                "viewport.zoom",
            ],
            forbidden_business_fields=[
                "canonical_status",
                "approval_status",
                "version_id",
                "prompt",
                "asset_url",
                "timeline_clip",
                "domain_payload",
            ],
        ),
    )
