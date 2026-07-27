import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  applyNodeChanges,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  Check,
  Clapperboard,
  Crosshair,
  GitBranch,
  GitMerge,
  LockKeyhole,
  LoaderCircle,
  Minimize2,
  RefreshCw,
  ScanSearch,
  Sparkles,
  UsersRound,
} from 'lucide-react'
import { Link, useParams } from 'react-router'
import {
  ApiError,
  createDirectorReviewProposal,
  createRelationshipGraphRevision,
  decideDirectorReviewProposal,
  executeDirectorReviewProposal,
  fetchProject,
  fetchCanvasProjection,
  fetchDirectorReviewProposals,
  fetchStoryWorkspace,
  analyzeRelationshipRevisionImpact,
  confirmCharacterRevision,
  regenerateStoryboardShot,
  reviewCharacterRevision,
  updateScriptScene,
  updatePersistedShot,
  type CanvasProjection,
  type CanvasReference,
  type CharacterRevisionChanges,
  type CharacterRevisionReview,
  type DirectorReviewProposal,
  type RelationshipGraphVersionRecord,
  type RelationshipRevisionImpact,
  type StoryWorkspace,
} from '../api/client'
import {
  fetchDirectorGenerationHistory,
  retryDirectorGeneration,
  type DirectorGenerationFailure,
  type DirectorGenerationRecord,
} from '../api/directorFailures'
import {
  canvasProjectionSignature,
  createCanvasViewState,
  parseCanvasViewState,
  projectCanvasGraph,
  resolveDirectorReviewTarget,
  type FilmCanvasEdge,
  type FilmCanvasEmptyLaneNode,
  type FilmCanvasGraphNode,
  type FilmCanvasLaneHeaderNode,
  type FilmCanvasNode,
  isFilmObjectNode,
} from '../canvas/filmCanvasProjection'
import { getFilmCanvasGuidance } from '../canvas/filmCanvasGuidance'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { DirectorFailureInspector } from '../components/director-review/DirectorFailureInspector'
import { DirectorGenerationHistory } from '../components/director-review/DirectorGenerationHistory'
import {
  DirectorReviewCard,
  directorApprovalRequiresOverride,
  type DirectorReviewAction,
} from '../components/director-review/DirectorReviewCard'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import {
  Button,
  Modal,
  PageHeader,
  SelectControl,
  StatusBadge,
  Surface,
  getStatusLabel,
} from '../components/ui'
import { useToast } from '../store/ToastContext'
import { useProjectReadiness } from '../store/ProjectReadinessContext'

const VIEW_STATE_KEY_PREFIX = 'film-canvas-view-state-v1:production-lanes-v2'
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }
const PROJECTION_REFRESH_INTERVAL_MS = 3_000

const objectTypeLabels: Record<string, string> = {
  Project: '项目',
  Story: '故事',
  Script: '剧本',
  Beat: '剧情关键点',
  ScriptScene: '剧本场次',
  DialogueLine: '对白',
  Scene: '制作场景',
  Character: '角色',
  Location: '地点',
  Prop: '道具',
  Storyboard: '故事板',
  Shot: '镜头',
  Timeline: '时间线',
  DirectorProposal: '导演建议',
}

const canonicalKindLabels: Record<string, string> = {
  CANONICAL: '正式事实',
  DERIVED: '推导事实',
  GENERATED: '生成事实',
}

function objectTypeLabel(item: CanvasProjection['nodes'][number]): string {
  if (item.ref.type === 'DialogueLine') {
    if (item.operationContext.line_type === 'ACTION') return '场景动作'
    if (item.operationContext.line_type === 'VOICE_OVER') return '画外音'
  }
  return objectTypeLabels[item.ref.type] ?? item.ref.type
}

type CharacterEditDraft = {
  name: string
  role: string
  personality: string
  dramaticFunction: string
  desire: string
  fear: string
  secret: string
  visualNotes: string
}

type CharacterEditorState = {
  characterKey: string
  storyBibleId: string
  relationshipGraphId: string
  draft: CharacterEditDraft
}

type RelationshipEditorState = {
  graph: RelationshipGraphVersionRecord
  characterKey: string
  relationshipKeys: string[]
  selectedKeys: string[]
  intent: string
}

