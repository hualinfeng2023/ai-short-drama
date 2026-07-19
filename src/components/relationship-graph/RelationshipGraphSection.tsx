import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from 'react'
import {
  Check,
  ChevronDown,
  Clock3,
  GitCompare,
  List,
  LockKeyhole,
  LoaderCircle,
  Maximize2,
  Minus,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
  UnlockKeyhole,
  Workflow,
} from 'lucide-react'
import {
  ApiError,
  approveRelationshipGraph,
  analyzeRelationshipRevisionImpact,
  createRelationshipGraphRevision,
  fetchRelationshipGraphDiff,
  fetchRelationshipGraphValidation,
  generateRelationshipUpbringingSuggestion,
  saveRelationshipGraph,
  setRelationshipGraphEdgeLock,
  submitRelationshipGraph,
  withdrawRelationshipGraph,
  type RelationshipBeatRecord,
  type RelationshipEdgeRecord,
  type FamilyRelationType,
  type RelationshipGraphPayloadRecord,
  type RelationshipGraphDiff,
  type RelationshipRevisionImpact,
  type RelationshipGraphValidationIssue,
  type RelationshipGraphVersionRecord,
  type RelationshipStateRecord,
  type RelationshipType,
  type RelationshipUpbringingSuggestion,
  type SharedUpbringing,
} from '../../api/client'
import { Button, Modal, SelectControl, StatusBadge } from '../ui'
import {
  createRelationshipDraftState,
  removeRelationshipBeatFromDraft,
  syncRelationshipDraftState,
  updateRelationshipDraft,
} from './relationshipGraphState'

export interface RelationshipCharacter {
  key: string
  name: string
  role: string
  desire: string
  fear: string
  secret: string
  dramaticFunction: string
  age: string
  occupation: string
  personality: string
}

interface RelationshipGraphSectionProps {
  versions: RelationshipGraphVersionRecord[]
  characters: RelationshipCharacter[]
  onGraphChanged: (graph: RelationshipGraphVersionRecord) => void
  onCharacterVisualsReady: (route: string, characterCount: number) => void
  focusTarget?: {
    graphId: string
    relationshipKey: string
    beatOrdinal: number
    requestId: number
  } | null
}

const RELATIONSHIP_TYPE_LABELS: Record<RelationshipType, string> = {
  FAMILY: '亲属',
  ROMANTIC: '情感',
  FRIENDSHIP: '朋友',
  ALLY: '盟友',
  RIVAL: '对手',
  AUTHORITY: '权威',
  DEPENDENCY: '依附',
  DEBT: '债务',
  CONTROL: '控制',
  SECRET: '秘密',
  OTHER: '其他',
}

const FAMILY_RELATION_LABELS: Record<FamilyRelationType, string> = {
  UNSPECIFIED: '未明确血缘来源',
  BIOLOGICAL_PARENT_CHILD: '亲生父母 / 子女',
  BIOLOGICAL_GRANDPARENT_GRANDCHILD: '亲生祖辈 / 孙辈',
  FULL_SIBLINGS: '同父同母兄弟姐妹',
  PATERNAL_HALF_SIBLINGS: '同父异母兄弟姐妹',
  MATERNAL_HALF_SIBLINGS: '同母异父兄弟姐妹',
  IDENTICAL_TWINS: '同卵双胞胎',
  FRATERNAL_TWINS: '异卵双胞胎',
  ADOPTIVE_PARENT_CHILD: '养父母 / 养子女（非血缘）',
  STEP_PARENT_CHILD: '继父母 / 继子女（非血缘）',
  IN_LAW: '姻亲（非血缘）',
  OTHER_NON_BIOLOGICAL: '其他非血缘亲属',
}

const SHARED_UPBRINGING_LABELS: Record<SharedUpbringing, string> = {
  SAME_HOUSEHOLD: '长期共同生活',
  PARTIAL: '部分共同成长',
  SEPARATE: '成长环境分离',
  UNKNOWN: '尚不明确',
}

const TRIGGER_LABELS: Record<RelationshipBeatRecord['triggerType'], string> = {
  STORY_EVENT: '剧情事件',
  MISJUDGMENT: '误判',
  AUTHENTICATION: '认证',
  REVEAL: '揭示',
  CHOICE: '选择',
  BETRAYAL: '背叛',
  PAYOFF: '兑现',
}

const VISIBILITY_LABELS: Record<RelationshipBeatRecord['audienceVisibility'], string> = {
  HIDDEN: '观众未知',
  PARTIAL: '部分可见',
  REVEALED: '完全揭示',
}

const SAVE_LABELS = {
  saved: '已保存',
  dirty: '有未保存修改',
  saving: '正在保存',
  failed: '保存失败',
  conflict: '版本冲突',
} as const

const DIFF_FIELD_LABELS: Record<string, string> = {
  relationship_types: '关系类型',
  family_kinship: '血缘与成长环境',
  surface_relationship: '明面关系',
  true_relationship: '真实关系',
  source_view: '前者认知',
  target_view: '后者认知',
  trust_level: '信任',
  emotional_temperature: '情感温度',
  power_balance: '权力平衡',
  conflict_intensity: '冲突强度',
  story_function: '剧情功能',
  secret: '关系秘密',
  is_core: '核心关系',
  locked: '核心锁定',
  sequence: '变化顺序',
  scene_ordinal: '关联场景',
  trigger_type: '触发类型',
  trigger_ref: '触发引用',
  before_state: '变化前状态',
  after_state: '变化后状态',
  evidence: '触发证据',
  emotional_consequence: '情绪后果',
  audience_visibility: '观众可见范围',
}

const VALIDATION_CODE_LABELS: Record<string, string> = {
  INVALID_CHARACTER_REFERENCE: '角色引用无效',
  CORE_CHARACTER_ISOLATED: '核心角色孤立',
  MISSING_CORE_RELATIONSHIP: '缺少核心关系',
  MISSING_PRIMARY_CONFLICT: '缺少主冲突',
  MISSING_RELATIONSHIP_BEAT: '缺少关系变化',
  HIDDEN_RELATIONSHIP_WITHOUT_REVEAL: '缺少揭示计划',
  FAMILY_KINSHIP_UNSPECIFIED: '血缘来源未标记',
  CORE_RELATIONSHIP_WITHOUT_BEAT: '核心关系缺少变化',
}

function characterName(characters: RelationshipCharacter[], key: string): string {
  return characters.find((character) => character.key === key)?.name ?? key
}

function edgeLabel(edge: RelationshipEdgeRecord, layer: 'surface' | 'truth'): string {
  return layer === 'surface' ? edge.surfaceRelationship : edge.trueRelationship
}

function wrapRelationshipLabel(label: string, charactersPerLine = 16): string[] {
  const characters = Array.from(label.trim())
  if (characters.length === 0) return ['—']
  const lines: string[] = []
  for (let index = 0; index < characters.length; index += charactersPerLine) {
    lines.push(characters.slice(index, index + charactersPerLine).join(''))
  }
  return lines
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value))
}

function edgeState(edge: RelationshipEdgeRecord): RelationshipStateRecord {
  return {
    surfaceRelationship: edge.surfaceRelationship,
    trueRelationship: edge.trueRelationship,
    trustLevel: edge.trustLevel,
    emotionalTemperature: edge.emotionalTemperature,
    powerBalance: edge.powerBalance,
    conflictIntensity: edge.conflictIntensity,
  }
}

function advanceState(state: RelationshipStateRecord): RelationshipStateRecord {
  return {
    ...structuredClone(state),
    trustLevel: state.trustLevel < 2 ? state.trustLevel + 1 : state.trustLevel,
    conflictIntensity: state.trustLevel < 2
      ? state.conflictIntensity
      : clamp(state.conflictIntensity - 1, 0, 4),
  }
}

function groupRelationshipBeats(beats: RelationshipBeatRecord[]) {
  const grouped = new Map<number, RelationshipBeatRecord[]>()
  beats
    .slice()
    .sort((left, right) => (
      left.episodeOrdinal - right.episodeOrdinal
      || left.sequence - right.sequence
      || left.ordinal - right.ordinal
    ))
    .forEach((beat) => {
      const group = grouped.get(beat.episodeOrdinal) ?? []
      group.push(beat)
      grouped.set(beat.episodeOrdinal, group)
    })
  return Array.from(grouped, ([episodeOrdinal, episodeBeats]) => ({
    episodeOrdinal,
    beats: episodeBeats,
  }))
}

