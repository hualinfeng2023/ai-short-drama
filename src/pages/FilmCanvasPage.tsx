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
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import { AlertTriangle, ArrowUpRight, GitMerge, LockKeyhole, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router'
import {
  ApiError,
  createDirectorReviewProposal,
  decideDirectorReviewProposal,
  executeDirectorReviewProposal,
  fetchCanvasProjection,
  fetchDirectorReviewProposals,
  type CanvasProjection,
  type CanvasReference,
  type DirectorReviewProposal,
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
  type FilmCanvasNode,
} from '../canvas/filmCanvasProjection'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { DirectorFailureInspector } from '../components/director-review/DirectorFailureInspector'
import { DirectorGenerationHistory } from '../components/director-review/DirectorGenerationHistory'
import {
  DirectorReviewCard,
  directorApprovalRequiresOverride,
  type DirectorReviewAction,
} from '../components/director-review/DirectorReviewCard'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { Button, PageHeader, StatusBadge, Surface, getStatusLabel } from '../components/ui'
import { useToast } from '../store/ToastContext'

const VIEW_STATE_KEY_PREFIX = 'film-canvas-view-state-v1'
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }
const PROJECTION_REFRESH_INTERVAL_MS = 3_000

const objectTypeLabels: Record<string, string> = {
  Project: '项目',
  Story: '故事',
  Script: '剧本',
  Beat: '叙事节拍',
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
        <span>{objectTypeLabels[item.ref.type] ?? item.ref.type}</span>
        <StatusBadge status={item.approvalStatus} />
      </header>
      <strong title={item.label}>{item.label}</strong>
      <footer>
        <span>{getStatusLabel(item.canonicalStatus)}</span>
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

const nodeTypes = { filmObject: FilmObjectNode }

export function FilmCanvasPage() {
  const { projectId } = useParams()
  const { notify } = useToast()
  const [projection, setProjection] = useState<CanvasProjection | null>(null)
  const [nodes, setNodes] = useState<FilmCanvasNode[]>([])
  const [edges, setEdges] = useState<FilmCanvasEdge[]>([])
  const [viewport, setViewport] = useState<Viewport>(DEFAULT_VIEWPORT)
  const [hasSavedViewport, setHasSavedViewport] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [directorProposals, setDirectorProposals] = useState<DirectorReviewProposal[]>([])
  const [directorSelections, setDirectorSelections] = useState<Record<string, string>>({})
  const [directorHistory, setDirectorHistory] = useState<DirectorGenerationRecord[]>([])
  const [directorBusy, setDirectorBusy] = useState(false)
  const [directorError, setDirectorError] = useState<string | null>(null)
  const [directorAction, setDirectorAction] = useState<DirectorReviewAction | null>(null)
  const [directorApprovalOverrideReason, setDirectorApprovalOverrideReason] = useState('')
  const projectionSignatureRef = useRef<string | null>(null)

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
        .filter((node) => node.selected)
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

  const onNodesChange = useCallback((changes: NodeChange<FilmCanvasNode>[]) => {
    setNodes((current) => applyNodeChanges(changes, current))
  }, [])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.selected) ?? null,
    [nodes],
  )

  const selectedReferences = useMemo<CanvasReference[]>(
    () => nodes.filter((node) => node.selected).map((node) => node.data.projection.ref),
    [nodes],
  )

  const directorTarget = useMemo(
    () => projection && selectedNode
      ? resolveDirectorReviewTarget(projection, selectedNode.data.projection.ref)
      : null,
    [projection, selectedNode],
  )

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

  async function reviewSelectedObject() {
    if (!projectId || !projection || !directorTarget || directorBusy) return
    setDirectorBusy(true)
    setDirectorError(null)
    try {
      const proposal = await createDirectorReviewProposal(projectId, {
        expectedVersion: projection.projectLockVersion,
        targetType: directorTarget.targetType,
        targetId: directorTarget.targetId,
        issueTypes: ['STORY_LOGIC', 'CHARACTER_MOTIVATION', 'AI_DIALOGUE', 'PACING'],
        instruction: '检查故事因果、人物当下目标、对白 AI 味和场景节奏。',
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
          description="画布只读取 Film IR，不保存或复制项目业务数据。"
          actions={<Button onClick={() => window.location.reload()} variant="secondary"><RefreshCw size={16} />重试</Button>}
        />
        <div className="brief-save-message brief-save-message--error" role="alert">
          {error ?? '画布投影不可用'}
        </div>
      </div>
    )
  }

  return (
    <div className="page page--film-canvas">
      <PageHeader
        eyebrow="架构验证版 · 只读投影"
        title="Agentic Film Canvas"
        description="同一组 Project、Story、Scene、Shot 与 Timeline 对象的空间视图。拖动只改变本地布局；业务修改继续进入现有详情页并走同一领域 API。"
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
      <section className="film-canvas-summary" aria-label="投影摘要">
        <div><span>对象</span><strong>{nodes.length}</strong></div>
        <div><span>依赖</span><strong>{edges.length}</strong></div>
        <div><span>项目版本</span><strong>{projection.projectLockVersion}</strong></div>
        <div><span>选择</span><strong>{selectedReferences.length}</strong></div>
      </section>
      <div className="film-canvas-workspace">
        <Surface className="film-canvas-stage" padding="none">
          <ReactFlow<FilmCanvasNode, FilmCanvasEdge>
            edges={edges}
            fitView={!hasSavedViewport}
            maxZoom={1.8}
            minZoom={0.25}
            nodeTypes={nodeTypes}
            nodes={nodes}
            nodesConnectable={false}
            onNodesChange={onNodesChange}
            onViewportChange={setViewport}
            panOnScroll
            proOptions={{ hideAttribution: false }}
            selectionOnDrag
            viewport={viewport}
          >
            <Background color="var(--color-border)" gap={24} variant={BackgroundVariant.Dots} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </Surface>
        <Surface as="aside" className="film-canvas-inspector">
          {selectedNode ? (
            <>
              <div>
                <p className="eyebrow">现有详情视图</p>
                <h2>{selectedNode.data.projection.label}</h2>
                <p>{objectTypeLabels[selectedNode.data.projection.ref.type] ?? selectedNode.data.projection.ref.type}</p>
              </div>
              <dl>
                <div><dt>稳定编号</dt><dd>{selectedNode.data.projection.ref.id}</dd></div>
                <div><dt>版本编号</dt><dd>{selectedNode.data.projection.ref.versionId ?? '未单独版本化'}</dd></div>
                <div><dt>事实类型</dt><dd>{selectedNode.data.projection.canonicalKind}</dd></div>
                <div><dt>事实状态</dt><dd>{getStatusLabel(selectedNode.data.projection.canonicalStatus)}</dd></div>
                <div><dt>审批状态</dt><dd>{getStatusLabel(selectedNode.data.projection.approvalStatus)}</dd></div>
              </dl>
              <Link
                className="button button--primary button--md"
                to={selectedNode.data.projection.detailRoute}
              >
                打开详情页<ArrowUpRight size={16} />
              </Link>
              <small>详情页写入继续经过现有 Command / Domain API；画布不直接修改领域对象。</small>
            </>
          ) : (
            <div className="film-canvas-inspector__empty">
              <p className="eyebrow">Inspector</p>
              <h2>选择一个对象</h2>
              <p>查看稳定编号、版本、审批状态和现有详情入口。画布不会保存这些业务字段。</p>
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
                onReview={() => void reviewSelectedObject()}
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
    </div>
  )
}
