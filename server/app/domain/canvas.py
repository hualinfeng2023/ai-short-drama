from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.film_ir import FilmIRReference


class CanvasViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, gt=0)


class CanvasNodeViewState(BaseModel):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    group: str | None = None
    z_index: int = 0
    collapsed: bool = False


class CanvasViewState(BaseModel):
    schema_version: Literal["film-canvas-view-state-v1"] = "film-canvas-view-state-v1"
    project_id: str
    projection_lock_version: int
    viewport: CanvasViewport = Field(default_factory=CanvasViewport)
    nodes: dict[str, CanvasNodeViewState] = Field(default_factory=dict)
    selected: list[FilmIRReference] = Field(default_factory=list)


class CanvasNodeProjection(BaseModel):
    ref: FilmIRReference
    canonical_kind: str
    canonical_status: str
    approval_status: str
    label: str
    content_summary: str | None = None
    group_key: str
    detail_route: str
    thumbnail_url: str | None = None
    operation_context: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True


class CanvasEdgeProjection(BaseModel):
    source: FilmIRReference
    target: FilmIRReference
    relation: str
    inferred: bool


class CanvasViewStateContract(BaseModel):
    schema_version: Literal["film-canvas-view-state-v1"] = "film-canvas-view-state-v1"
    persistence: Literal["CLIENT_LOCAL"] = "CLIENT_LOCAL"
    allowed_fields: list[str]
    forbidden_business_fields: list[str]


class CanvasProjection(BaseModel):
    schema_version: Literal["film-canvas-projection-v1"] = "film-canvas-projection-v1"
    project_id: str
    project_lock_version: int
    source_projection: Literal["film-ir-projection-v1"] = "film-ir-projection-v1"
    nodes: list[CanvasNodeProjection]
    edges: list[CanvasEdgeProjection]
    view_state_contract: CanvasViewStateContract