const RELATIONSHIP_METRICS = [
  { key: 'trustLevel', label: '信任', range: '-2 至 2' },
  { key: 'emotionalTemperature', label: '情感温度', range: '-2 至 2' },
  { key: 'powerBalance', label: '权力平衡', range: '-2 至 2' },
  { key: 'conflictIntensity', label: '冲突强度', range: '0 至 4' },
] as const

function conflictIntensityLabel(value: number): string {
  return ['无', '轻微', '中等', '强烈', '激烈'][clamp(Math.round(value), 0, 4)]
}

function formatMetricDelta(value: number): string {
  return value > 0 ? `+${value}` : String(value)
}

function metricChangeDescription(label: string, delta: number): string {
  return `${label}${delta > 0 ? '增加' : '减少'} ${Math.abs(delta)}`
}

function RelationshipMetricChanges({
  beforeState,
  afterState,
}: {
  beforeState: RelationshipStateRecord
  afterState: RelationshipStateRecord
}) {
  const changes = RELATIONSHIP_METRICS
    .map((metric) => ({
      ...metric,
      delta: afterState[metric.key] - beforeState[metric.key],
    }))
    .filter((metric) => metric.delta !== 0)

  return (
    <div
      aria-label={changes.length
        ? `本次关系变化：${changes.map((metric) => metricChangeDescription(metric.label, metric.delta)).join('，')}`
        : '本次关系变化：量化指标未变化'}
      className="relationship-metric-changes"
    >
      <span>本次关系变化</span>
      <div>
        {changes.length ? changes.map((metric) => (
          <span data-direction={metric.delta > 0 ? 'increase' : 'decrease'} key={metric.key}>
            {metric.label} <strong>{formatMetricDelta(metric.delta)}</strong>
          </span>
        )) : <small>量化指标未变化</small>}
      </div>
    </div>
  )
}

function RelationshipTimelineState({ state }: { state: RelationshipStateRecord }) {
  const intensityLabel = conflictIntensityLabel(state.conflictIntensity)
  return (
    <details className="relationship-timeline-state">
      <summary aria-label={`冲突强度：${intensityLabel}，${state.conflictIntensity}/4。展开查看精确指标`}>
        <span>冲突强度</span>
        <strong>{intensityLabel}</strong>
        <small>{state.conflictIntensity}/4</small>
        <ChevronDown aria-hidden="true" size={13} />
      </summary>
      <div className="relationship-timeline-state__details">
        <dl aria-label="精确关系状态指标">
          {RELATIONSHIP_METRICS.map((metric) => (
            <div key={metric.key}>
              <dt>{metric.label}</dt>
              <dd>{state[metric.key]}</dd>
              <small>{metric.range}</small>
            </div>
          ))}
        </dl>
        <p>信任、温度与权力使用 −2 至 2；冲突强度使用 0 至 4。</p>
      </div>
    </details>
  )
}

function relationshipBeatIdentity(beat: RelationshipBeatRecord): string {
  return `${beat.relationshipKey}:${beat.ordinal}`
}

function validationTone(severity: RelationshipGraphValidationIssue['severity']): string {
  return severity === 'BLOCKER' ? '阻断' : severity === 'WARNING' ? '提醒' : '信息'
}

type GraphPoint = { x: number; y: number }
type GraphViewport = GraphPoint & { scale: number }
type GraphDrag =
  | { type: 'node'; key: string; pointerId: number; offset: GraphPoint }
  | { type: 'canvas'; pointerId: number; start: GraphPoint; viewport: GraphViewport }

function initialGraphPositions(characterKeys: string[]): Map<string, GraphPoint> {
  return new Map(characterKeys.map((key, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(characterKeys.length, 1) - Math.PI / 2
    return [key, { x: 400 + Math.cos(angle) * 275, y: 210 + Math.sin(angle) * 145 }] as const
  }))
}

