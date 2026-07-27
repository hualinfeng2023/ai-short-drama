import type { Edge, Node, Viewport } from '@xyflow/react'
import type { CanvasProjection, CanvasReference } from '../api/client'

const NODE_WIDTH = 232
const NODE_HEIGHT = 108
const COLUMN_GAP = 304
const ROW_GAP = 148
const EMPTY_LANE_HEIGHT = 552

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

const relationLabels: Record<string, string> = {
  ACTIVE_CHARACTER_VERSION: '当前角色版本',
  APPEARS_IN_BEAT: '出场',
  APPEARS_IN_SHOT: '出现在镜头中',
  BEAT_TO_SCENE: '对应制作场景',
  BEAT_TO_SCRIPT_SCENE: '对应剧本场次',
  CONTAINS: '包含',
  CONTAINS_CLIP: '包含时间片段',
  CONTAINS_DIALOGUE: '包含对白',
  CONTAINS_SCENE: '包含场景',
  CONTAINS_SHOT: '包含镜头',
  CURRENT_DERIVED_TIMELINE: '当前派生时间线',
  CURRENT_STORY: '当前故事',
  DERIVES_BEAT: '生成剧情关键点',
  DERIVES_STORYBOARD: '生成故事板',
  DIALOGUE_TO_AUDIO: '对白对应音频',
  EVALUATED_SCRIPT_SCENE: '评估剧本场次',
  GENERATED_ASSET: '生成素材',
  GENERATED_AUDIO_TAKE: '生成音频版本',
  GENERATED_TAKE: '生成镜头版本',
  INVALIDATES: '需要重新检查',
  LOCATION_FOR_SHOT: '镜头地点',
  NEXT_TAKE_VERSION: '下一镜头版本',
  OUTPUT_ASSET: '输出素材',
  PRESERVES: '保持不变',
  PRODUCED_AUDIO_TAKE: '产出音频版本',
  PRODUCED_TAKE: '产出镜头版本',
  PROPOSES_CHANGE_TO: '建议修改',
  PROP_FOR_SHOT: '镜头道具',
  REALIZED_AS_SCENE: '落实为制作场景',
  RETRY_OF: '重试自',
  SCENE_TO_AUDIO: '场景对应音频',
  SHOT_TO_AUDIO: '镜头对应音频',
  SNAPSHOTTED_BY_SHOT: '由镜头快照记录',
  SPECIFIES_SHOT: '定义镜头',
  STORY_TO_BEAT: '故事拆分为关键点',
  STORY_TO_SCRIPT: '故事生成剧本',
  USED_BY_TIMELINE: '用于时间线',
}

export interface FilmCanvasNodeData extends Record<string, unknown> {
  projection: CanvasProjection['nodes'][number]
  speakerLabel: string | null
}

export type FilmCanvasNode = Node<FilmCanvasNodeData, 'filmObject'>
export interface FilmCanvasEmptyLaneNodeData extends Record<string, unknown> {
  description: string
  label: string
}

export type FilmCanvasEmptyLaneNode = Node<FilmCanvasEmptyLaneNodeData, 'emptyLane'>
export type FilmCanvasGraphNode = FilmCanvasNode | FilmCanvasEmptyLaneNode
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
  nodes: FilmCanvasGraphNode[]
  edges: FilmCanvasEdge[]
  viewport: Viewport | null
}

export interface DirectorReviewTarget {
  targetType: 'SCRIPT_SCENE' | 'SCENE'
  targetId: string
  scriptSceneId: string
}

export function canvasNodeId(ref: CanvasReference): string {
  return `${ref.type}:${ref.id}`
}

export function isFilmObjectNode(node: FilmCanvasGraphNode): node is FilmCanvasNode {
  return node.type === 'filmObject'
}

export function getCanvasRelationLabel(relation: string): string {
  return relationLabels[relation] ?? relation
}

export function canvasProjectionSignature(projection: CanvasProjection): string {
  const nodes = projection.nodes.map((node) => [
    node.ref.type,
    node.ref.id,
    node.ref.versionId,
    node.canonicalStatus,
    node.approvalStatus,
    node.label,
    node.contentSummary,
    node.operationContext,
  ])
  const edges = projection.edges.map((edge) => [
    edge.source.type,
    edge.source.id,
    edge.source.versionId,
    edge.relation,
    edge.target.type,
    edge.target.id,
    edge.target.versionId,
    edge.inferred,
  ])
  return JSON.stringify([projection.projectLockVersion, nodes, edges])
}

