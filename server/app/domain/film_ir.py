from typing import Any, Literal

from pydantic import BaseModel, Field

FilmIRCanonicalKind = Literal["CANONICAL", "DERIVED", "GENERATED"]


class FilmIRReference(BaseModel):
    type: str
    id: str
    version_id: str | None = None


class FilmIRSource(BaseModel):
    table: str
    row_id: str
    id_strategy: Literal["PERSISTED", "VERSION_SCOPED_DERIVED"]


class FilmIREdge(BaseModel):
    source: FilmIRReference
    target: FilmIRReference
    relation: str
    inferred: bool = False
    evidence: str


class FilmIRObject(BaseModel):
    type: str
    id: str
    version_id: str | None = None
    canonical_kind: FilmIRCanonicalKind
    canonical_status: str
    approval_status: str
    parent: list[FilmIRReference] = Field(default_factory=list)
    children: list[FilmIRReference] = Field(default_factory=list)
    upstream: list[FilmIRReference] = Field(default_factory=list)
    downstream: list[FilmIRReference] = Field(default_factory=list)
    source: FilmIRSource
    attributes: dict[str, Any] = Field(default_factory=dict)


class FilmIRWarning(BaseModel):
    code: str
    message: str
    object_refs: list[FilmIRReference] = Field(default_factory=list)


class FilmIRProjection(BaseModel):
    schema_version: Literal["film-ir-projection-v1"] = "film-ir-projection-v1"
    project_id: str
    project_lock_version: int
    objects: list[FilmIRObject]
    edges: list[FilmIREdge]
    warnings: list[FilmIRWarning] = Field(default_factory=list)