type ShotEditDraft = {
  shotId: string
  shotSpecId: string | null
  shotLockVersion: number
  description: string
  dialogue: string
  shotSize: 'WS' | 'MS' | 'MCU' | 'CU'
  cameraMovement: 'STATIC' | 'PAN' | 'DOLLY_IN' | 'TRACK' | 'HANDHELD'
  downstreamAction: 'REVIEW' | 'REGENERATE'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringField(value: Record<string, unknown>, field: string): string {
  const candidate = value[field]
  return typeof candidate === 'string' ? candidate : ''
}

function latestStoryContext(workspace: StoryWorkspace) {
  const bible = workspace.storyBibleVersions[0]
  if (!bible) return null
  const graph = workspace.relationshipGraphVersions.find(
    (item) => item.storyBibleVersionId === bible.id,
  ) ?? workspace.relationshipGraphVersions[0]
  return graph ? { bible, graph } : null
}

function characterFromStoryBible(
  workspace: StoryWorkspace,
  characterKey: string,
): Record<string, unknown> | null {
  const context = latestStoryContext(workspace)
  const characters = context?.bible.payload.characters
  if (!Array.isArray(characters)) return null
  return characters.find(
    (item): item is Record<string, unknown> => isRecord(item)
      && stringField(item, 'key') === characterKey,
  ) ?? null
}

function relationshipKeysForCharacter(
  graph: RelationshipGraphVersionRecord,
  characterKey: string,
): string[] {
  return graph.graph.edges
    .filter(
      (edge) => edge.sourceCharacterKey === characterKey
        || edge.targetCharacterKey === characterKey,
    )
    .map((edge) => edge.relationshipKey)
}

function viewStateKey(projectId: string): string {
  return `${VIEW_STATE_KEY_PREFIX}:${projectId}`
}

function FilmObjectNode({ data, selected }: NodeProps<FilmCanvasNode>) {
  const item = data.projection
  return (
    <article className={`film-canvas-node ${selected ? 'is-selected' : ''}`}>
      <Handle
        className="film-canvas-node__handle"
        isConnectable={false}
        position={Position.Left}
        type="target"
      />
      <header>
        <span>{objectTypeLabel(item)}</span>
        <StatusBadge status={item.canonicalStatus} />
      </header>
      {data.speakerLabel ? (
        <p className="film-canvas-node__speaker">说话人：<strong>{data.speakerLabel}</strong></p>
      ) : null}
      <div className="film-canvas-node__content">
        {item.thumbnailUrl ? (
          <img
            alt={`${item.label} 角色形象缩略图`}
            className="film-canvas-node__thumbnail"
            src={item.thumbnailUrl}
          />
        ) : null}
        <strong title={item.label}>{item.label}</strong>
      </div>
      {item.contentSummary ? (
        <div className="film-canvas-node__detail">
          <small>详细描述</small>
          <p className="film-canvas-node__summary" title={item.contentSummary}>{item.contentSummary}</p>
        </div>
      ) : null}
      <footer>
        <small>{item.ref.versionId ? `版本 ${item.ref.versionId.slice(0, 8)}` : '无独立版本'}</small>
      </footer>
      <Handle
        className="film-canvas-node__handle"
        isConnectable={false}
        position={Position.Right}
        type="source"
      />
    </article>
  )
}

function FilmCanvasEmptyLaneNode({ data }: NodeProps<FilmCanvasEmptyLaneNode>) {
  const icon = data.variant === 'assets'
    ? <UsersRound size={20} />
    : data.variant === 'timeline'
      ? <GitMerge size={20} />
      : <Clapperboard size={20} />
  return (
    <section
      className={`film-canvas-empty-lane film-canvas-empty-lane--${data.variant}`}
      aria-label={data.label}
    >
      <span className="film-canvas-empty-lane__icon">{icon}</span>
      <p className="eyebrow">{data.eyebrow}</p>
      <h3>{data.label}</h3>
      <p>{data.description}</p>
      <small>{data.status}</small>
      <Link
        className="button button--secondary button--sm film-canvas-empty-lane__action nodrag nopan"
        to={data.actionHref}
      >
        {data.actionLabel}<ArrowUpRight size={14} />
      </Link>
    </section>
  )
}

function FilmCanvasLaneHeaderNode({ data }: NodeProps<FilmCanvasLaneHeaderNode>) {
  return (
    <header className="film-canvas-lane-header" aria-label={`${data.label}区域`}>
      <span>{String(data.index).padStart(2, '0')}</span>
      <strong>{data.label}</strong>
    </header>
  )
}

const nodeTypes = {
  emptyLane: FilmCanvasEmptyLaneNode,
  filmObject: FilmObjectNode,
  laneHeader: FilmCanvasLaneHeaderNode,
}

export function FilmCanvasPage() {
  const { projectId } = useParams()
  const { notify } = useToast()
  const { readiness } = useProjectReadiness()
  const [projection, setProjection] = useState<CanvasProjection | null>(null)
  const [nodes, setNodes] = useState<FilmCanvasGraphNode[]>([])
  const [edges, setEdges] = useState<FilmCanvasEdge[]>([])
  const [viewport, setViewport] = useState<Viewport>(DEFAULT_VIEWPORT)
  const [hasSavedViewport, setHasSavedViewport] = useState(false)
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<FilmCanvasGraphNode, FilmCanvasEdge> | null>(null)
  const [isCanvasFullscreen, setCanvasFullscreen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [directorProposals, setDirectorProposals] = useState<DirectorReviewProposal[]>([])
  const [directorSelections, setDirectorSelections] = useState<Record<string, string>>({})
  const [directorHistory, setDirectorHistory] = useState<DirectorGenerationRecord[]>([])
  const [directorBusy, setDirectorBusy] = useState(false)
  const [directorError, setDirectorError] = useState<string | null>(null)
  const [directorAction, setDirectorAction] = useState<DirectorReviewAction | null>(null)
  const [directorApprovalOverrideReason, setDirectorApprovalOverrideReason] = useState('')
  const [beatEditor, setBeatEditor] = useState<{
    scriptId: string
    sceneId: string
    description: string
  } | null>(null)
  const [beatEditorBusy, setBeatEditorBusy] = useState(false)
  const [beatEditorError, setBeatEditorError] = useState<string | null>(null)
  const [canvasActionBusy, setCanvasActionBusy] = useState(false)
  const [canvasActionError, setCanvasActionError] = useState<string | null>(null)
  const [characterEditor, setCharacterEditor] = useState<CharacterEditorState | null>(null)
  const [characterReview, setCharacterReview] = useState<CharacterRevisionReview | null>(null)
  const [relationshipEditor, setRelationshipEditor] = useState<RelationshipEditorState | null>(null)
  const [relationshipImpact, setRelationshipImpact] = useState<RelationshipRevisionImpact | null>(null)
  const [shotEditor, setShotEditor] = useState<ShotEditDraft | null>(null)
  const projectionSignatureRef = useRef<string | null>(null)

  const openCanvasFullscreen = useCallback(() => {
    setCanvasFullscreen(true)
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        void reactFlowInstance?.fitView({ duration: 180 })
      })
    })
  }, [reactFlowInstance])

  useEffect(() => {
    if (!isCanvasFullscreen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCanvasFullscreen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isCanvasFullscreen])

  const load = useCallback(async (signal?: AbortSignal, force = false) => {
    if (!projectId) return
    const nextProjection = await fetchCanvasProjection(projectId, signal)
    const nextSignature = canvasProjectionSignature(nextProjection)
    if (!force && projectionSignatureRef.current === nextSignature) return
    let saved = null
    try {
      saved = parseCanvasViewState(
        window.localStorage.getItem(viewStateKey(projectId)),
        projectId,
      )
    } catch {
      // 浏览器禁用本地存储时，画布仍可作为无持久布局的只读投影使用。
    }
    const graph = projectCanvasGraph(nextProjection, saved)
    setProjection(nextProjection)
    setNodes(graph.nodes)
    setEdges(graph.edges)
    setViewport(graph.viewport ?? DEFAULT_VIEWPORT)
    setHasSavedViewport(graph.viewport !== null)
    projectionSignatureRef.current = nextSignature
  }, [projectId])

  useEffect(() => {
    const controller = new AbortController()
    let hasSnapshot = false
    projectionSignatureRef.current = null
    setLoading(true)
    setError(null)
    const refresh = async () => {
      try {
        await load(controller.signal)
        if (!controller.signal.aborted) {
          hasSnapshot = true
          setError(null)
        }
      } catch (reason) {
        if (!controller.signal.aborted && !hasSnapshot) {
          setError(reason instanceof Error ? reason.message : 'Film IR 投影读取失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, PROJECTION_REFRESH_INTERVAL_MS)
    return () => {
      controller.abort()
      window.clearInterval(interval)
    }
  }, [load])

  const loadDirectorProposals = useCallback(async (signal?: AbortSignal) => {
    if (!projectId) return
    const next = await fetchDirectorReviewProposals(projectId, signal)
    setDirectorProposals(next)
  }, [projectId])

  useEffect(() => {
    const controller = new AbortController()
    const refresh = () => {
      void loadDirectorProposals(controller.signal).catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setDirectorError(reason instanceof Error ? reason.message : 'Director 建议读取失败')
      })
    }
    refresh()
    const interval = window.setInterval(refresh, PROJECTION_REFRESH_INTERVAL_MS)
    return () => {
      controller.abort()
      window.clearInterval(interval)
    }
  }, [loadDirectorProposals])

  useEffect(() => {
    if (!projection || !projectId) return
    const timer = window.setTimeout(() => {
      const selected = nodes
        .filter((node): node is FilmCanvasNode => node.selected === true && isFilmObjectNode(node))
        .map((node) => node.data.projection.ref)
      const viewState = createCanvasViewState(projection, nodes, viewport, selected)
      try {
        window.localStorage.setItem(viewStateKey(projectId), JSON.stringify(viewState))
      } catch {
        // ViewState 写入失败不会影响领域数据或画布读取。
      }
    }, 200)
    return () => window.clearTimeout(timer)
  }, [nodes, projectId, projection, viewport])

  const onNodesChange = useCallback((changes: NodeChange<FilmCanvasGraphNode>[]) => {
    setNodes((current) => applyNodeChanges(changes, current))
  }, [])

  const selectedNode = useMemo(
    () => nodes.find(
      (node): node is FilmCanvasNode => node.selected === true && isFilmObjectNode(node),
    ) ?? null,
    [nodes],
  )
  const initialFitViewNodes = useMemo(
    () => nodes.filter((node) => !isFilmObjectNode(node)).map((node) => ({ id: node.id })),
    [nodes],
  )

  const domainNodeCount = useMemo(
    () => nodes.filter(isFilmObjectNode).length,
    [nodes],
  )

  const canvasGuidance = useMemo(
    () => readiness ? getFilmCanvasGuidance(readiness) : null,
    [readiness],
  )

  const recommendedNode = useMemo(
    () => canvasGuidance?.recommendedNodeType
      ? nodes.find(
        (node): node is FilmCanvasNode => isFilmObjectNode(node)
          && node.data.projection.ref.type === canvasGuidance.recommendedNodeType,
      ) ?? null
      : null,
    [canvasGuidance, nodes],
  )

  const focusRecommendedNode = useCallback(() => {
    if (!recommendedNode) return
    setNodes((current) => current.map((node) => ({
      ...node,
      selected: node.id === recommendedNode.id,
    })))
    window.requestAnimationFrame(() => {
      void reactFlowInstance?.fitView({
        duration: 240,
        maxZoom: 1.1,
        nodes: [{ id: recommendedNode.id }],
      })
    })
  }, [reactFlowInstance, recommendedNode])

  const selectedReferences = useMemo<CanvasReference[]>(
    () => nodes
      .filter((node): node is FilmCanvasNode => node.selected === true && isFilmObjectNode(node))
      .map((node) => node.data.projection.ref),
    [nodes],
  )

  const directorTarget = useMemo(
    () => projection && selectedNode
      ? resolveDirectorReviewTarget(projection, selectedNode.data.projection.ref)
      : null,
    [projection, selectedNode],
  )

  const beatEditTarget = useMemo(() => {
    if (!projection || !selectedNode) return null
    const beat = selectedNode.data.projection
    if (beat.ref.type !== 'Beat' || !beat.ref.versionId) return null
    const sceneEdge = projection.edges.find(
      (edge) => edge.relation === 'BEAT_TO_SCRIPT_SCENE'
        && edge.source.type === 'Beat'
        && edge.source.id === beat.ref.id
        && edge.target.type === 'ScriptScene'
        && edge.target.versionId,
    )
    if (!sceneEdge?.target.versionId) return null
    return {
      scriptId: beat.ref.versionId,
      sceneId: sceneEdge.target.versionId,
      description: beat.contentSummary ?? '',
    }
  }, [projection, selectedNode])

  useEffect(() => {
    setBeatEditorError(null)
    setBeatEditor(beatEditTarget)
  }, [
    beatEditTarget?.description,
    beatEditTarget?.sceneId,
    beatEditTarget?.scriptId,
  ])

  const loadDirectorHistory = useCallback(async (
    scriptSceneId: string,
    signal?: AbortSignal,
  ) => {
    if (!projectId) return
    const history = await fetchDirectorGenerationHistory(projectId, scriptSceneId, signal)
    setDirectorHistory(history)
  }, [projectId])

  useEffect(() => {
    const scriptSceneId = directorTarget?.scriptSceneId
    setDirectorHistory([])
    setDirectorError(null)
    if (!scriptSceneId) return
    const controller = new AbortController()
    void loadDirectorHistory(scriptSceneId, controller.signal).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setDirectorError(reason instanceof Error ? reason.message : 'Director 审查历史读取失败')
    })
    return () => controller.abort()
  }, [directorTarget?.scriptSceneId, loadDirectorHistory])

  const selectedDirectorProposal = useMemo(() => {
    if (!projection || !directorTarget || !selectedNode) return null
    const selectedRef = selectedNode.data.projection.ref
    const logicalScriptSceneId = selectedRef.type === 'ScriptScene'
      ? selectedRef.id
      : projection.edges.find(
        (edge) => edge.relation === 'REALIZED_AS_SCENE'
          && edge.target.type === 'Scene'
          && edge.target.id === selectedRef.id,
      )?.source.id
    const proposalIds = projection.edges
      .filter(
        (edge) => edge.relation === 'PROPOSES_CHANGE_TO'
          && edge.target.type === 'ScriptScene'
          && edge.target.id === logicalScriptSceneId,
      )
      .map((edge) => edge.source.id)
      .reverse()
    return proposalIds
      .map((proposalId) => directorProposals.find((item) => item.proposalId === proposalId))
      .find((item): item is DirectorReviewProposal => item !== undefined)
      ?? directorProposals.find((item) => item.scriptSceneId === directorTarget.scriptSceneId)
      ?? null
  }, [directorProposals, directorTarget, projection, selectedNode])

  const selectedDirectorOption = useMemo(() => {
    if (!selectedDirectorProposal) return null
    const optionId = directorSelections[selectedDirectorProposal.proposalId]
      ?? selectedDirectorProposal.recommendedOption
    return selectedDirectorProposal.alternatives.find((item) => item.optionId === optionId)
      ?? selectedDirectorProposal.alternatives[0]
      ?? null
  }, [directorSelections, selectedDirectorProposal])

  const latestDirectorFailure = useMemo<DirectorGenerationFailure | null>(() => {
    const latest = directorHistory[0]
    return latest?.status === 'FAILED' ? { ...latest, status: 'FAILED' } : null
  }, [directorHistory])

  function upsertDirectorProposal(next: DirectorReviewProposal) {
    setDirectorProposals((current) => [
      next,
      ...current.filter((item) => item.proposalId !== next.proposalId),
    ])
    setDirectorSelections((current) => ({
      ...current,
      [next.proposalId]: current[next.proposalId] ?? next.recommendedOption,
    }))
  }

  async function reviewSelectedObject(intentInstruction?: string) {
    if (!projectId || !projection || !directorTarget || directorBusy) return
    setDirectorBusy(true)
    setDirectorError(null)
    try {
      const proposal = await createDirectorReviewProposal(projectId, {
        expectedVersion: projection.projectLockVersion,
        targetType: directorTarget.targetType,
        targetId: directorTarget.targetId,
        issueTypes: ['STORY_LOGIC', 'CHARACTER_MOTIVATION', 'AI_DIALOGUE', 'PACING'],
        instruction: intentInstruction?.trim()
          || '检查故事因果、人物当下目标、对白 AI 味和场景节奏。',
        compileIntent: Boolean(intentInstruction?.trim()),
      })
      upsertDirectorProposal(proposal)
      await load(undefined, true)
      notify('Director 已完成审查，请选择修复方案。')
    } catch (reason) {
      setDirectorError(
        reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
          ? '项目版本已经变化，请等待画布刷新后重新审查。'
          : reason instanceof Error ? reason.message : 'Director 审查失败',
      )
      await loadDirectorHistory(directorTarget.scriptSceneId).catch(() => undefined)
    } finally {
      setDirectorBusy(false)
    }
  }

  async function retryFailedDirector(failure: DirectorGenerationFailure) {
    if (!projectId || !projection || !directorTarget || directorBusy) return
    setDirectorBusy(true)
    setDirectorError(null)
    try {
      const proposal = await retryDirectorGeneration(projectId, failure, {
        expectedVersion: projection.projectLockVersion,
        targetType: directorTarget.targetType,
        targetId: directorTarget.targetId,
        issueTypes: ['STORY_LOGIC', 'CHARACTER_MOTIVATION', 'AI_DIALOGUE', 'PACING'],
        instruction: '重新检查故事因果、人物当下目标、对白 AI 味和场景节奏。',
      })
      upsertDirectorProposal(proposal)
      await Promise.all([
        load(undefined, true),
        loadDirectorProposals(),
        loadDirectorHistory(directorTarget.scriptSceneId),
      ])
      notify('Director 已创建新的审查记录；原失败记录保持可追溯。')
    } catch (reason) {
      setDirectorError(
        reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
          ? '项目版本已经变化，请等待画布刷新后重新审查。'
          : reason instanceof Error ? reason.message : 'Director 重新审查失败',
      )
      await loadDirectorHistory(directorTarget.scriptSceneId).catch(() => undefined)
    } finally {
      setDirectorBusy(false)
    }
  }

  async function confirmDirectorAction() {
    if (!directorAction || !projection || directorBusy) return
    const action = directorAction
    setDirectorBusy(true)
    setDirectorError(null)
    try {
      const next = action.type === 'EXECUTE'
          ? await executeDirectorReviewProposal(action.proposal.proposalId, {
            expectedVersion: projection.projectLockVersion,
            optionId: action.optionId,
            intentConfirmationToken: action.proposal.directorIntentConfirmationToken,
          })
        : await decideDirectorReviewProposal(action.proposal.proposalId, {
            expectedVersion: projection.projectLockVersion,
            decision: action.decision,
            overrideReason: directorApprovalRequiresOverride(action)
              ? directorApprovalOverrideReason
              : undefined,
          })
      upsertDirectorProposal(next)
      setDirectorAction(null)
      setDirectorApprovalOverrideReason('')
      await Promise.all([load(undefined, true), loadDirectorProposals()])
      notify(
        action.type === 'EXECUTE'
          ? '修改版剧本已创建；相关内容已标记为待检查。'
          : action.decision === 'APPROVE'
            ? '已确认使用这个修改版。'
            : action.decision === 'ROLLBACK'
              ? '已恢复原来的版本，之前的版本仍然保留。'
              : '未采用这条建议，剧本没有变化。',
      )
    } catch (reason) {
      setDirectorError(
        reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
          ? '项目版本已经变化，请等待画布刷新后重新确认。'
          : reason instanceof ApiError
            && reason.code === 'DIRECTOR_APPROVAL_OVERRIDE_REASON_REQUIRED'
            ? '低成本时间线预览仍需调整；请填写至少 8 个字的覆盖理由。'
          : reason instanceof Error ? reason.message : 'Director 操作失败',
      )
    } finally {
      setDirectorBusy(false)
    }
  }

  function openDirectorAction(action: DirectorReviewAction) {
    setDirectorApprovalOverrideReason('')
    setDirectorAction(action)
  }

  async function submitBeatRevision() {
    if (!beatEditor || !projection || beatEditorBusy) return
    const description = beatEditor.description.trim()
    if (!description) {
      setBeatEditorError('请填写叙事节拍。')
      return
    }
    setBeatEditorBusy(true)
    setBeatEditorError(null)
    try {
      const revision = await updateScriptScene(beatEditor.scriptId, beatEditor.sceneId, {
        expectedVersion: projection.projectLockVersion,
        beatDescription: description,
      })
      setBeatEditor(null)
      await load(undefined, true)
      notify(`已创建剧本第 ${revision.version} 版，并提交审核。`)
    } catch (reason) {
      setBeatEditorError(
        reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
          ? '项目版本已变化，请刷新画布后重新提交。'
          : reason instanceof ApiError && reason.code === 'SCRIPT_BEAT_LINEAGE_MISSING'
            ? '当前节拍与剧本场次的对应关系已变化，请刷新画布后重试。'
            : reason instanceof Error ? reason.message : '提交修改版失败',
      )
    } finally {
      setBeatEditorBusy(false)
    }
  }

  async function openCharacterEditor() {
    if (!projectId || !selectedNode || canvasActionBusy) return
    const characterKey = selectedNode.data.projection.operationContext.character_key
    if (typeof characterKey !== 'string' || !characterKey) {
      setCanvasActionError('当前角色缺少故事设定键，无法安全定位正式版本。')
      return
    }
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      const workspace = await fetchStoryWorkspace(projectId)
      const context = latestStoryContext(workspace)
      const character = characterFromStoryBible(workspace, characterKey)
      if (!context || !character) {
        throw new Error('当前角色尚未进入故事设定集，请先完成故事设定。')
      }
      const personality = character.personality
      setCharacterEditor({
        characterKey,
        storyBibleId: context.bible.id,
        relationshipGraphId: context.graph.id,
        draft: {
          name: stringField(character, 'name'),
          role: stringField(character, 'role'),
          personality: Array.isArray(personality)
            ? personality.filter((item): item is string => typeof item === 'string').join('、')
            : '',
          dramaticFunction: stringField(character, 'dramatic_function'),
          desire: stringField(character, 'desire'),
          fear: stringField(character, 'fear'),
          secret: stringField(character, 'secret'),
          visualNotes: stringField(character, 'visual_notes'),
        },
      })
      setCharacterReview(null)
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '角色设定读取失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  function characterChanges(): CharacterRevisionChanges {
    if (!characterEditor) return {}
    const draft = characterEditor.draft
    const optional = (value: string) => value.trim() || undefined
    const personality = draft.personality
      .split(/[、,，]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 5)
    return {
      name: optional(draft.name),
      role: optional(draft.role),
      ...(personality.length ? { personality } : {}),
      dramatic_function: optional(draft.dramaticFunction),
      desire: optional(draft.desire),
      fear: optional(draft.fear),
      secret: optional(draft.secret),
      visual_notes: optional(draft.visualNotes),
    }
  }

  async function reviewCharacterChanges() {
    if (!projectId || !projection || !characterEditor || canvasActionBusy) return
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      const review = await reviewCharacterRevision(projectId, {
        baseStoryBibleId: characterEditor.storyBibleId,
        baseRelationshipGraphId: characterEditor.relationshipGraphId,
        characterKey: characterEditor.characterKey,
        changes: characterChanges(),
        expectedVersion: projection.projectLockVersion,
      })
      setCharacterReview(review)
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '角色修改影响检查失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  async function confirmCharacterChanges() {
    if (!projectId || !projection || !characterEditor || !characterReview || canvasActionBusy) return
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      await confirmCharacterRevision(projectId, {
        baseStoryBibleId: characterReview.baseStoryBibleId,
        baseRelationshipGraphId: characterReview.baseRelationshipGraphId,
        characterKey: characterEditor.characterKey,
        changes: characterChanges(),
        expectedVersion: projection.projectLockVersion,
        impactHash: characterReview.impactHash,
      })
      setCharacterEditor(null)
      setCharacterReview(null)
      await load(undefined, true)
      notify('角色修改版与关系草稿已创建；旧版本继续保留。')
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '角色修改版创建失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  async function openRelationshipEditor() {
    if (!projectId || !selectedNode || canvasActionBusy) return
    const characterKey = selectedNode.data.projection.operationContext.character_key
    if (typeof characterKey !== 'string' || !characterKey) {
      setCanvasActionError('当前角色缺少故事设定键，无法定位人物关系。')
      return
    }
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      const workspace = await fetchStoryWorkspace(projectId)
      const context = latestStoryContext(workspace)
      if (!context) throw new Error('当前项目还没有可修改的人物关系版本。')
      const relationshipKeys = relationshipKeysForCharacter(context.graph, characterKey)
      setRelationshipEditor({
        graph: context.graph,
        characterKey,
        relationshipKeys,
        selectedKeys: relationshipKeys,
        intent: '',
      })
      setRelationshipImpact(null)
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '人物关系读取失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  async function analyzeRelationshipImpact() {
    if (!relationshipEditor || canvasActionBusy) return
    if (!relationshipEditor.selectedKeys.length) {
      setCanvasActionError('请至少选择一条要修改的人物关系。')
      return
    }
    if (relationshipEditor.intent.trim().length < 6) {
      setCanvasActionError('请用至少 6 个字说明关系要如何变化。')
      return
    }
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      const impact = await analyzeRelationshipRevisionImpact(
        relationshipEditor.graph,
        relationshipEditor.selectedKeys,
        relationshipEditor.intent,
      )
      setRelationshipImpact(impact)
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '关系修改影响分析失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  async function confirmRelationshipRevision() {
    if (!relationshipImpact || canvasActionBusy) return
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      await createRelationshipGraphRevision(relationshipImpact)
      setRelationshipEditor(null)
      setRelationshipImpact(null)
      await load(undefined, true)
      notify('人物关系修改版已创建；受影响的故事线与剧本已标记为待重审。')
    } catch (reason) {
      setCanvasActionError(reason instanceof Error ? reason.message : '关系修改版创建失败')
    } finally {
      setCanvasActionBusy(false)
    }
  }

  function openShotEditor() {
    if (!selectedNode || selectedNode.data.projection.ref.type !== 'Shot') return
    const item = selectedNode.data.projection
    const context = item.operationContext
    const shotLockVersion = context.shot_lock_version
    if (typeof shotLockVersion !== 'number') {
      setCanvasActionError('当前镜头缺少锁版本，请等待画布刷新后重试。')
      return
    }
    setCanvasActionError(null)
    setShotEditor({
      shotId: item.ref.id,
      shotSpecId: item.ref.versionId,
      shotLockVersion,
      description: typeof context.description === 'string' ? context.description : '',
      dialogue: typeof context.dialogue === 'string' ? context.dialogue : '',
      shotSize: ['WS', 'MS', 'MCU', 'CU'].includes(String(context.shot_size))
        ? context.shot_size as ShotEditDraft['shotSize']
        : 'MS',
      cameraMovement: ['STATIC', 'PAN', 'DOLLY_IN', 'TRACK', 'HANDHELD'].includes(
        String(context.camera_movement),
      )
        ? context.camera_movement as ShotEditDraft['cameraMovement']
        : 'STATIC',
      downstreamAction: 'REVIEW',
    })
  }

  async function submitShotRevision() {
    if (!projectId || !shotEditor || canvasActionBusy) return
    if (!shotEditor.description.trim()) {
      setCanvasActionError('请填写镜头内容。')
      return
    }
    setCanvasActionBusy(true)
    setCanvasActionError(null)
    try {
      await updatePersistedShot(shotEditor.shotId, shotEditor.shotLockVersion, {
        description: shotEditor.description.trim(),
        dialogue: shotEditor.dialogue.trim(),
        shotSize: shotEditor.shotSize,
        cameraMovement: shotEditor.cameraMovement,
      })
      if (shotEditor.downstreamAction === 'REGENERATE') {
        if (!shotEditor.shotSpecId) {
          throw new Error('当前镜头还没有分镜规格，无法重生成下游内容。')
        }
        const currentProject = await fetchProject(projectId)
        await regenerateStoryboardShot(
          shotEditor.shotSpecId,
          currentProject.lockVersion,
          '画布内修改镜头规划后重生成下游内容',
        )
        notify('镜头规划已保存，下游关键帧与节奏样片正在重生成。')
      } else {
        notify('镜头规划已保存，相关分镜与下游内容已标记为待重审。')
      }
      setShotEditor(null)
      await load(undefined, true)
    } catch (reason) {
      setCanvasActionError(
        reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
          ? '镜头版本已经变化，请刷新画布后重新提交。'
          : reason instanceof Error ? reason.message : '镜头规划保存失败',
      )
    } finally {
      setCanvasActionBusy(false)
    }
  }

  if (!projectId) {
    return <div className="page"><p role="alert">缺少项目编号，无法读取创作画布。</p></div>
  }
  if (loading) {
    return <PageLoadingSkeleton label="正在构建创作画布" stage="读取 Film IR 只读投影" />
  }
  if (!projection || error) {
    return (
      <div className="page page--film-canvas">
        <PageHeader
          title="Agentic Film Canvas"
          description="画布无法读取项目关系；请检查服务连接后重试。"
          actions={<Button onClick={() => window.location.reload()} variant="secondary"><RefreshCw size={16} />重试</Button>}
        />
        <div className="brief-save-message brief-save-message--error" role="alert">
          {error ?? '画布投影不可用'}
        </div>
      </div>
    )
  }

  return (
    <div className={`page page--film-canvas${isCanvasFullscreen ? ' is-canvas-fullscreen' : ''}`}>
      <PageHeader
        eyebrow="项目控制台"
        title="项目画布"
        description="在同一张关系图中定位故事、场次、角色与镜头，并发起经过版本与审批约束的正式操作。拖动仅保存个人布局。"
        actions={
          <Button
            onClick={() => {
              try {
                window.localStorage.removeItem(viewStateKey(projectId))
              } catch {
                // 本地存储不可用时直接重建当前会话布局。
              }
              projectionSignatureRef.current = null
              void load(undefined, true)
            }}
            variant="secondary"
          >
            <RefreshCw size={16} />重置布局
          </Button>
        }
      />
      {canvasGuidance ? (
        <Surface
          className={`film-canvas-next-action film-canvas-next-action--${canvasGuidance.tone}`}
        >
          <div className="film-canvas-next-action__marker" aria-hidden="true">
            {canvasGuidance.tone === 'blocked'
              ? <AlertTriangle size={20} />
              : <ArrowRight size={20} />}
          </div>
          <div className="film-canvas-next-action__copy">
            <p className="eyebrow">下一步 · {canvasGuidance.activeStageLabel}</p>
            <h2>{canvasGuidance.title}</h2>
            {canvasGuidance.tone === 'blocked' ? <p>{canvasGuidance.description}</p> : null}
          </div>
          <div className="film-canvas-next-action__actions">
            <Link
              className="button button--primary button--md"
              to={canvasGuidance.actionHref}
            >
              {canvasGuidance.actionLabel}<ArrowRight size={16} />
            </Link>
            {recommendedNode ? (
              <Button onClick={focusRecommendedNode} variant="secondary">
                <Crosshair size={16} />在画布中定位
              </Button>
            ) : null}
          </div>
        </Surface>
      ) : null}
      <section className="film-canvas-summary" aria-label="投影摘要">
        <div><span>对象</span><strong>{domainNodeCount}</strong></div>
        <div><span>依赖</span><strong>{edges.length}</strong></div>
        <div><span>项目版本</span><strong>{projection.projectLockVersion}</strong></div>
        <div><span>选择</span><strong>{selectedReferences.length}</strong></div>
      </section>
      <div className="film-canvas-workspace">
        <Surface className="film-canvas-stage" padding="none">
          <ReactFlow<FilmCanvasGraphNode, FilmCanvasEdge>
            edges={edges}
            fitView={!hasSavedViewport}
            fitViewOptions={{
              maxZoom: 0.85,
              nodes: initialFitViewNodes,
            }}
            maxZoom={1.8}
            minZoom={0.25}
            nodeTypes={nodeTypes}
            nodes={nodes}
            nodesConnectable={false}
            onInit={setReactFlowInstance}
            onNodesChange={onNodesChange}
            onViewportChange={setViewport}
            panOnScroll
            proOptions={{ hideAttribution: false }}
            selectionOnDrag
            viewport={viewport}
          >
            <Background color="var(--color-border)" gap={24} variant={BackgroundVariant.Dots} />
            <Controls onFitView={openCanvasFullscreen} position="top-right" showInteractive={false} />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </Surface>
        {isCanvasFullscreen ? (
          <Button
            autoFocus
            className="film-canvas-fullscreen__exit"
            onClick={() => setCanvasFullscreen(false)}
            variant="secondary"
          >
            <Minimize2 size={16} />退出全屏
          </Button>
        ) : null}
        <Surface as="aside" className="film-canvas-inspector">
          {selectedNode ? (
            <>
              <div>
                <p className="eyebrow">{objectTypeLabel(selectedNode.data.projection)}</p>
                {beatEditTarget ? (
                  <label className="film-canvas-inspector__title-editor">
                    <span>详细描述</span>
                    <textarea
                      aria-label="剧情关键点详细描述"
                      disabled={beatEditorBusy}
                      maxLength={2000}
                      onChange={(event) => setBeatEditor((current) => current
                        ? { ...current, description: event.target.value }
                        : { ...beatEditTarget, description: event.target.value })}
                      rows={5}
                      value={beatEditor?.description ?? beatEditTarget.description}
                    />
                    <span className="film-canvas-inspector__title-editor-meta">
                      <small>{beatEditor?.description.length ?? beatEditTarget.description.length}/2000</small>
                      <Button
                        disabled={
                          beatEditorBusy
                          || !beatEditor?.description.trim()
                          || beatEditor.description.trim() === beatEditTarget.description.trim()
                        }
                        onClick={() => void submitBeatRevision()}
                        size="sm"
                      >
                        <Check size={14} />{beatEditorBusy ? '提交中…' : '提交修改版审核'}
                      </Button>
                    </span>
                    {beatEditorError ? (
                      <span className="film-canvas-inspector__action-error" role="alert">
                        {beatEditorError}
                      </span>
                    ) : null}
                  </label>
                ) : (
                  <h2>{selectedNode.data.projection.label}</h2>
                )}
              </div>
              {selectedNode.data.projection.ref.type === 'Character'
                && selectedNode.data.projection.thumbnailUrl ? (
                  <figure className="film-canvas-inspector__character-image">
                    <img
                      alt={`${selectedNode.data.projection.label} 角色形象`}
                      decoding="async"
                      src={selectedNode.data.projection.thumbnailUrl}
                    />
                    <figcaption>角色形象</figcaption>
                  </figure>
                ) : null}
              <dl>
                {selectedNode.data.projection.ref.type === 'Script'
                  && selectedNode.data.projection.contentSummary ? (
                    <div>
                      <dt>剧本梗概</dt>
                      <dd>{selectedNode.data.projection.contentSummary}</dd>
                    </div>
                  ) : null}
                <div><dt>稳定编号</dt><dd>{selectedNode.data.projection.ref.id}</dd></div>
                <div><dt>版本编号</dt><dd>{selectedNode.data.projection.ref.versionId ?? '未单独版本化'}</dd></div>
                <div><dt>事实类型</dt><dd>{canonicalKindLabels[selectedNode.data.projection.canonicalKind] ?? selectedNode.data.projection.canonicalKind}</dd></div>
                <div><dt>状态</dt><dd>{getStatusLabel(selectedNode.data.projection.canonicalStatus)}</dd></div>
                {selectedNode.data.speakerLabel ? (
                  <div><dt>说话人</dt><dd>{selectedNode.data.speakerLabel}</dd></div>
                ) : null}
              </dl>
              <section className="film-canvas-inspector__actions" aria-label="项目操作">
                <div>
                  <p className="eyebrow">项目操作</p>
                  <h3>继续处理当前对象</h3>
                </div>
                <Link
                  className="button button--secondary button--md"
                  to={selectedNode.data.projection.detailRoute}
                >
                  {selectedNode.data.projection.ref.type === 'Script'
                    ? '查看完整剧本'
                    : selectedNode.data.projection.ref.type === 'Storyboard'
                      ? '进入分镜审核'
                      : '打开完整工作区'}
                  <ArrowUpRight size={16} />
                </Link>
                {selectedNode.data.projection.ref.type === 'Character' ? (
                  <>
                    <Button
                      disabled={canvasActionBusy}
                      onClick={() => void openCharacterEditor()}
                    >
                      {canvasActionBusy
                        ? <LoaderCircle className="spin" size={16} />
                        : <UsersRound size={16} />}
                      修改角色设定
                    </Button>
                    <Button
                      disabled={canvasActionBusy}
                      onClick={() => void openRelationshipEditor()}
                      variant="secondary"
                    >
                      <GitBranch size={16} />修改关系并查看影响
                    </Button>
                  </>
                ) : null}
                {selectedNode.data.projection.ref.type === 'Shot' ? (
                  <Button disabled={canvasActionBusy} onClick={openShotEditor}>
                    <Clapperboard size={16} />修改镜头规划
                  </Button>
                ) : null}
                {directorTarget && !selectedDirectorProposal ? (
                  <Button disabled={directorBusy} onClick={() => void reviewSelectedObject()}>
                    <Sparkles size={16} />{directorBusy ? '审查中…' : '发起场景审查'}
                  </Button>
                ) : null}
                {selectedDirectorProposal?.status === 'PROPOSED' && selectedDirectorOption ? (
                  <Button
                    disabled={directorBusy}
                    onClick={() => openDirectorAction({
                      type: 'EXECUTE',
                      proposal: selectedDirectorProposal,
                      optionId: selectedDirectorOption.optionId,
                    })}
                  >
                    <Check size={16} />采用所选方案
                  </Button>
                ) : null}
                {selectedDirectorProposal?.status === 'APPLIED_PENDING_APPROVAL' ? (
                  <Button
                    disabled={directorBusy}
                    onClick={() => openDirectorAction({
                      type: 'DECIDE',
                      proposal: selectedDirectorProposal,
                      decision: 'APPROVE',
                    })}
                  >
                    <Check size={16} />批准修改版
                  </Button>
                ) : null}
                {selectedDirectorProposal
                  && ['APPROVED', 'REJECTED', 'ROLLED_BACK'].includes(selectedDirectorProposal.status) ? (
                    <Button disabled={directorBusy} onClick={() => void reviewSelectedObject()}>
                      <Sparkles size={16} />{directorBusy ? '审查中…' : '重新审查'}
                    </Button>
                  ) : null}
                {canvasActionError ? (
                  <p className="film-canvas-inspector__action-error" role="alert">
                    {canvasActionError}
                  </p>
                ) : null}
              </section>
              <small>所有写入继续经过现有 Command / Domain API、项目版本与审批校验；画布不维护第二套业务状态。</small>
            </>
          ) : (
            <div className="film-canvas-inspector__empty">
              <p className="eyebrow">Inspector</p>
              <h2>选择一个对象</h2>
              <p>选择节点后，可查看版本与审批状态，并从这里继续编辑或发起可用操作。</p>
            </div>
          )}
        </Surface>
      </div>
      {selectedNode && ['Scene', 'ScriptScene'].includes(selectedNode.data.projection.ref.type) ? (
        <Surface className="film-canvas-director">
          <header>
            <div>
              <p className="eyebrow">同一 Command 边界</p>
              <h2>AI Director 场景审查</h2>
            </div>
            <small>Proposal → 用户确认 → Command → ChangeSet</small>
          </header>
          {directorError ? (
            <div className="brief-save-message brief-save-message--error" role="alert">
              {directorError}
            </div>
          ) : null}
          {directorTarget ? (
            <>
              <DirectorGenerationHistory records={directorHistory} />
              {latestDirectorFailure ? (
                <DirectorFailureInspector
                  busy={directorBusy}
                  failure={latestDirectorFailure}
                  onRetry={(failure) => void retryFailedDirector(failure)}
                />
              ) : null}
              <DirectorReviewCard
                busy={directorBusy}
                onAction={openDirectorAction}
                onReview={(instruction) => void reviewSelectedObject(instruction)}
                onSelectOption={(proposalId, optionId) => {
                  setDirectorSelections((current) => ({ ...current, [proposalId]: optionId }))
                }}
                proposal={selectedDirectorProposal}
                selectedOptionId={
                  selectedDirectorProposal
                    ? directorSelections[selectedDirectorProposal.proposalId]
                    : undefined
                }
                targetLabel={`“${selectedNode.data.projection.label}”`}
              />
            </>
          ) : (
            <div className="film-canvas-director__unavailable">
              <AlertTriangle size={18} />
              <div>
                <strong>暂不能从这个节点发起 Director 审查</strong>
                <p>
                  当前 Scene 尚未通过 ShotSpec 建立到 ScriptScene 的明确 lineage。
                  请先在现有故事与分镜流程中建立关联；画布不会用名称或数组位置猜测目标。
                </p>
              </div>
            </div>
          )}
        </Surface>
      ) : null}

      <ImpactConfirmModal
        cancelLabel="先不处理"
        confirmLabel={
          directorAction?.type === 'EXECUTE'
            ? '采用并生成修改版'
            : directorAction?.decision === 'APPROVE'
              ? '确认使用这版'
              : directorAction?.decision === 'ROLLBACK'
                ? '恢复原来的版本'
                : '不采用'
        }
        confirmVariant={
          directorAction?.type === 'DECIDE' && directorAction.decision === 'REJECT'
            ? 'danger'
            : 'primary'
        }
        confirmDisabled={
          directorApprovalRequiresOverride(directorAction)
          && directorApprovalOverrideReason.trim().length < 8
        }
        items={directorAction ? [
          {
            icon: <GitMerge size={16} />,
            title: directorAction.type === 'EXECUTE' ? '保留原稿，另存修改版' : '保存这次选择',
            detail: directorAction.type === 'EXECUTE'
              ? '原来的剧本不会变；这次调整会另存为一个新版本。'
              : '系统会记下你是采用、不采用，还是恢复原稿，之后可以查到。',
          },
          {
            icon: <AlertTriangle size={16} />,
            title: directorAction.proposal.affectedObjects.length
              ? `还需检查 ${directorAction.proposal.affectedObjects.length} 项相关内容`
              : '没有其他内容受影响',
            detail: directorAction.proposal.affectedObjects.length
              ? '与这次修改有关的镜头和时间安排会标记为待检查，不会自动重做。'
              : '目前没有关联的镜头或成片素材，不需要重新生成。',
          },
          {
            icon: <LockKeyhole size={16} />,
            title: directorAction.proposal.preservedObjects.length
              ? `其他 ${directorAction.proposal.preservedObjects.length} 项内容保持不变`
              : '没有需要额外保护的内容',
            detail: directorAction.proposal.preservedObjects.length
              ? '这次修改范围之外的已确认内容不会改变。'
              : '当前没有范围外的已确认内容。',
          },
          ...(directorApprovalRequiresOverride(directorAction)
            ? [{
                icon: <AlertTriangle size={16} />,
                title: '需要说明为什么仍要采用',
                detail: '当前修改可能影响时长。请确认风险可以接受，并写下后续检查方式。',
              }]
            : []),
        ] : []}
        loading={directorBusy}
        onClose={() => {
          if (!directorBusy) {
            setDirectorAction(null)
            setDirectorApprovalOverrideReason('')
          }
        }}
        onConfirm={() => void confirmDirectorAction()}
        open={directorAction !== null}
        subtitle="这次只改你刚才看到的内容，不会生成视频、配音或音乐。"
        title={
          directorAction?.type === 'EXECUTE'
            ? '采用这个修改方案？'
            : directorAction?.decision === 'APPROVE'
              ? '确认使用这个修改版？'
              : directorAction?.decision === 'ROLLBACK'
                ? '恢复到修改前？'
                : '不采用这条建议？'
        }
      >
        {directorApprovalRequiresOverride(directorAction) ? (
          <label className="director-approval-override">
            <strong>为什么仍要采用？</strong>
            <span>系统已按当前时长问题填写了一版，你可以直接修改。</span>
            <textarea
              autoFocus
              maxLength={1000}
              onChange={(event) => setDirectorApprovalOverrideReason(event.target.value)}
              placeholder="例如：对白略超预算，但下一场留有节奏余量；批准后仍会在配音前复核。"
              rows={3}
              value={directorApprovalOverrideReason}
            />
            <small>
              至少 8 个字 · 当前 {directorApprovalOverrideReason.trim().length} 字
            </small>
          </label>
        ) : null}
      </ImpactConfirmModal>
      <Modal
        className="modal--character-revision"
        description="先检查人物关系、分集大纲与剧本的影响，再创建同步修改版；当前正式版本不会被覆盖。"
        footer={(
          <>
            <Button
              disabled={canvasActionBusy}
              onClick={() => {
                setCharacterEditor(null)
                setCharacterReview(null)
                setCanvasActionError(null)
              }}
              variant="secondary"
            >
              取消
            </Button>
            {characterReview ? (
              <>
                <Button
                  disabled={canvasActionBusy}
                  onClick={() => {
                    setCharacterReview(null)
                    setCanvasActionError(null)
                  }}
                  variant="secondary"
                >
                  返回修改
                </Button>
                <Button disabled={canvasActionBusy} onClick={() => void confirmCharacterChanges()}>
                  {canvasActionBusy
                    ? <LoaderCircle className="spin" size={16} />
                    : <Check size={16} />}
                  确认修改并同步
                </Button>
              </>
            ) : (
              <Button
                disabled={
                  canvasActionBusy
                  || !characterEditor?.draft.name.trim()
                  || !characterEditor.draft.role.trim()
                }
                onClick={() => void reviewCharacterChanges()}
              >
                {canvasActionBusy
                  ? <LoaderCircle className="spin" size={16} />
                  : <ScanSearch size={16} />}
                查看影响范围
              </Button>
            )}
          </>
        )}
        onClose={() => {
          if (!canvasActionBusy) {
            setCharacterEditor(null)
            setCharacterReview(null)
            setCanvasActionError(null)
          }
        }}
        open={characterEditor !== null}
        title={characterEditor ? `修改角色设定 · ${characterEditor.draft.name}` : '修改角色设定'}
      >
        {canvasActionError ? (
          <div className="character-revision-error" role="alert">
            <AlertTriangle size={17} />
            <div><strong>未能完成本次操作</strong><p>{canvasActionError}</p></div>
          </div>
        ) : null}
        {characterEditor ? (
          <div className="character-revision-form">
            <div className="character-revision-form__grid">
              {([
                ['name', '姓名'],
                ['role', '角色定位'],
              ] as const).map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input
                    disabled={Boolean(characterReview)}
                    onChange={(event) => setCharacterEditor((current) => current
                      ? { ...current, draft: { ...current.draft, [field]: event.target.value } }
                      : current)}
                    value={characterEditor.draft[field]}
                  />
                </label>
              ))}
              <label className="character-revision-form__wide">
                性格关键词（用顿号分隔）
                <input
                  disabled={Boolean(characterReview)}
                  onChange={(event) => setCharacterEditor((current) => current
                    ? { ...current, draft: { ...current.draft, personality: event.target.value } }
                    : current)}
                  value={characterEditor.draft.personality}
                />
              </label>
              {([
                ['dramaticFunction', '剧情功能'],
                ['desire', '欲望'],
                ['fear', '恐惧'],
                ['secret', '秘密'],
                ['visualNotes', '视觉特征'],
              ] as const).map(([field, label]) => (
                <label className="character-revision-form__wide" key={field}>
                  {label}
                  <textarea
                    disabled={Boolean(characterReview)}
                    onChange={(event) => setCharacterEditor((current) => current
                      ? { ...current, draft: { ...current.draft, [field]: event.target.value } }
                      : current)}
                    rows={field === 'visualNotes' ? 3 : 2}
                    value={characterEditor.draft[field]}
                  />
                </label>
              ))}
            </div>
            {characterReview ? (
              <section className={`character-revision-review is-${characterReview.review.verdict.toLowerCase()}`}>
                <header>
                  <div>
                    <span>{characterReview.review.verdict === 'CONFLICT' ? '发现逻辑冲突' : '影响检查完成'}</span>
                    <h3>{characterReview.review.summary}</h3>
                  </div>
                </header>
                {characterReview.review.issues.length ? (
                  <ul>
                    {characterReview.review.issues.map((issue) => (
                      <li data-severity={issue.severity.toLowerCase()} key={`${issue.code}:${issue.field ?? ''}`}>
                        <strong>{issue.severity === 'BLOCKER' ? '冲突' : issue.severity === 'WARNING' ? '提醒' : '信息'}</strong>
                        <div><p>{issue.message}</p><small>{issue.suggestion}</small></div>
                      </li>
                    ))}
                  </ul>
                ) : <p>未发现需要阻止修改的故事逻辑问题。</p>}
                <div className="character-revision-impact">
                  <div><span>人物关系</span><strong>{characterReview.affected.relationshipCount} 条</strong></div>
                  <div><span>分集大纲</span><strong>{characterReview.affected.outlineCount} 版</strong></div>
                  <div><span>剧本</span><strong>{characterReview.affected.scriptCount} 版</strong></div>
                </div>
                <p>确认后创建新的故事设定和关系草稿；旧版本与已锁定视觉仍然保留。</p>
              </section>
            ) : null}
          </div>
        ) : null}
      </Modal>
      <Modal
        className="modal--relationship-revision"
        description="选择需要修改的关系并说明变化意图。系统先计算影响范围，确认后才创建新关系版本。"
        footer={(
          <>
            <Button
              disabled={canvasActionBusy}
              onClick={() => {
                setRelationshipEditor(null)
                setRelationshipImpact(null)
                setCanvasActionError(null)
              }}
              variant="secondary"
            >
              取消
            </Button>
            {relationshipImpact ? (
              <>
                <Button
                  disabled={canvasActionBusy}
                  onClick={() => {
                    setRelationshipImpact(null)
                    setCanvasActionError(null)
                  }}
                  variant="secondary"
                >
                  返回修改
                </Button>
                <Button disabled={canvasActionBusy} onClick={() => void confirmRelationshipRevision()}>
                  {canvasActionBusy
                    ? <LoaderCircle className="spin" size={16} />
                    : <GitBranch size={16} />}
                  创建关系修改版
                </Button>
              </>
            ) : (
              <Button
                disabled={
                  canvasActionBusy
                  || !relationshipEditor?.selectedKeys.length
                  || (relationshipEditor?.intent.trim().length ?? 0) < 6
                }
                onClick={() => void analyzeRelationshipImpact()}
              >
                {canvasActionBusy
                  ? <LoaderCircle className="spin" size={16} />
                  : <ScanSearch size={16} />}
                查看影响范围
              </Button>
            )}
          </>
        )}
        onClose={() => {
          if (!canvasActionBusy) {
            setRelationshipEditor(null)
            setRelationshipImpact(null)
            setCanvasActionError(null)
          }
        }}
        open={relationshipEditor !== null}
        title="修改角色关系"
      >
        {canvasActionError ? (
          <div className="character-revision-error" role="alert">
            <AlertTriangle size={17} />
            <div><strong>未能完成本次操作</strong><p>{canvasActionError}</p></div>
          </div>
        ) : null}
        {relationshipEditor ? (
          <div className="canvas-relationship-editor">
            {!relationshipEditor.relationshipKeys.length ? (
              <div className="canvas-relationship-editor__empty">
                <UsersRound size={18} />
                <p>这个角色当前没有已建档的人物关系。请先在故事工作区建立关系。</p>
              </div>
            ) : (
              <>
                <fieldset disabled={Boolean(relationshipImpact)}>
                  <legend>选择要修改的关系</legend>
                  {relationshipEditor.relationshipKeys.map((relationshipKey) => {
                    const edge = relationshipEditor.graph.graph.edges.find(
                      (item) => item.relationshipKey === relationshipKey,
                    )
                    return (
                      <label key={relationshipKey}>
                        <input
                          checked={relationshipEditor.selectedKeys.includes(relationshipKey)}
                          onChange={(event) => setRelationshipEditor((current) => {
                            if (!current) return current
                            const selectedKeys = event.target.checked
                              ? [...current.selectedKeys, relationshipKey]
                              : current.selectedKeys.filter((item) => item !== relationshipKey)
                            return { ...current, selectedKeys }
                          })}
                          type="checkbox"
                        />
                        <span>
                          <strong>{edge
                            ? `${edge.sourceCharacterKey} ↔ ${edge.targetCharacterKey}`
                            : relationshipKey}</strong>
                          <small>{edge?.surfaceRelationship ?? '已建立关系'}</small>
                        </span>
                      </label>
                    )
                  })}
                </fieldset>
                <label>
                  <span>关系变化意图</span>
                  <textarea
                    disabled={Boolean(relationshipImpact)}
                    maxLength={2000}
                    onChange={(event) => setRelationshipEditor((current) => current
                      ? { ...current, intent: event.target.value }
                      : current)}
                    placeholder="例如：把表面合作改为互相猜疑，但保留共同保护孩子的真实目标。"
                    rows={4}
                    value={relationshipEditor.intent}
                  />
                  <small>至少 6 个字 · 当前 {relationshipEditor.intent.trim().length} 字</small>
                </label>
              </>
            )}
            {relationshipImpact ? (
              <section className="canvas-impact-summary">
                <header><ScanSearch size={18} /><h3>影响范围</h3></header>
                <div className="canvas-impact-summary__metrics">
                  <div><span>分集</span><strong>{relationshipImpact.affected.episodeOrdinals.length}</strong></div>
                  <div><span>剧本</span><strong>{relationshipImpact.affected.scriptVersionIds.length}</strong></div>
                  <div><span>场次</span><strong>{relationshipImpact.affected.scenes.length}</strong></div>
                  <div><span>预计耗时</span><strong>{relationshipImpact.estimate.seconds} 秒</strong></div>
                </div>
                <p>
                  {relationshipImpact.touchesApproved
                    ? '会触及已批准内容；确认后相关版本进入待重审，不会自动覆盖。'
                    : '不会覆盖已批准内容；系统会创建独立关系修改版。'}
                </p>
              </section>
            ) : null}
          </div>
        ) : null}
      </Modal>
      <Modal
        className="modal--shot-planning"
        description="保存会写回正式镜头规格，并让你选择只标记下游重审，或立即重生成该镜头的下游内容。"
        footer={(
          <>
            <Button
              disabled={canvasActionBusy}
              onClick={() => {
                setShotEditor(null)
                setCanvasActionError(null)
              }}
              variant="secondary"
            >
              取消
            </Button>
            <Button
              disabled={canvasActionBusy || !shotEditor?.description.trim()}
              onClick={() => void submitShotRevision()}
            >
              {canvasActionBusy
                ? <LoaderCircle className="spin" size={16} />
                : <Check size={16} />}
              保存镜头规划
            </Button>
          </>
        )}
        onClose={() => {
          if (!canvasActionBusy) {
            setShotEditor(null)
            setCanvasActionError(null)
          }
        }}
        open={shotEditor !== null}
        title="修改镜头／分镜规划"
      >
        {canvasActionError ? (
          <div className="character-revision-error" role="alert">
            <AlertTriangle size={17} />
            <div><strong>未能完成本次操作</strong><p>{canvasActionError}</p></div>
          </div>
        ) : null}
        {shotEditor ? (
          <div className="canvas-shot-editor">
            <label className="canvas-shot-editor__wide">
              <span>镜头内容</span>
              <textarea
                disabled={canvasActionBusy}
                maxLength={4000}
                onChange={(event) => setShotEditor((current) => current
                  ? { ...current, description: event.target.value }
                  : current)}
                rows={4}
                value={shotEditor.description}
              />
            </label>
            <label className="canvas-shot-editor__wide">
              <span>对白／画外音</span>
              <textarea
                disabled={canvasActionBusy}
                maxLength={4000}
                onChange={(event) => setShotEditor((current) => current
                  ? { ...current, dialogue: event.target.value }
                  : current)}
                rows={3}
                value={shotEditor.dialogue}
              />
            </label>
            <div className="canvas-shot-editor__grid">
              <label>
                <span>景别</span>
                <SelectControl
                  disabled={canvasActionBusy}
                  onChange={(event) => setShotEditor((current) => current
                    ? { ...current, shotSize: event.target.value as ShotEditDraft['shotSize'] }
                    : current)}
                  value={shotEditor.shotSize}
                >
                  <option value="WS">全景</option>
                  <option value="MS">中景</option>
                  <option value="MCU">中近景</option>
                  <option value="CU">特写</option>
                </SelectControl>
              </label>
              <label>
                <span>运镜</span>
                <SelectControl
                  disabled={canvasActionBusy}
                  onChange={(event) => setShotEditor((current) => current
                    ? {
                        ...current,
                        cameraMovement: event.target.value as ShotEditDraft['cameraMovement'],
                      }
                    : current)}
                  value={shotEditor.cameraMovement}
                >
                  <option value="STATIC">固定</option>
                  <option value="PAN">摇镜</option>
                  <option value="DOLLY_IN">推镜</option>
                  <option value="TRACK">跟拍</option>
                  <option value="HANDHELD">手持</option>
                </SelectControl>
              </label>
            </div>
            <fieldset className="canvas-shot-editor__downstream">
              <legend>保存后如何处理下游内容</legend>
              <label>
                <input
                  checked={shotEditor.downstreamAction === 'REVIEW'}
                  name="shot-downstream-action"
                  onChange={() => setShotEditor((current) => current
                    ? { ...current, downstreamAction: 'REVIEW' }
                    : current)}
                  type="radio"
                />
                <span>
                  <strong>标记为待重审</strong>
                  <small>保留现有关键帧、镜头版本和节奏样片，等待人工决定。</small>
                </span>
              </label>
              <label>
                <input
                  checked={shotEditor.downstreamAction === 'REGENERATE'}
                  name="shot-downstream-action"
                  onChange={() => setShotEditor((current) => current
                    ? { ...current, downstreamAction: 'REGENERATE' }
                    : current)}
                  type="radio"
                />
                <span>
                  <strong>重生成下游内容</strong>
                  <small>保存后立即重生成该镜头，并自动刷新关联节奏样片。</small>
                </span>
              </label>
            </fieldset>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
