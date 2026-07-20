import { useMemo, useState } from 'react'
import { ArrowRight, Check, CheckCircle2, Film, Layers3, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { Link } from 'react-router'
import { Button, EmptyState, IconButton, PageHeader, StatusBadge, Tab, TabList, Toolbar } from '../components/ui'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'

type FilterId = 'all' | 'flagged' | 'clear'

const FILTERS: Array<{ id: FilterId; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'flagged', label: '系统标记差异' },
  { id: 'clear', label: '未标记差异' },
]

export function ReviewsPage() {
  const { project, applyCandidateTake } = useStudio()
  const { notify } = useToast()
  const [filter, setFilter] = useState<FilterId>('all')
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())

  const pending = project.shots.filter((shot) => shot.status === 'PENDING_REVIEW')
  const flaggedCount = pending.filter((shot) => shot.candidateIdentityStatus === 'REVIEW_REQUIRED').length
  const filtered = useMemo(() => pending.filter((shot) => {
    if (filter === 'flagged') return shot.candidateIdentityStatus === 'REVIEW_REQUIRED'
    if (filter === 'clear') return shot.candidateIdentityStatus !== 'REVIEW_REQUIRED'
    return true
  }), [pending, filter])
  const groups = project.scenes
    .map((scene) => ({ scene, shots: filtered.filter((shot) => shot.sceneId === scene.id) }))
    .filter((group) => group.shots.length > 0)

  const toggleSelect = (shotId: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(shotId)) next.delete(shotId)
      else next.add(shotId)
      return next
    })
  }
  const clearSelection = () => setSelected(new Set())
  const approveOne = (shotId: string, code: string) => {
    applyCandidateTake(shotId)
    notify(`${code} 候选版本已批准，成为当前版本。`)
    setSelected((current) => {
      const next = new Set(current)
      next.delete(shotId)
      return next
    })
  }
  const approveSelected = () => {
    const ids = [...selected].filter((id) => pending.some((shot) => shot.id === id))
    if (ids.length === 0) return
    ids.forEach((id) => applyCandidateTake(id))
    notify(`已批量批准 ${ids.length} 个候选版本。`)
    clearSelection()
  }

  if (pending.length === 0) {
    return (
      <div className="page page--reviews">
        <PageHeader eyebrow="创作者审核" title="审核中心" description="首版仅支持单用户批准或请求修改；需要专业判断的高风险内容会直接阻断。" />
        <EmptyState
          title="没有待审核内容"
          description="所有候选版本都已处理。新生成的候选版本会自动进入这个队列。"
          action={<Link className="button button--secondary button--md" to={`/projects/${project.id}/episodes/${project.episodeId}`}>返回工作台</Link>}
        />
      </div>
    )
  }

  return (
    <div className="page page--reviews">
      <PageHeader
        eyebrow="创作者审核"
        title="审核中心"
        description={`${pending.length} 个镜头待审核${flaggedCount > 0 ? ` · ${flaggedCount} 个被系统标记差异` : ''}，按场景分组处理。`}
      />

      <Toolbar className="review-queue-bar" label="审核队列筛选">
        <TabList className="review-filter" aria-label="按差异标记筛选">
          {FILTERS.map((item) => {
            const count = item.id === 'all' ? pending.length : item.id === 'flagged' ? flaggedCount : pending.length - flaggedCount
            return (
              <Tab
                className={filter === item.id ? 'active' : ''}
                key={item.id}
                onClick={() => { setFilter(item.id); clearSelection() }}
                selected={filter === item.id}
              >
                {item.label}
                <em>{count}</em>
              </Tab>
            )
          })}
        </TabList>
        {selected.size > 0 ? (
          <div className="review-batch" role="status">
            <span>已选 {selected.size} 个镜头</span>
            <Button onClick={approveSelected} size="sm"><Check size={15} />批量批准</Button>
            <IconButton className="review-batch__clear" label="清除选择" onClick={clearSelection} size="sm" variant="ghost"><X size={14} /></IconButton>
          </div>
        ) : null}
      </Toolbar>

      {groups.length === 0 ? (
        <EmptyState
          title="该筛选下没有内容"
          description="切换其他筛选，或返回全部待审核。"
          action={<button className="button button--secondary button--md" onClick={() => setFilter('all')} type="button">查看全部</button>}
        />
      ) : groups.map(({ scene, shots }) => (
        <section className="review-group" key={scene.id}>
          <div className="review-group__heading">
            <Layers3 size={15} />
            <strong>场景 {scene.code} · {scene.title}</strong>
            <span>{shots.length} 个待审核</span>
          </div>
          <div className="review-grid">
            {shots.map((shot) => {
              const frameUrl = shot.candidateImageUrl ?? shot.currentImageUrl
              const flagged = shot.candidateIdentityStatus === 'REVIEW_REQUIRED'
              const checked = selected.has(shot.id)
              return (
                <article className={checked ? 'review-card--selected' : ''} key={shot.id}>
                  <div className="review-frame">
                    {frameUrl ? <img alt={`${shot.code} · ${shot.title}`} src={frameUrl} /> : <span className="review-frame__empty"><Film size={22} />画面候选仍在生成</span>}
                    <span className="review-frame__label">{shot.candidateImageUrl ? '候选画面' : '当前画面'} · {shot.code}</span>
                    <label className="review-frame__check">
                      <input
                        aria-label={`选择 ${shot.code}`}
                        checked={checked}
                        onChange={() => toggleSelect(shot.id)}
                        type="checkbox"
                      />
                      <span aria-hidden="true">{checked ? <Check size={13} /> : null}</span>
                    </label>
                  </div>
                  <div>
                    <div className="section-heading">
                      <div><p className="eyebrow">{shot.code} · 候选第 {shot.candidateTake} 版</p><h2>{shot.title}</h2></div>
                      <StatusBadge status={shot.status} />
                    </div>
                    <p>{shot.description}</p>
                    <div className="review-checks">
                      {flagged
                        ? <span className="warning"><TriangleAlert size={15} />系统标记：角色形象可能存在差异</span>
                        : <span><CheckCircle2 size={15} />未发现明显角色差异</span>}
                      <span><ShieldCheck size={15} />无权利阻断</span>
                    </div>
                    <div className="review-card__actions">
                      <Link className="button button--primary button--md" to={`/projects/${project.id}/episodes/${project.episodeId}/scenes/${shot.sceneId}?shot=${shot.id}`}>打开版本比较 <ArrowRight size={16} /></Link>
                      {shot.candidateTake ? (
                        <Button onClick={() => approveOne(shot.id, shot.code)} size="md" variant="secondary"><Check size={15} />直接批准</Button>
                      ) : null}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}
