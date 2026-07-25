import type { Edge, Node, Viewport } from '@xyflow/react'
import type { CanvasProjection, CanvasReference } from '../api/client'

const NODE_WIDTH = 232
const NODE_HEIGHT = 108
const COLUMN_GAP = 304
const ROW_GAP = 148

const TYPE_COLUMN: Record<string, number> = {
  Project: 0,
  Story: 1,
  Script: 1,
  DirectorProposal: 1,
  Beat: 2,
  ScriptScene: 2,
  DialogueLine: 2,
  Scene: 3,
  Character: 3,
  Location: 3,
  Prop: 3,
  Storyboard: 4,
  Shot: 4,
  Timeline: 5,
}

export interface FilmCanvasNodeData extends Record<string, unknown> {
  projection: CanvasProjection['nodes'][number]
}

export type FilmCanvasNode = Node<FilmCanvasNodeData, 'filmObject'>
export type FilmCanvasEdge = Edge

export interface FilmCanvasNodeViewState {
  x: number
  y: number
  width: number
  height: number
  group: string | null
  z_index: number
  collapsed: boolean
}

export interface FilmCanvasViewState {
  schema_version: 'film-canvas-view-state-v1'
  project_id: string
  projection_lock_version: number
  viewport: Viewport
  nodes: Record<string, FilmCanvasNodeViewState>
  selected: Array<{
    type: string
    id: string
    version_id: string | null
  }>
}

export interface FilmCanvasGraph {
  nodes: FilmCanvasNode[]
  edges: FilmCanvasEdge[]
  viewport: Viewport | null
}

export function canvasNodeId(ref: CanvasReference): string {
  return `${ref.type}:${ref.id}`
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function parseReference(value: unknown): FilmCanvasViewState['selected'][number] | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Record<string, unknown>
  if (typeof candidate.type !== 'string' || typeof candidate.id !== 'string') return null
  return {
    type: candidate.type,
    id: candidate.id,
    version_id: typeof candidate.version_id === 'string' ? candidate.version_id : null,
  }
}

export function parseCanvasViewState(
  serialized: string | null,
  projectId: string,
): FilmCanvasViewState | null {
  if (!serialized) return null
  try {
    const value = JSON.parse(serialized) as Record<string, unknown>
    if (
      value.schema_version !== 'film-canvas-view-state-v1'
      || value.project_id !== projectId
      || !isFiniteNumber(value.projection_lock_version)
    ) return null

    const rawViewport = value.viewport as Record<string, unknown> | undefined
    if (
      !rawViewport
      || !isFiniteNumber(rawViewport.x)
      || !isFiniteNumber(rawViewport.y)
      || !isFiniteNumber(rawViewport.zoom)
      || rawViewport.zoom <= 0
    ) return null

    const nodes: Record<string, FilmCanvasNodeViewState> = {}
    const rawNodes = value.nodes
    if (rawNodes && typeof rawNodes === 'object' && !Array.isArray(rawNodes)) {
      for (const [id, rawNode] of Object.entries(rawNodes)) {
        if (!rawNode || typeof rawNode !== 'object' || Array.isArray(rawNode)) continue
        const candidate = rawNode as Record<string, unknown>
        if (
          !isFiniteNumber(candidate.x)
          || !isFiniteNumber(candidate.y)
          || !isFiniteNumber(candidate.width)
          || !isFiniteNumber(candidate.height)
          || candidate.width <= 0
          || candidate.height <= 0
        ) continue
        nodes[id] = {
          x: candidate.x,
          y: candidate.y,
          width: candidate.width,
          height: candidate.height,
          group: typeof candidate.group === 'string' ? candidate.group : null,
          z_index: isFiniteNumber(candidate.z_index) ? candidate.z_index : 0,
          collapsed: candidate.collapsed === true,
        }
      }
    }

    const selected = Array.isArray(value.selected)
      ? value.selected
        .map(parseReference)
        .filter((ref): ref is FilmCanvasViewState['selected'][number] => ref !== null)
      : []
    return {
      schema_version: 'film-canvas-view-state-v1',
      project_id: projectId,
      projection_lock_version: value.projection_lock_version,
      viewport: {
        x: rawViewport.x,
        y: rawViewport.y,
        zoom: rawViewport.zoom,
      },
      nodes,
      selected,
    }
  } catch {
    return null
  }
}

export function projectCanvasGraph(
  projection: CanvasProjection,
  viewState: FilmCanvasViewState | null,
): FilmCanvasGraph {
  const rowByColumn = new Map<number, number>()
  const nodes = projection.nodes.map<FilmCanvasNode>((item) => {
    const id = canvasNodeId(item.ref)
    const column = TYPE_COLUMN[item.ref.type] ?? 3
    const row = rowByColumn.get(column) ?? 0
    rowByColumn.set(column, row + 1)
    const saved = viewState?.nodes[id]
    return {
      id,
      type: 'filmObject',
      position: saved
        ? { x: saved.x, y: saved.y }
        : { x: column * COLUMN_GAP, y: row * ROW_GAP },
      data: { projection: item },
      draggable: true,
      connectable: false,
      deletable: false,
      selectable: true,
      selected: viewState?.selected.some(
        (ref) => canvasNodeId({ type: ref.type, id: ref.id, versionId: ref.version_id }) === id,
      ) ?? false,
      zIndex: saved?.z_index ?? 0,
    }
  })
  const visibleNodeIds = new Set(nodes.map((node) => node.id))
  const edges = projection.edges
    .map<FilmCanvasEdge>((edge, index) => ({
      id: `${canvasNodeId(edge.source)}-${edge.relation}-${canvasNodeId(edge.target)}-${index}`,
      source: canvasNodeId(edge.source),
      target: canvasNodeId(edge.target),
      label: edge.relation,
      animated: false,
      selectable: false,
      deletable: false,
      data: { inferred: edge.inferred },
    }))
    .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))

  return {
    nodes,
    edges,
    viewport: viewState?.viewport ?? null,
  }
}

export function createCanvasViewState(
  projection: CanvasProjection,
  nodes: FilmCanvasNode[],
  viewport: Viewport,
  selected: CanvasReference[],
): FilmCanvasViewState {
  return {
    schema_version: 'film-canvas-view-state-v1',
    project_id: projection.projectId,
    projection_lock_version: projection.projectLockVersion,
    viewport,
    nodes: Object.fromEntries(nodes.map((node) => [
      node.id,
      {
        x: node.position.x,
        y: node.position.y,
        width: node.measured?.width ?? NODE_WIDTH,
        height: node.measured?.height ?? NODE_HEIGHT,
        group: node.data.projection.groupKey,
        z_index: node.zIndex ?? 0,
        collapsed: false,
      },
    ])),
    selected: selected.map((ref) => ({
      type: ref.type,
      id: ref.id,
      version_id: ref.versionId,
    })),
  }
}