export function resolveDirectorReviewTarget(
  projection: CanvasProjection,
  selected: CanvasReference,
): DirectorReviewTarget | null {
  if (selected.type === 'ScriptScene' && selected.versionId) {
    return {
      targetType: 'SCRIPT_SCENE',
      targetId: selected.versionId,
      scriptSceneId: selected.versionId,
    }
  }
  if (selected.type !== 'Scene') return null

  const lineage = projection.edges.find(
    (edge) => edge.relation === 'REALIZED_AS_SCENE'
      && edge.source.type === 'ScriptScene'
      && edge.target.type === 'Scene'
      && edge.target.id === selected.id,
  )
  if (!lineage) return null
  const scriptScene = projection.nodes.find(
    (node) => node.ref.type === 'ScriptScene' && node.ref.id === lineage.source.id,
  )
  if (!scriptScene?.ref.versionId) return null
  return {
    targetType: 'SCENE',
    targetId: selected.id,
    scriptSceneId: scriptScene.ref.versionId,
  }
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
  const characterLabels = new Map(
    projection.nodes
      .filter((item) => item.ref.type === 'Character')
      .map((item) => [item.operationContext.character_key, item.label])
      .filter(
        (entry): entry is [string, string] => typeof entry[0] === 'string' && Boolean(entry[0]),
      ),
  )
  const objectNodes = projection.nodes.map<FilmCanvasNode>((item) => {
    const id = canvasNodeId(item.ref)
    const column = TYPE_COLUMN[item.ref.type] ?? 3
    const row = rowByColumn.get(column) ?? 0
    rowByColumn.set(column, row + 1)
    const saved = viewState?.nodes[id]
    const lineType = item.ref.type === 'DialogueLine'
      && typeof item.operationContext.line_type === 'string'
      ? item.operationContext.line_type
      : null
    const speakerKey = item.ref.type === 'DialogueLine'
      && ['DIALOGUE', 'VOICE_OVER'].includes(lineType ?? '')
      && typeof item.operationContext.speaker_key === 'string'
      ? item.operationContext.speaker_key
      : null
    return {
      id,
      type: 'filmObject',
      position: saved
        ? { x: saved.x, y: saved.y }
        : { x: column * COLUMN_GAP, y: row * ROW_GAP },
      data: {
        projection: item,
        speakerLabel: speakerKey ? characterLabels.get(speakerKey) ?? speakerKey : null,
      },
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
  const hasStoryboardContent = projection.nodes.some(
    (item) => item.ref.type === 'Storyboard' || item.ref.type === 'Shot',
  )
  const nodes: FilmCanvasGraphNode[] = hasStoryboardContent
    ? objectNodes
    : [
      ...objectNodes,
      {
        id: 'canvas-empty-lane:storyboard',
        type: 'emptyLane',
        position: { x: TYPE_COLUMN.Storyboard * COLUMN_GAP, y: 0 },
        data: {
          label: '分镜区域',
          description: '剧本批准后，故事板与镜头会出现在这里。',
        },
        draggable: false,
        connectable: false,
        deletable: false,
        selectable: false,
        style: { width: NODE_WIDTH, height: EMPTY_LANE_HEIGHT },
      },
    ]
  const visibleNodeIds = new Set(nodes.map((node) => node.id))
  const edges = projection.edges
    .map<FilmCanvasEdge>((edge, index) => ({
      id: `${canvasNodeId(edge.source)}-${edge.relation}-${canvasNodeId(edge.target)}-${index}`,
      source: canvasNodeId(edge.source),
      target: canvasNodeId(edge.target),
      label: getCanvasRelationLabel(edge.relation),
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
  nodes: FilmCanvasGraphNode[],
  viewport: Viewport,
  selected: CanvasReference[],
): FilmCanvasViewState {
  return {
    schema_version: 'film-canvas-view-state-v1',
    project_id: projection.projectId,
    projection_lock_version: projection.projectLockVersion,
    viewport,
    nodes: Object.fromEntries(nodes.filter(isFilmObjectNode).map((node) => [
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