function RelationshipGraphCanvas({
  graph,
  characters,
  selectedRelationshipKey,
  selectedCharacterKey,
  layoutKey,
  layer,
  onSelectRelationship,
  onSelectCharacter,
}: {
  graph: RelationshipGraphPayloadRecord
  characters: RelationshipCharacter[]
  selectedRelationshipKey: string | null
  selectedCharacterKey: string | null
  layoutKey: string
  layer: 'surface' | 'truth'
  onSelectRelationship: (relationshipKey: string) => void
  onSelectCharacter: (characterKey: string) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const dragRef = useRef<GraphDrag | null>(null)
  const characterKeys = useMemo(() => {
    const keys = new Set<string>()
    graph.edges.forEach((edge) => {
      keys.add(edge.sourceCharacterKey)
      keys.add(edge.targetCharacterKey)
    })
    return [...keys]
  }, [graph.edges])
  const [positions, setPositions] = useState(() => initialGraphPositions(characterKeys))
  const [viewport, setViewport] = useState<GraphViewport>({ x: 0, y: 0, scale: 1 })

  useEffect(() => {
    setPositions((current) => {
      const defaults = initialGraphPositions(characterKeys)
      return new Map(characterKeys.map((key) => [key, current.get(key) ?? defaults.get(key) ?? { x: 400, y: 210 }]))
    })
  }, [characterKeys])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(`relationship-graph-layout:${layoutKey}`)
      if (!raw) return
      const saved = JSON.parse(raw) as { positions?: Record<string, GraphPoint>; viewport?: GraphViewport }
      if (saved.positions) {
        setPositions((current) => new Map(characterKeys.map((key) => [key, saved.positions?.[key] ?? current.get(key) ?? { x: 400, y: 210 }])))
      }
      if (saved.viewport && Number.isFinite(saved.viewport.scale)) {
        setViewport({ x: saved.viewport.x, y: saved.viewport.y, scale: clamp(saved.viewport.scale, 0.65, 1.8) })
      }
    } catch {
      window.localStorage.removeItem(`relationship-graph-layout:${layoutKey}`)
    }
  }, [layoutKey, characterKeys])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      window.localStorage.setItem(`relationship-graph-layout:${layoutKey}`, JSON.stringify({ positions: Object.fromEntries(positions), viewport }))
    }, 120)
    return () => window.clearTimeout(timer)
  }, [layoutKey, positions, viewport])

  function svgPoint(clientX: number, clientY: number): GraphPoint {
    const svg = svgRef.current
    const matrix = svg?.getScreenCTM()
    if (!svg || !matrix) return { x: 0, y: 0 }
    const point = svg.createSVGPoint()
    point.x = clientX
    point.y = clientY
    return point.matrixTransform(matrix.inverse())
  }

  function graphPoint(clientX: number, clientY: number): GraphPoint {
    const point = svgPoint(clientX, clientY)
    return { x: (point.x - viewport.x) / viewport.scale, y: (point.y - viewport.y) / viewport.scale }
  }

  function startNodeDrag(event: ReactPointerEvent<SVGGElement>, key: string) {
    event.preventDefault()
    event.stopPropagation()
    const position = positions.get(key)
    if (!position) return
    const point = graphPoint(event.clientX, event.clientY)
    dragRef.current = { type: 'node', key, pointerId: event.pointerId, offset: { x: point.x - position.x, y: point.y - position.y } }
    event.currentTarget.setPointerCapture(event.pointerId)
    onSelectCharacter(key)
  }

  function startCanvasDrag(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.target !== event.currentTarget) return
    const start = svgPoint(event.clientX, event.clientY)
    dragRef.current = { type: 'canvas', pointerId: event.pointerId, start, viewport }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function moveDrag(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (drag.type === 'node') {
      const point = graphPoint(event.clientX, event.clientY)
      setPositions((current) => new Map(current).set(drag.key, {
        x: clamp(point.x - drag.offset.x, 60, 740),
        y: clamp(point.y - drag.offset.y, 60, 360),
      }))
      return
    }
    const point = svgPoint(event.clientX, event.clientY)
    setViewport({ ...drag.viewport, x: drag.viewport.x + point.x - drag.start.x, y: drag.viewport.y + point.y - drag.start.y })
  }

  function endDrag(event: ReactPointerEvent<SVGSVGElement>) {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
  }

  function zoomAt(point: GraphPoint, nextScale: number) {
    setViewport((current) => {
      const scale = clamp(nextScale, 0.65, 1.8)
      const graphX = (point.x - current.x) / current.scale
      const graphY = (point.y - current.y) / current.scale
      return { scale, x: point.x - graphX * scale, y: point.y - graphY * scale }
    })
  }

  function handleWheel(event: ReactWheelEvent<SVGSVGElement>) {
    event.preventDefault()
    const point = svgPoint(event.clientX, event.clientY)
    zoomAt(point, viewport.scale * (event.deltaY > 0 ? 0.9 : 1.1))
  }

  function resetGraphView() {
    setPositions(initialGraphPositions(characterKeys))
    setViewport({ x: 0, y: 0, scale: 1 })
  }

  return (
    <div className="relationship-canvas" aria-label="角色关系图">
      <div className="relationship-canvas__controls" aria-label="图谱视图控制">
        <button aria-label="放大图谱" onClick={() => zoomAt({ x: 400, y: 210 }, viewport.scale * 1.15)} type="button"><Plus size={15} /></button>
        <button aria-label="缩小图谱" onClick={() => zoomAt({ x: 400, y: 210 }, viewport.scale * 0.85)} type="button"><Minus size={15} /></button>
        <button aria-label="重置图谱布局" onClick={resetGraphView} type="button"><Maximize2 size={15} /></button>
      </div>
      <svg aria-label={layer === 'surface' ? '可交互的角色明面关系图' : '可交互的角色真实关系图'} onPointerCancel={endDrag} onPointerDown={startCanvasDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onWheel={handleWheel} ref={svgRef} role="group" viewBox="0 0 800 420">
        <title>{layer === 'surface' ? '角色明面关系图' : '角色真实关系图'}</title>
        <defs>
          <marker id="relationship-arrow" markerHeight="7" markerWidth="7" orient="auto-start-reverse" refX="6" refY="3.5">
            <path d="M0,0 L7,3.5 L0,7 Z" />
          </marker>
        </defs>
        <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.scale})`}>
        {graph.edges.map((edge) => {
          const source = positions.get(edge.sourceCharacterKey)
          const target = positions.get(edge.targetCharacterKey)
          if (!source || !target) return null
          const selected = edge.relationshipKey === selectedRelationshipKey
          const midpointX = (source.x + target.x) / 2
          const midpointY = (source.y + target.y) / 2
          const labelLines = wrapRelationshipLabel(edgeLabel(edge, layer))
          const labelHeight = 16 + labelLines.length * 18
          const labelTop = midpointY - labelHeight / 2
          return <g
            aria-label={`${characterName(characters, edge.sourceCharacterKey)}与${characterName(characters, edge.targetCharacterKey)}：${edgeLabel(edge, layer)}${edge.locked ? '，已锁定' : ''}`}
            className={`relationship-canvas__edge ${selected ? 'is-selected' : ''}`}
            key={edge.relationshipKey}
            onClick={() => onSelectRelationship(edge.relationshipKey)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onSelectRelationship(edge.relationshipKey)
            }}
            role="button"
            tabIndex={0}
          >
            <line
              markerEnd={edge.directionality === 'DIRECTED' ? 'url(#relationship-arrow)' : undefined}
              strokeDasharray={edge.surfaceRelationship !== edge.trueRelationship ? '9 6' : undefined}
              strokeWidth={2 + edge.conflictIntensity * 0.65}
              x1={source.x}
              x2={target.x}
              y1={source.y}
              y2={target.y}
            />
            <rect height={labelHeight} rx="8" width="230" x={midpointX - 115} y={labelTop} />
            <text textAnchor="middle" x={midpointX} y={labelTop + 20}>
              {labelLines.map((line, index) => <tspan dy={index === 0 ? 0 : 18} key={`${edge.relationshipKey}-${index}`} x={midpointX}>{line}</tspan>)}
            </text>
            {edge.locked ? <text className="relationship-canvas__lock" textAnchor="middle" x={midpointX} y={labelTop - 7}>已锁定</text> : null}
          </g>
        })}
        {characterKeys.map((key) => {
          const point = positions.get(key)
          const character = characters.find((item) => item.key === key)
          if (!point) return null
          const selected = key === selectedCharacterKey
          return <g aria-label={`查看角色：${character?.name ?? key}`} className={`relationship-canvas__node ${selected ? 'is-selected' : ''}`} key={key} onClick={() => onSelectCharacter(key)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelectCharacter(key) }} onPointerDown={(event) => startNodeDrag(event, key)} role="button" tabIndex={0} transform={`translate(${point.x}, ${point.y})`}>
            <circle r="48" />
            <text textAnchor="middle" y="-2">{character?.name ?? key}</text>
            <text className="relationship-canvas__role" textAnchor="middle" y="17">{character?.role ?? '角色'}</text>
          </g>
        })}
        </g>
      </svg>
      <div className="relationship-canvas__legend" aria-label="图例">
        <span><i className="line-solid" />关系已公开或双方知情</span>
        <span><i className="line-dashed" />存在隐藏、误判或单方认知</span>
        <span>线越粗，冲突越强</span>
      </div>
    </div>
  )
}

function RangeField({
  label,
  value,
  minimum,
  maximum,
  disabled,
  onChange,
}: {
  label: string
  value: number
  minimum: number
  maximum: number
  disabled: boolean
  onChange: (value: number) => void
}) {
  return <label className="relationship-range"><span>{label}<strong>{value}</strong></span><input disabled={disabled} max={maximum} min={minimum} onChange={(event) => onChange(Number(event.target.value))} type="range" value={value} /></label>
}

export function RelationshipGraphSection({
  versions,
  characters,
  onGraphChanged,
  onCharacterVisualsReady,
  focusTarget = null,
}: RelationshipGraphSectionProps) {
  const [selectedGraphId, setSelectedGraphId] = useState(versions[0]?.id ?? '')
  const selectedGraph = versions.find((item) => item.id === selectedGraphId) ?? versions[0]
  const [draftState, setDraftState] = useState(() => createRelationshipDraftState(selectedGraph))
  const [selectedRelationshipKey, setSelectedRelationshipKey] = useState<string | null>(selectedGraph.graph.edges[0]?.relationshipKey ?? null)
  const [selectedCharacterKey, setSelectedCharacterKey] = useState<string | null>(null)
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const [layer, setLayer] = useState<'surface' | 'truth'>('surface')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [validationIssues, setValidationIssues] = useState(selectedGraph.validationIssues)
  const [dialog, setDialog] = useState<'diff' | 'revision' | null>(null)
  const [diff, setDiff] = useState<RelationshipGraphDiff | null>(null)
  const [revisionImpact, setRevisionImpact] = useState<RelationshipRevisionImpact | null>(null)
  const [revisionIntent, setRevisionIntent] = useState('调整这段关系，并同步修订受影响的剧情与场景。')
  const [revisionKeys, setRevisionKeys] = useState<string[]>(
    selectedGraph.graph.edges.map((edge) => edge.relationshipKey),
  )
  const [upbringingSuggestion, setUpbringingSuggestion] = useState<RelationshipUpbringingSuggestion | null>(null)
  const [upbringingSuggestionBusy, setUpbringingSuggestionBusy] = useState(false)
  const [upbringingSuggestionError, setUpbringingSuggestionError] = useState<string | null>(null)
  const [newBeatEpisodeOrdinal, setNewBeatEpisodeOrdinal] = useState(1)
  const [pendingDeleteBeatOrdinal, setPendingDeleteBeatOrdinal] = useState<number | null>(null)
  const upbringingSuggestionRequestRef = useRef(0)

  useEffect(() => {
    setDraftState((current) => syncRelationshipDraftState(current, selectedGraph))
    setValidationIssues(selectedGraph.validationIssues)
  }, [selectedGraph])

  useEffect(() => {
    if (!draftState.dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [draftState.dirty])

  useEffect(() => {
    if (!focusTarget) return
    const graph = versions.find((item) => item.id === focusTarget.graphId)
    if (!graph || (draftState.dirty && graph.id !== selectedGraph.id)) return
    setSelectedGraphId(graph.id)
    setSelectedRelationshipKey(focusTarget.relationshipKey)
    setSelectedCharacterKey(null)
    setDraftState((current) => syncRelationshipDraftState(current, graph))
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        document.getElementById(
          `relationship-beat-${graph.id}-${focusTarget.beatOrdinal}`,
        )?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
      })
    })
  }, [focusTarget?.requestId])

  const selectedCharacter = characters.find((character) => character.key === selectedCharacterKey) ?? null
  const selectedEdge = selectedCharacter
    ? null
    : draftState.localDraft.edges.find((edge) => edge.relationshipKey === selectedRelationshipKey)
      ?? draftState.localDraft.edges[0]
      ?? null
  const connectedEdges = selectedCharacter
    ? draftState.localDraft.edges.filter((edge) => edge.sourceCharacterKey === selectedCharacter.key || edge.targetCharacterKey === selectedCharacter.key)
    : []
  const selectedRelationshipBeats = selectedEdge
    ? draftState.localDraft.beats.filter((beat) => beat.relationshipKey === selectedEdge.relationshipKey)
    : []
  const persistedBeatIdentities = useMemo(
    () => new Set(draftState.serverSnapshot.beats.map(relationshipBeatIdentity)),
    [draftState.serverSnapshot.beats],
  )
  const relationshipBeatGroups = groupRelationshipBeats(selectedRelationshipBeats)
  const finalRelationshipState = relationshipBeatGroups.at(-1)?.beats.at(-1)?.afterState
    ?? (selectedEdge ? edgeState(selectedEdge) : null)
  const canEditSelected = Boolean(selectedEdge && selectedGraph.editability.semanticEditable && !selectedEdge.locked && !busy)
  const upbringingFieldId = selectedEdge
    ? `relationship-upbringing-${selectedGraph.id}-${selectedEdge.relationshipKey}`
    : 'relationship-upbringing'

  useEffect(() => {
    upbringingSuggestionRequestRef.current += 1
    setUpbringingSuggestion(null)
    setUpbringingSuggestionBusy(false)
    setUpbringingSuggestionError(null)
    const latestEpisode = selectedRelationshipBeats.reduce(
      (latest, beat) => Math.max(latest, beat.episodeOrdinal),
      1,
    )
    setNewBeatEpisodeOrdinal(latestEpisode)
    setPendingDeleteBeatOrdinal(null)
  }, [selectedGraph.id, selectedEdge?.relationshipKey])

  function editDraft(updater: (graph: RelationshipGraphPayloadRecord) => RelationshipGraphPayloadRecord) {
    setDraftState((current) => updateRelationshipDraft(current, updater))
    setMessage(null)
    setError(null)
  }

  function updateEdge(updater: (edge: RelationshipEdgeRecord) => void) {
    if (!selectedEdge || !canEditSelected) return
    editDraft((graph) => {
      const edge = graph.edges.find((item) => item.relationshipKey === selectedEdge.relationshipKey)
      if (edge) updater(edge)
      graph.coreRelationshipKeys = graph.edges.filter((item) => item.isCore).map((item) => item.relationshipKey)
      return graph
    })
  }

  function updateBeat(ordinal: number, updater: (beat: RelationshipBeatRecord) => void) {
    if (!canEditSelected) return
    editDraft((graph) => {
      const beat = graph.beats.find((item) => item.relationshipKey === selectedEdge?.relationshipKey && item.ordinal === ordinal)
      if (beat) updater(beat)
      return graph
    })
  }

  function clearUpbringingSuggestion() {
    upbringingSuggestionRequestRef.current += 1
    setUpbringingSuggestion(null)
    setUpbringingSuggestionBusy(false)
    setUpbringingSuggestionError(null)
  }

  async function generateUpbringingSuggestion() {
    if (
      !selectedEdge
      || !selectedEdge.familyKinship
      || !canEditSelected
      || upbringingSuggestionBusy
    ) return
    const requestId = upbringingSuggestionRequestRef.current + 1
    upbringingSuggestionRequestRef.current = requestId
    setUpbringingSuggestionBusy(true)
    setUpbringingSuggestionError(null)
    try {
      const suggestion = await generateRelationshipUpbringingSuggestion(
        selectedGraph.id,
        selectedEdge.relationshipKey,
        {
          familyKinship: selectedEdge.familyKinship,
          surfaceRelationship: selectedEdge.surfaceRelationship,
          trueRelationship: selectedEdge.trueRelationship,
        },
      )
      if (upbringingSuggestionRequestRef.current !== requestId) return
      setUpbringingSuggestion(suggestion)
    } catch (reason) {
      if (upbringingSuggestionRequestRef.current !== requestId) return
      setUpbringingSuggestionError(
        reason instanceof Error ? reason.message : '成长经历说明生成失败，请稍后重试。',
      )
    } finally {
      if (upbringingSuggestionRequestRef.current === requestId) {
        setUpbringingSuggestionBusy(false)
      }
    }
  }

  function applyRemoteGraph(graph: RelationshipGraphVersionRecord, notice: string) {
    setSelectedGraphId(graph.id)
    setDraftState(createRelationshipDraftState(graph))
    setSelectedCharacterKey(null)
    setValidationIssues(graph.validationIssues)
    setMessage(notice)
    setError(null)
    onGraphChanged(graph)
  }

  function comparisonBase(): RelationshipGraphVersionRecord | undefined {
    return versions.find((item) => item.id === selectedGraph.parentVersionId)
      ?? versions.filter((item) => item.version < selectedGraph.version).sort((a, b) => b.version - a.version)[0]
  }

  async function openDiff() {
    const base = comparisonBase()
    if (!base) return
    const result = await fetchRelationshipGraphDiff(base.id, selectedGraph.id)
    setDiff(result)
    setDialog('diff')
  }

  function openRevision() {
    setRevisionKeys(selectedEdge ? [selectedEdge.relationshipKey] : selectedGraph.graph.edges.map((edge) => edge.relationshipKey))
    setRevisionIntent('调整这段关系，并同步修订受影响的剧情与场景。')
    setRevisionImpact(null)
    setDialog('revision')
  }

  async function analyzeRevision() {
    const impact = await analyzeRelationshipRevisionImpact(selectedGraph, revisionKeys, revisionIntent)
    setRevisionImpact(impact)
  }

  async function confirmRevision() {
    if (!revisionImpact) return
    const graph = await createRelationshipGraphRevision(revisionImpact)
    setDialog(null)
    applyRemoteGraph(graph, '关系修改版已创建。当前剧本已标记为过期，请完成修改并重新确认关系。')
  }

  async function run(action: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      await action()
    } catch (reason) {
      if (reason instanceof ApiError && ['VERSION_CONFLICT', 'RELATIONSHIP_VERSION_CONFLICT'].includes(reason.code)) {
        setDraftState((current) => ({ ...current, saveStatus: 'conflict' }))
        setError('服务端版本已经变化。本地修改仍然保留，请先刷新比较后再决定。')
      } else {
        setDraftState((current) => current.saveStatus === 'saving' ? { ...current, saveStatus: 'failed' } : current)
        setError(reason instanceof Error ? reason.message : '关系网操作失败')
      }
    } finally {
      setBusy(false)
    }
  }

  async function saveDraft() {
    setDraftState((current) => ({ ...current, saveStatus: 'saving' }))
    const graph = await saveRelationshipGraph(
      selectedGraph.id,
      selectedGraph.projectLockVersion,
      selectedGraph.lockVersion,
      draftState.localDraft,
    )
    applyRemoteGraph(graph, '关系草稿已保存。')
  }

  function switchVersion(graphId: string) {
    if (graphId === selectedGraph.id) return
    if (draftState.dirty && !window.confirm('当前版本有未保存修改。切换版本会放弃这些修改，是否继续？')) return
    const next = versions.find((item) => item.id === graphId)
    if (!next) return
    setSelectedGraphId(graphId)
    setDraftState(createRelationshipDraftState(next))
    setSelectedRelationshipKey(next.graph.edges[0]?.relationshipKey ?? null)
    setSelectedCharacterKey(null)
    setMessage(null)
    setError(null)
  }

  function selectIssue(issue: RelationshipGraphValidationIssue) {
    const relationshipKey = issue.relationshipKey
      ?? draftState.localDraft.edges.find((edge) => edge.sourceCharacterKey === issue.characterKey || edge.targetCharacterKey === issue.characterKey)?.relationshipKey
    if (relationshipKey) {
      setSelectedRelationshipKey(relationshipKey)
      setSelectedCharacterKey(null)
    } else if (issue.characterKey) {
      setSelectedCharacterKey(issue.characterKey)
      setSelectedRelationshipKey(null)
    }
  }

  function isLocallyAddedBeat(beat: RelationshipBeatRecord): boolean {
    return !persistedBeatIdentities.has(relationshipBeatIdentity(beat))
  }

  function deleteLocallyAddedBeat(beat: RelationshipBeatRecord) {
    if (!canEditSelected || !isLocallyAddedBeat(beat)) return
    setDraftState((current) => removeRelationshipBeatFromDraft(
      current,
      beat.relationshipKey,
      beat.ordinal,
    ))
    setPendingDeleteBeatOrdinal(null)
    setError(null)
    setMessage(`已从未保存草稿中删除第 ${beat.episodeOrdinal} 集变化 ${beat.sequence}。`)
  }

  function addBeat() {
    if (!selectedEdge || !canEditSelected) return
    editDraft((graph) => {
      const existing = graph.beats
        .filter((beat) => (
          beat.relationshipKey === selectedEdge.relationshipKey
          && beat.episodeOrdinal === newBeatEpisodeOrdinal
        ))
        .sort((left, right) => left.sequence - right.sequence)
      const previousEpisodeBeat = graph.beats
        .filter((beat) => (
          beat.relationshipKey === selectedEdge.relationshipKey
          && beat.episodeOrdinal < newBeatEpisodeOrdinal
        ))
        .sort((left, right) => (
          right.episodeOrdinal - left.episodeOrdinal
          || right.sequence - left.sequence
        ))[0]
      const beforeState = existing.at(-1)?.afterState
        ?? previousEpisodeBeat?.afterState
        ?? edgeState(selectedEdge)
      graph.beats.push({
        relationshipKey: selectedEdge.relationshipKey,
        episodeOrdinal: newBeatEpisodeOrdinal,
        sequence: existing.length + 1,
        sceneOrdinal: null,
        triggerType: 'STORY_EVENT',
        triggerRef: null,
        beforeState: structuredClone(beforeState),
        afterState: advanceState(beforeState),
        evidence: '待补充触发证据',
        emotionalConsequence: '待补充情绪后果',
        audienceVisibility: 'PARTIAL',
        ordinal: Math.max(0, ...graph.beats.map((beat) => beat.ordinal)) + 1,
      })
      return graph
    })
  }

  return (
    <section className="story-section relationship-editor" aria-labelledby="relationship-editor-title" id="relationship-review">
      <div className="relationship-editor__heading">
        <div><p className="eyebrow">关系基线确认</p><h2 id="relationship-editor-title">角色关系设计</h2><p>先确认人物之间的明面关系、真实关系与变化节拍，再让系统据此生成分集大纲和剧本。</p></div>
        <div className="relationship-editor__version"><label>关系版本<SelectControl aria-label="选择关系版本" onChange={(event) => switchVersion(event.target.value)} value={selectedGraph.id}>{versions.map((version) => <option key={version.id} value={version.id}>第 {version.version} 版 · {version.status === 'DRAFT' ? '草稿' : version.status === 'READY_FOR_REVIEW' ? '待审核' : version.status === 'APPROVED' ? '已批准' : '历史版本'}</option>)}</SelectControl></label><StatusBadge status={selectedGraph.status} />{comparisonBase() ? <Button disabled={busy || draftState.dirty} onClick={() => void run(openDiff)} size="sm" variant="secondary"><GitCompare size={15} />比较版本</Button> : null}</div>
      </div>

      {!selectedGraph.editability.semanticEditable ? <div className="relationship-editor__readonly"><LockKeyhole size={17} /><div><strong>当前为只读状态</strong><p>{selectedGraph.editability.reasonMessage ?? '当前项目阶段不开放关系语义编辑。'}</p></div></div> : null}
      {draftState.remoteUpdateAvailable ? <div className="relationship-editor__remote"><RefreshCw size={17} /><div><strong>服务端已有更新，本地修改未被覆盖</strong><p>继续保存会由版本锁阻止误覆盖。你可以保留本地内容，或放弃本地修改并载入最新版本。</p></div><Button onClick={() => { if (window.confirm('确认放弃本地未保存修改并载入服务端版本？')) setDraftState(createRelationshipDraftState(selectedGraph)) }} size="sm" variant="secondary">载入服务端版本</Button></div> : null}
      {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
      {message ? <div className="brief-save-message brief-save-message--success" role="status">{message}</div> : null}

      <div className="relationship-editor__toolbar">
        <div className="relationship-editor__segmented" aria-label="关系层次"><button aria-pressed={layer === 'surface'} onClick={() => setLayer('surface')} type="button">明面关系</button><button aria-pressed={layer === 'truth'} onClick={() => setLayer('truth')} type="button">真实关系</button></div>
        <div className="relationship-editor__segmented" aria-label="视图方式"><button aria-pressed={view === 'graph'} onClick={() => setView('graph')} type="button"><Workflow size={14} />图形视图</button><button aria-pressed={view === 'list'} onClick={() => setView('list')} type="button"><List size={14} />列表视图</button></div>
        <div className={`relationship-editor__save-status is-${draftState.saveStatus}`} aria-live="polite">{SAVE_LABELS[draftState.saveStatus]}</div>
        <Button disabled={busy || !draftState.dirty || !selectedGraph.editability.semanticEditable} onClick={() => void run(saveDraft)} size="sm" variant="secondary"><Save size={15} />保存草稿</Button>
        <Button disabled={busy || draftState.dirty} onClick={() => void run(async () => { const result = await fetchRelationshipGraphValidation(selectedGraph.id); setValidationIssues(result.issues); setMessage(result.validForApproval ? '检查完成：关系网可以批准。' : '检查完成：请先处理阻断问题。') })} size="sm" variant="secondary"><ShieldCheck size={15} />检查关系网</Button>
      </div>

      <div className="relationship-editor__workspace">
        <div className="relationship-editor__main">
          {view === 'graph' ? <RelationshipGraphCanvas characters={characters} graph={draftState.localDraft} layer={layer} layoutKey={selectedGraph.id} onSelectCharacter={(key) => { setSelectedCharacterKey(key); setSelectedRelationshipKey(null) }} onSelectRelationship={(key) => { setSelectedRelationshipKey(key); setSelectedCharacterKey(null) }} selectedCharacterKey={selectedCharacter?.key ?? null} selectedRelationshipKey={selectedEdge?.relationshipKey ?? null} /> : <div className="relationship-list" role="list">{draftState.localDraft.edges.map((edge) => <button aria-current={edge.relationshipKey === selectedEdge?.relationshipKey} className={edge.relationshipKey === selectedEdge?.relationshipKey ? 'is-selected' : ''} key={edge.relationshipKey} onClick={() => { setSelectedRelationshipKey(edge.relationshipKey); setSelectedCharacterKey(null) }} role="listitem" type="button"><div><strong>{characterName(characters, edge.sourceCharacterKey)} {edge.directionality === 'DIRECTED' ? '→' : '↔'} {characterName(characters, edge.targetCharacterKey)}</strong><span>{edge.relationshipTypes.map((type) => RELATIONSHIP_TYPE_LABELS[type]).join(' · ')}{edge.familyKinship ? ` · ${FAMILY_RELATION_LABELS[edge.familyKinship.relationType]}` : ''}</span></div><p>{edgeLabel(edge, layer)}</p><small>冲突 {edge.conflictIntensity}/4 · {edge.isCore ? '核心关系' : '一般关系'} · {edge.locked ? '已锁定' : '未锁定'}</small></button>)}</div>}
        </div>

        <aside className="relationship-inspector" aria-label={selectedCharacter ? '角色信息' : '关系属性'}>
          {selectedCharacter ? <>
            <header><div><span>角色信息</span><h3>{selectedCharacter.name}</h3></div></header>
            <dl className="relationship-character-inspector">
              <div><dt>角色定位</dt><dd>{selectedCharacter.role}</dd></div>
              <div><dt>年龄</dt><dd>{selectedCharacter.age}</dd></div>
              <div><dt>职业</dt><dd>{selectedCharacter.occupation}</dd></div>
              <div><dt>性格</dt><dd>{selectedCharacter.personality}</dd></div>
              <div><dt>剧情功能</dt><dd>{selectedCharacter.dramaticFunction}</dd></div>
              <div><dt>欲望</dt><dd>{selectedCharacter.desire}</dd></div>
              <div><dt>恐惧</dt><dd>{selectedCharacter.fear}</dd></div>
              <div><dt>秘密</dt><dd>{selectedCharacter.secret}</dd></div>
            </dl>
            <section className="relationship-character-connections"><h4>相关关系 · {connectedEdges.length}</h4>{connectedEdges.map((edge) => <button key={edge.relationshipKey} onClick={() => { setSelectedRelationshipKey(edge.relationshipKey); setSelectedCharacterKey(null) }} type="button"><strong>{characterName(characters, edge.sourceCharacterKey)} {edge.directionality === 'DIRECTED' ? '→' : '↔'} {characterName(characters, edge.targetCharacterKey)}</strong><span>{edgeLabel(edge, layer)}</span></button>)}</section>
          </> : selectedEdge ? <>
            <header><div><span>关系属性</span><h3>{characterName(characters, selectedEdge.sourceCharacterKey)} {selectedEdge.directionality === 'DIRECTED' ? '→' : '↔'} {characterName(characters, selectedEdge.targetCharacterKey)}</h3></div>{selectedEdge.locked ? <span className="relationship-inspector__lock"><LockKeyhole size={13} />已锁定</span> : null}</header>
            <label>关系方向<SelectControl aria-label="关系方向" disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.directionality = event.target.value as RelationshipEdgeRecord['directionality'] })} value={selectedEdge.directionality}><option value="BIDIRECTIONAL">双向关系</option><option value="DIRECTED">由前者指向后者</option></SelectControl></label>
            <fieldset disabled={!canEditSelected}><legend>关系类型</legend><div className="relationship-type-options">{(Object.keys(RELATIONSHIP_TYPE_LABELS) as RelationshipType[]).map((type) => <label key={type}><input checked={selectedEdge.relationshipTypes.includes(type)} onChange={(event) => { if (type === 'FAMILY') clearUpbringingSuggestion(); updateEdge((edge) => { if (!event.target.checked && edge.relationshipTypes.length === 1) return; edge.relationshipTypes = event.target.checked ? [...edge.relationshipTypes, type] : edge.relationshipTypes.filter((item) => item !== type); if (type === 'FAMILY' && event.target.checked && !edge.familyKinship) edge.familyKinship = { relationType: 'UNSPECIFIED', sharedUpbringing: 'UNKNOWN' }; if (type === 'FAMILY' && !event.target.checked) delete edge.familyKinship }) }} type="checkbox" />{RELATIONSHIP_TYPE_LABELS[type]}</label>)}</div></fieldset>
            {selectedEdge.relationshipTypes.includes('FAMILY') ? <fieldset className="relationship-kinship-fields" disabled={!canEditSelected}><legend>血缘与成长环境</legend><label>亲属来源<SelectControl aria-label="亲属来源" onChange={(event) => { clearUpbringingSuggestion(); updateEdge((edge) => { edge.familyKinship = { ...(edge.familyKinship ?? { sharedUpbringing: 'UNKNOWN' }), relationType: event.target.value as FamilyRelationType } }) }} value={selectedEdge.familyKinship?.relationType ?? 'UNSPECIFIED'}>{(Object.keys(FAMILY_RELATION_LABELS) as FamilyRelationType[]).map((type) => <option key={type} value={type}>{FAMILY_RELATION_LABELS[type]}</option>)}</SelectControl></label><label>共同成长环境<SelectControl aria-label="共同成长环境" onChange={(event) => { clearUpbringingSuggestion(); updateEdge((edge) => { edge.familyKinship = { ...(edge.familyKinship ?? { relationType: 'UNSPECIFIED' }), sharedUpbringing: event.target.value as SharedUpbringing } }) }} value={selectedEdge.familyKinship?.sharedUpbringing ?? 'UNKNOWN'}>{(Object.keys(SHARED_UPBRINGING_LABELS) as SharedUpbringing[]).map((type) => <option key={type} value={type}>{SHARED_UPBRINGING_LABELS[type]}</option>)}</SelectControl></label><div className="relationship-ai-field"><div className="relationship-ai-field__heading"><label htmlFor={upbringingFieldId}>成长经历说明</label><Button aria-label="AI 生成成长经历说明" disabled={!canEditSelected || upbringingSuggestionBusy} onClick={() => void generateUpbringingSuggestion()} size="sm" variant="ghost">{upbringingSuggestionBusy ? <LoaderCircle className="spin" size={13} /> : <Sparkles size={13} />}{upbringingSuggestionBusy ? '生成中' : upbringingSuggestion ? '重新生成' : selectedEdge.familyKinship?.upbringingContext ? 'AI 改写' : 'AI 生成'}</Button></div><textarea id={upbringingFieldId} onChange={(event) => { clearUpbringingSuggestion(); updateEdge((edge) => { edge.familyKinship = { ...(edge.familyKinship ?? { relationType: 'UNSPECIFIED', sharedUpbringing: 'UNKNOWN' }), upbringingContext: event.target.value } }) }} placeholder="例如：从小共同生活，但因家庭冲突形成不同的情绪表达方式" rows={3} value={selectedEdge.familyKinship?.upbringingContext ?? ''} />{upbringingSuggestionError ? <small className="relationship-ai-field__error" role="alert">{upbringingSuggestionError}</small> : null}{upbringingSuggestion ? <div className="relationship-ai-suggestion" role="status"><div><span><Sparkles size={13} /><strong>生成建议</strong></span><Button onClick={() => { const suggestion = upbringingSuggestion.suggestion; updateEdge((edge) => { edge.familyKinship = { ...(edge.familyKinship ?? { relationType: 'UNSPECIFIED', sharedUpbringing: 'UNKNOWN' }), upbringingContext: suggestion } }); clearUpbringingSuggestion() }} size="sm" variant="secondary"><Check size={13} />采用这版</Button></div><p>{upbringingSuggestion.suggestion}</p>{upbringingSuggestion.warning ? <small>{upbringingSuggestion.warning}</small> : null}</div> : null}</div><small>只有明确的亲生血缘会生成容貌相似约束；养亲、继亲和姻亲不会自动添加。</small></fieldset> : null}
            <label>明面关系<textarea className="relationship-inspector__narrative" disabled={!canEditSelected} onChange={(event) => { clearUpbringingSuggestion(); updateEdge((edge) => { edge.surfaceRelationship = event.target.value }) }} rows={4} value={selectedEdge.surfaceRelationship} /></label>
            <label>真实关系<textarea className="relationship-inspector__narrative" disabled={!canEditSelected} onChange={(event) => { clearUpbringingSuggestion(); updateEdge((edge) => { edge.trueRelationship = event.target.value }) }} rows={4} value={selectedEdge.trueRelationship} /></label>
            <details><summary>双方如何理解这段关系</summary><label>{characterName(characters, selectedEdge.sourceCharacterKey)}眼中的关系<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.sourceView.perceivedRelationship = event.target.value })} rows={2} value={selectedEdge.sourceView.perceivedRelationship} /></label><label>{characterName(characters, selectedEdge.sourceCharacterKey)}的判断<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.sourceView.belief = event.target.value })} rows={2} value={selectedEdge.sourceView.belief} /></label><label>{characterName(characters, selectedEdge.targetCharacterKey)}眼中的关系<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.targetView.perceivedRelationship = event.target.value })} rows={2} value={selectedEdge.targetView.perceivedRelationship} /></label><label>{characterName(characters, selectedEdge.targetCharacterKey)}的判断<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.targetView.belief = event.target.value })} rows={2} value={selectedEdge.targetView.belief} /></label></details>
            <div className="relationship-inspector__ranges"><RangeField disabled={!canEditSelected} label="信任" maximum={2} minimum={-2} onChange={(value) => updateEdge((edge) => { edge.trustLevel = value })} value={selectedEdge.trustLevel} /><RangeField disabled={!canEditSelected} label="情感温度" maximum={2} minimum={-2} onChange={(value) => updateEdge((edge) => { edge.emotionalTemperature = value })} value={selectedEdge.emotionalTemperature} /><RangeField disabled={!canEditSelected} label="权力平衡" maximum={2} minimum={-2} onChange={(value) => updateEdge((edge) => { edge.powerBalance = value })} value={selectedEdge.powerBalance} /><RangeField disabled={!canEditSelected} label="冲突强度" maximum={4} minimum={0} onChange={(value) => updateEdge((edge) => { edge.conflictIntensity = value })} value={selectedEdge.conflictIntensity} /></div>
            <label>剧情功能<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.storyFunction = event.target.value })} rows={3} value={selectedEdge.storyFunction} /></label>
            <label>关系秘密<textarea disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.secret = event.target.value || null })} placeholder="没有秘密可留空" rows={2} value={selectedEdge.secret ?? ''} /></label>
            <label className="relationship-inspector__core"><input checked={selectedEdge.isCore} disabled={!canEditSelected} onChange={(event) => updateEdge((edge) => { edge.isCore = event.target.checked })} type="checkbox" />设为核心关系</label>
            <Button disabled={busy || draftState.dirty || !selectedGraph.editability.semanticEditable || (!selectedEdge.isCore && !selectedEdge.locked)} onClick={() => void run(async () => { if (selectedEdge.locked && !window.confirm('解除核心关系锁定后，后续修改可能让已生成剧本失效。确认继续？')) return; const graph = await setRelationshipGraphEdgeLock(selectedGraph, selectedEdge.relationshipKey, !selectedEdge.locked); applyRemoteGraph(graph, selectedEdge.locked ? '关系锁定已解除。' : '核心关系已锁定。') })} size="sm" variant="secondary">{selectedEdge.locked ? <UnlockKeyhole size={15} /> : <LockKeyhole size={15} />}{selectedEdge.locked ? '解除关系锁定' : '锁定核心关系'}</Button>
          </> : <p>请选择一个角色或一条关系查看详情。</p>}
        </aside>
      </div>

      <section className="relationship-timeline" aria-labelledby="relationship-timeline-title">
        <header className="relationship-timeline__heading">
          <div>
            <p className="eyebrow">按集编排 · 纵向演变轨道</p>
            <h3 id="relationship-timeline-title">关系变化时间线</h3>
            <p>从故事开场开始，按集查看触发事件如何一步步改变两人的关系。</p>
          </div>
          {selectedEdge ? <div className="relationship-timeline__toolbar">
            <div className="relationship-timeline__summary" aria-label="时间线概览">
              <span><strong>{relationshipBeatGroups.length}</strong> 个剧情阶段</span>
              <span><strong>{selectedRelationshipBeats.length}</strong> 次关系变化</span>
            </div>
            <div className="relationship-timeline__add">
              <label>
                <span>添加至</span>
                <span>第 <input aria-label="添加变化事件的目标集数" disabled={!canEditSelected} min={1} onChange={(event) => setNewBeatEpisodeOrdinal(Math.max(1, Number(event.target.value) || 1))} type="number" value={newBeatEpisodeOrdinal} /> 集</span>
              </label>
              <Button disabled={!canEditSelected} onClick={addBeat} size="sm" variant="secondary"><Plus size={14} />添加变化事件</Button>
            </div>
          </div> : null}
        </header>
        {selectedEdge && finalRelationshipState ? <div className="relationship-timeline__track">
          <article className="relationship-timeline__baseline">
            <div className="relationship-timeline__node"><Clock3 size={16} /></div>
            <div className="relationship-timeline__baseline-copy">
              <span>故事开场 · 关系基线</span>
              <strong>{selectedEdge.surfaceRelationship}</strong>
              <p>真实关系：{selectedEdge.trueRelationship}</p>
            </div>
            <RelationshipTimelineState state={selectedEdge} />
          </article>

          <div className="relationship-timeline__episodes">
            {relationshipBeatGroups.length ? relationshipBeatGroups.map((group) => {
              const openingState = group.beats[0].beforeState
              const endingState = group.beats.at(-1)?.afterState ?? openingState
              const episodeTitleId = `relationship-episode-${selectedGraph.id}-${group.episodeOrdinal}`
              return <section aria-labelledby={episodeTitleId} className="relationship-episode" key={group.episodeOrdinal}>
                <header className="relationship-episode__heading">
                  <span className="relationship-episode__marker"><small>EP</small><strong>{String(group.episodeOrdinal).padStart(2, '0')}</strong></span>
                  <span className="relationship-episode__title">
                    <strong id={episodeTitleId}>第 {group.episodeOrdinal} 集</strong>
                    <small>{group.beats.length} 次关系变化</small>
                  </span>
                  <span className="relationship-episode__journey">
                    <span><small>本集起点</small><strong>{openingState.surfaceRelationship}</strong></span>
                    <i aria-hidden="true">→</i>
                    <span><small>本集落点</small><strong>{endingState.surfaceRelationship}</strong></span>
                  </span>
                </header>

                <div className="relationship-episode__body">
                  <ol className="relationship-episode__events">
                    {group.beats.map((beat) => <li data-sequence={beat.sequence} key={`${beat.relationshipKey}-${beat.ordinal}`}>
                      <article className="relationship-beat" id={`relationship-beat-${selectedGraph.id}-${beat.ordinal}`}>
                        <header>
                          <div><span>变化 {beat.sequence}</span><strong>{TRIGGER_LABELS[beat.triggerType]}</strong>{isLocallyAddedBeat(beat) ? <small className="relationship-beat__local-badge">未保存新增</small> : null}</div>
                          <div className="relationship-beat__controls">
                            <label><span>触发方式</span><SelectControl aria-label={`第 ${beat.episodeOrdinal} 集变化 ${beat.sequence} 的触发类型`} disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.triggerType = event.target.value as RelationshipBeatRecord['triggerType']; if (!['MISJUDGMENT', 'AUTHENTICATION'].includes(item.triggerType)) item.triggerRef = null })} value={beat.triggerType}>{(Object.keys(TRIGGER_LABELS) as RelationshipBeatRecord['triggerType'][]).map((type) => <option key={type} value={type}>{TRIGGER_LABELS[type]}</option>)}</SelectControl></label>
                            <label><span>观众可见</span><SelectControl aria-label={`第 ${beat.episodeOrdinal} 集变化 ${beat.sequence} 的观众可见范围`} disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.audienceVisibility = event.target.value as RelationshipBeatRecord['audienceVisibility'] })} value={beat.audienceVisibility}>{(Object.keys(VISIBILITY_LABELS) as RelationshipBeatRecord['audienceVisibility'][]).map((visibility) => <option key={visibility} value={visibility}>{VISIBILITY_LABELS[visibility]}</option>)}</SelectControl></label>
                            {isLocallyAddedBeat(beat) ? pendingDeleteBeatOrdinal === beat.ordinal ? <div className="relationship-beat__delete-confirm" role="group" aria-label={`确认删除第 ${beat.episodeOrdinal} 集变化 ${beat.sequence}`}>
                              <span>删除？</span>
                              <Button aria-label={`取消删除第 ${beat.episodeOrdinal} 集变化 ${beat.sequence}`} onClick={() => setPendingDeleteBeatOrdinal(null)} size="sm" variant="ghost">取消</Button>
                              <Button aria-label={`确认删除第 ${beat.episodeOrdinal} 集变化 ${beat.sequence}`} onClick={() => deleteLocallyAddedBeat(beat)} size="sm" variant="danger"><Trash2 size={13} />确认</Button>
                            </div> : <Button aria-label={`删除第 ${beat.episodeOrdinal} 集变化 ${beat.sequence}`} className="relationship-beat__delete" disabled={!canEditSelected} onClick={() => setPendingDeleteBeatOrdinal(beat.ordinal)} size="sm" variant="ghost"><Trash2 size={13} />删除</Button> : null}
                          </div>
                        </header>
                        <div className="relationship-beat__before"><span>变化前</span><p>{beat.beforeState.surfaceRelationship}</p></div>
                        <div className="relationship-beat__form">
                          {['MISJUDGMENT', 'AUTHENTICATION'].includes(beat.triggerType) ? <label><span>关联序号</span><input disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.triggerRef = event.target.value })} placeholder="例如认证步骤 2" value={beat.triggerRef ?? ''} /></label> : null}
                          <label className="is-wide"><span>触发证据</span><textarea disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.evidence = event.target.value })} rows={2} value={beat.evidence} /></label>
                          <div className="relationship-beat__states">
                            <label><span>变化后明面关系</span><input disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.afterState.surfaceRelationship = event.target.value })} value={beat.afterState.surfaceRelationship} /></label>
                            <label><span>变化后真实关系</span><input disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.afterState.trueRelationship = event.target.value })} value={beat.afterState.trueRelationship} /></label>
                          </div>
                          <label className="is-wide"><span>情绪后果</span><textarea disabled={!canEditSelected} onChange={(event) => updateBeat(beat.ordinal, (item) => { item.emotionalConsequence = event.target.value })} rows={2} value={beat.emotionalConsequence} /></label>
                        </div>
                        <RelationshipMetricChanges beforeState={beat.beforeState} afterState={beat.afterState} />
                      </article>
                    </li>)}
                  </ol>
                  <footer className="relationship-episode__outcome">
                    <span className="relationship-episode__outcome-icon"><Check size={15} /></span>
                    <div><span>本集关系落点</span><strong>{endingState.surfaceRelationship}</strong><small>真实关系：{endingState.trueRelationship}</small></div>
                    <RelationshipTimelineState state={endingState} />
                  </footer>
                </div>
              </section>
            }) : <div className="relationship-timeline__empty">
              <strong>还没有设置关系变化</strong>
              <p>选择目标集数并添加第一个事件，系统会从开场关系自动承接变化前状态。</p>
            </div>}
          </div>

          <article className="relationship-timeline__final">
            <div className="relationship-timeline__node"><Check size={16} /></div>
            <div>
              <span>{relationshipBeatGroups.length ? '当前最终状态' : '关系基线即当前状态'}</span>
              <strong>{finalRelationshipState.surfaceRelationship}</strong>
              <small>{relationshipBeatGroups.length ? `承接至第 ${relationshipBeatGroups.at(-1)?.episodeOrdinal} 集结尾，并作为后续剧本的关系起点。` : '添加变化事件后，这里会同步显示最新的关系落点。'}</small>
            </div>
            <RelationshipTimelineState state={finalRelationshipState} />
          </article>
        </div> : <p className="relationship-timeline__unselected">请选择一条关系查看变化时间线。</p>}
      </section>

      <section className="relationship-validation" aria-labelledby="relationship-validation-title"><header><div><h3 id="relationship-validation-title">关系网检查</h3><p>阻断问题必须修正后才能确认关系并生成剧本。</p></div><span>{validationIssues.filter((issue) => issue.severity === 'BLOCKER').length} 个阻断 · {validationIssues.filter((issue) => issue.severity === 'WARNING').length} 个提醒</span></header>{validationIssues.length ? <ul>{validationIssues.map((issue) => <li data-severity={issue.severity.toLowerCase()} key={`${issue.code}-${issue.relationshipKey ?? issue.characterKey ?? ''}`}><button onClick={() => selectIssue(issue)} type="button"><strong>{validationTone(issue.severity)}</strong><span>{issue.message}</span><small title={issue.code}>{VALIDATION_CODE_LABELS[issue.code] ?? '关系规则'}</small></button></li>)}</ul> : <div className="relationship-validation__empty"><Check size={16} />当前检查未发现问题</div>}</section>

      <footer className="relationship-editor__actions"><div><strong>{selectedGraph.status === 'DRAFT' ? '草稿可直接确认，也可以先提交审核。' : selectedGraph.status === 'READY_FOR_REVIEW' ? '当前版本已提交审核，可批准或撤回。' : '当前版本仅供查看。'}</strong><p>批准后会自动准备结构化角色视觉档案；不会后台生图，也不会自动采用任何候选。</p></div><div>{selectedGraph.editability.canCreateRevision ? <Button disabled={busy || draftState.dirty} onClick={openRevision} variant="secondary"><GitCompare size={16} />创建修改版</Button> : null}{selectedGraph.status === 'DRAFT' && selectedGraph.editability.canSubmit ? <Button disabled={busy || draftState.dirty} onClick={() => void run(async () => { const graph = await submitRelationshipGraph(selectedGraph); applyRemoteGraph(graph, '关系网已提交审核。') })} variant="secondary">提交审核</Button> : null}{selectedGraph.status === 'READY_FOR_REVIEW' ? <Button disabled={busy || draftState.dirty} onClick={() => void run(async () => { const graph = await withdrawRelationshipGraph(selectedGraph); applyRemoteGraph(graph, '已撤回审核，可继续编辑。') })} variant="secondary">撤回审核</Button> : null}<Button disabled={busy || draftState.dirty || !selectedGraph.editability.canApprove || validationIssues.some((issue) => issue.severity === 'BLOCKER')} onClick={() => void run(async () => { if (!window.confirm('确认当前角色文字设定与关系基线，并进入角色形象步骤？')) return; const result = await approveRelationshipGraph(selectedGraph.id, selectedGraph.projectLockVersion, selectedGraph.lockVersion); applyRemoteGraph(result.graph, '关系基线已批准，角色视觉档案已准备。'); onCharacterVisualsReady(result.characterVisuals.route, result.characterVisuals.characterCount) })}><Check size={16} />确认关系并准备角色形象</Button></div></footer>

      <Modal className="modal--relationship-change" description={diff ? `第 ${diff.fromVersion} 版 → 第 ${diff.toVersion} 版` : undefined} footer={<Button onClick={() => setDialog(null)}>关闭</Button>} onClose={() => setDialog(null)} open={dialog === 'diff'} title="关系版本比较">
        {diff?.changes.length ? <div className="relationship-diff"><div className="relationship-diff__summary"><strong>最高影响等级 {diff.highestPriority}</strong><span>P0 {diff.counts.P0} · P1 {diff.counts.P1} · P2 {diff.counts.P2} · P3 {diff.counts.P3} · P4 {diff.counts.P4}</span></div><ul>{diff.changes.map((change, index) => <li key={`${change.category}-${change.relationshipKey}-${change.beatOrdinal ?? index}`}><span className={`relationship-impact-level is-${change.priority.toLowerCase()}`}>{change.priority}</span><div><strong>{change.category.startsWith('BEAT_') ? '关系变化事件' : change.relationshipKey}</strong><p>{change.summary}</p>{change.fields.length ? <small>{change.fields.map((field) => DIFF_FIELD_LABELS[field] ?? field).join('、')}</small> : null}</div></li>)}</ul></div> : <div className="relationship-dialog-empty"><Check size={18} />两个版本的创作语义完全一致</div>}
      </Modal>

      <Modal className="modal--relationship-change" description="系统会先计算影响范围；确认后才复制为可编辑草稿。" footer={<><Button disabled={busy} onClick={() => setDialog(null)} variant="secondary">取消</Button>{revisionImpact ? <Button disabled={busy} onClick={() => void run(confirmRevision)}>确认影响并创建修改版</Button> : <Button disabled={busy || revisionKeys.length === 0 || revisionIntent.trim().length < 6} onClick={() => void run(analyzeRevision)}>查看影响范围</Button>}</>} onClose={() => setDialog(null)} open={dialog === 'revision'} title="创建关系修改版">
        <div className="relationship-revision-form"><label>修改意图<textarea onChange={(event) => { setRevisionIntent(event.target.value); setRevisionImpact(null) }} rows={3} value={revisionIntent} /></label><fieldset><legend>计划修改的关系</legend>{selectedGraph.graph.edges.map((edge) => <label key={edge.relationshipKey}><input checked={revisionKeys.includes(edge.relationshipKey)} onChange={(event) => { setRevisionKeys((current) => event.target.checked ? [...current, edge.relationshipKey] : current.filter((key) => key !== edge.relationshipKey)); setRevisionImpact(null) }} type="checkbox" />{characterName(characters, edge.sourceCharacterKey)} ↔ {characterName(characters, edge.targetCharacterKey)}</label>)}</fieldset>{revisionImpact ? <section className="relationship-impact"><header><span className="relationship-impact-level is-p1">需确认</span><div><strong>将影响 {revisionImpact.affected.episodeOrdinals.length} 集、{revisionImpact.affected.scenes.length} 个定位场景</strong><p>当前剧本会立即标记为过期，不能继续批准。</p></div></header><dl><div><dt>受影响集数</dt><dd>{revisionImpact.affected.episodeOrdinals.map((ordinal) => `第 ${ordinal} 集`).join('、') || '尚无已生成集数'}</dd></div><div><dt>受影响场景</dt><dd>{revisionImpact.affected.scenes.map((scene) => `场景 ${scene.ordinal} · ${scene.heading}`).join('、') || '尚无已生成场景'}</dd></div><div><dt>需要重生成</dt><dd>{revisionImpact.affected.regenerateAssetTypes.join('、')}</dd></div><div><dt>继续保留</dt><dd>{revisionImpact.affected.preservedAssetTypes.join('、')}</dd></div><div><dt>预计成本</dt><dd>{revisionImpact.estimate.seconds} 秒 · {revisionImpact.estimate.points} 积分</dd></div></dl></section> : null}</div>
      </Modal>
    </section>
  )
}
