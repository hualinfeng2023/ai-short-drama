import { useState } from 'react'
import { AlertTriangle, ArrowRight, CalendarDays, Clapperboard, Clock3, LoaderCircle, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { calculateProgress } from '../data/demo'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import { Button, Modal, PageHeader, ProgressBar, StatusBadge } from '../components/ui'
import type { ProjectSummary } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'

export function ProjectsPage() {
  const { apiStatus, deleteProject, project, projectSummaries, resetDemo } = useStudio()
  const progress = calculateProgress(project.shots)
  const featureImage = project.shots.find((shot) => shot.currentImageUrl)?.currentImageUrl
  const { notify } = useToast()
  const navigate = useNavigate()
  const [pendingDelete, setPendingDelete] = useState<ProjectSummary | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  async function confirmDelete() {
    if (!pendingDelete || deleting) return
    setDeleting(true)
    setDeleteError(null)
    try {
      const name = pendingDelete.name
      await deleteProject(pendingDelete.id)
      setPendingDelete(null)
      notify(`“${name}”已删除。`)
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : '删除项目失败，请稍后重试。')
    } finally {
      setDeleting(false)
    }
  }

  const updatedLabel = (value: string) => {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return '刚刚'
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date)
  }

  return (
    <div className="page page--projects">
      <PageHeader
        eyebrow="创作空间"
        title="项目"
        description="继续上次创作，或从一句故事想法开始新的验证样片。"
        actions={
          <Link className="button button--primary button--md" to="/projects/new">
            <Plus size={17} />
            新建项目
          </Link>
        }
      />

      <section className="project-feature" aria-labelledby="recent-project-title">
        <div className="project-feature__visual">
          {featureImage ? <img alt={`${project.name} 当前镜头`} className="project-feature__image" src={featureImage} /> : null}
          <span className="frame-label">演示生成 · 混合小样</span>
          <div className="frame-copy">
            <small>场景 02 · 镜头 S05</small>
            <strong>把决定变成一个动作。</strong>
            <span>{localizeDisplayText(project.style)} / {project.aspectRatio} / {project.targetDurationSec} 秒</span>
          </div>
        </div>
        <div className="project-feature__body">
          <div className="project-feature__heading">
            <div>
              <p className="eyebrow">最近编辑</p>
              <h2 id="recent-project-title">{project.name}</h2>
            </div>
            <StatusBadge status={project.status} />
          </div>
          <p>{project.idea}</p>
          <div className="project-meta-row">
            <span><Clapperboard size={15} />第 1 集 · 验证样片</span>
            <span><Clock3 size={15} />{project.targetDurationSec} 秒 · {project.aspectRatio}</span>
            <span><CalendarDays size={15} />今天 19:42 更新</span>
          </div>
          <ProgressBar label="镜头制作进度" value={progress} />
          <div className="project-feature__actions">
            <Link
              className="button button--primary button--md"
              to={`/projects/${project.id}/episodes/${project.episodeId}`}
            >
              继续创作 <ArrowRight size={16} />
            </Link>
            <Link
              className="button button--secondary button--md"
              to={`/projects/${project.id}/episodes/${project.episodeId}/preview`}
            >
              查看完整小样
            </Link>
          </div>
        </div>
      </section>

      <section className="project-library" aria-labelledby="project-library-title">
        <div className="section-heading">
          <h2 id="project-library-title">全部项目</h2>
          {apiStatus === 'mock_fallback' ? <Button onClick={() => { resetDemo(); notify('已恢复内置演示项目。') }} size="sm" variant="ghost">
            <RotateCcw size={15} /> 恢复演示项目
          </Button> : null}
        </div>
        <div className="project-table" role="table" aria-label="项目列表">
          <div className="project-table__head" role="row">
            <span>项目</span><span>状态</span><span>规格</span><span>进度</span><span>更新时间</span><span>操作</span>
          </div>
          {projectSummaries.map((item) => {
            const itemProgress = item.id === project.id ? progress : 0
            const itemHref = item.id === project.id
              ? `/projects/${item.id}/episodes/${project.episodeId}`
              : `/projects/${item.id}`
            return <div
              className="project-table__row project-table__row--clickable"
              key={item.id}
              onClick={() => navigate(itemHref)}
              role="row"
              title={`打开${item.name}`}
            >
              <span className="project-table__name">
                <span className="project-monogram">{item.name.slice(0, 1)}</span>
                <span><strong>{item.name}</strong><small>{localizeDisplayText(item.genre)}</small></span>
              </span>
              <span><StatusBadge status={item.status} /></span>
              <span>{item.episodeCount} 集 · {item.sceneCount} 场 · {item.shotCount} 镜头</span>
              <span><ProgressBar value={itemProgress} /></span>
              <span>{updatedLabel(item.updatedAt)}</span>
              <span className="project-table__actions">
                <button
                  aria-label={`删除${item.name}`}
                  className="project-table__delete"
                  disabled={item.id === project.id}
                  onClick={(event) => {
                    event.stopPropagation()
                    setDeleteError(null)
                    setPendingDelete(item)
                  }}
                  title={item.id === project.id ? '当前正在编辑的项目不能删除' : `删除${item.name}`}
                  type="button"
                >
                  <Trash2 size={16} />
                </button>
                <Link aria-label={`打开${item.name}`} onClick={(event) => event.stopPropagation()} to={itemHref}>
                  <ArrowRight size={17} />
                </Link>
              </span>
            </div>
          })}
        </div>
      </section>

      <Modal
        className="modal--project-delete"
        description="此操作不可撤销，项目关联的项目简报、任务、剧本、素材和时间线都会一并删除。"
        footer={<>
          <Button disabled={deleting} onClick={() => setPendingDelete(null)} variant="secondary">取消</Button>
          <Button disabled={deleting} onClick={() => void confirmDelete()} variant="danger">
            {deleting ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}
            {deleting ? '正在删除…' : '确认删除'}
          </Button>
        </>}
        onClose={() => {
          if (!deleting) setPendingDelete(null)
        }}
        open={pendingDelete !== null}
        title="删除项目？"
      >
        <div className="project-delete-confirmation">
          <span><AlertTriangle size={20} /></span>
          <div><strong>{pendingDelete?.name}</strong><p>删除后无法恢复，也不会保留项目版本记录。</p></div>
        </div>
        {deleteError ? <p className="project-delete-dialog-error" role="alert">{deleteError}</p> : null}
      </Modal>
    </div>
  )
}
