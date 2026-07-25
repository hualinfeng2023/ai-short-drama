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
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { fetchCanvasProjection, type CanvasProjection, type CanvasReference } from '../api/client'
import {
  createCanvasViewState,
  parseCanvasViewState,
  projectCanvasGraph,
  type FilmCanvasEdge,
  type FilmCanvasNode,
} from '../canvas/filmCanvasProjection'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { Button, PageHeader, StatusBadge, Surface, getStatusLabel } from '../components/ui'

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
  const [projection, setProjection] = useState<CanvasProjection | null>(null)
  const [nodes, setNodes] = useState<FilmCanvasNode[]>([])
  const [edges, setEdges] = useState<FilmCanvasEdge[]>([])
  const [viewport, setViewport] = useState<Viewport>(DEFAULT_VIEWPORT)
  const [hasSavedViewport, setHasSavedViewport] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const projectionLockRef = useRef<number | null>(null)

  const load = useCallback(async (signal?: AbortSignal, force = false) => {
    if (!projectId) return
    const nextProjection = await fetchCanvasProjection(projectId, signal)
    if (!force && projectionLockRef.current === nextProjection.projectLockVersion) return
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
    projectionLockRef.current = nextProjection.projectLockVersion
  }, [projectId])

  useEffect(() => {
    const controller = new AbortController()
    let hasSnapshot = false
    projectionLockRef.current = null
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
              projectionLockRef.current = null
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
    </div>
  )
}
