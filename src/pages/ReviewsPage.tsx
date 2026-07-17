import { ArrowRight, CheckCircle2, Film, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router'
import { EmptyState, PageHeader, StatusBadge } from '../components/ui'
import { useStudio } from '../store/StudioContext'

export function ReviewsPage() {
  const { project } = useStudio()
  const pending = project.shots.filter((shot) => shot.status === 'PENDING_REVIEW')
  return <div className="page page--reviews"><PageHeader eyebrow="创作者审核" title="审核中心" description="首版仅支持单用户批准或请求修改；需要专业判断的高风险内容会直接阻断。" />{pending.length === 0 ? <EmptyState title="没有待审核内容" description="所有候选版本都已处理。" action={<Link className="button button--secondary button--md" to={`/projects/${project.id}/episodes/${project.episodeId}`}>返回工作台</Link>} /> : <div className="review-grid">{pending.map((shot) => {
    const frameUrl = shot.candidateImageUrl ?? shot.currentImageUrl
    return <article key={shot.id}><div className="review-frame">{frameUrl ? <img alt={`${shot.code} · ${shot.title}`} src={frameUrl} /> : <span className="review-frame__empty"><Film size={22} />画面候选仍在生成</span>}<span className="review-frame__label">{shot.candidateImageUrl ? '候选画面' : '当前画面'} · {shot.code}</span></div><div><div className="section-heading"><div><p className="eyebrow">{shot.code} · 候选第 {shot.candidateTake} 版</p><h2>{shot.title}</h2></div><StatusBadge status={shot.status} /></div><p>{shot.description}</p><div className="review-checks"><span><CheckCircle2 size={15} />结构一致性通过</span><span><ShieldCheck size={15} />无权利阻断</span></div><Link className="button button--primary button--md" to={`/projects/${project.id}/episodes/${project.episodeId}/scenes/${shot.sceneId}?shot=${shot.id}`}>打开版本比较 <ArrowRight size={16} /></Link></div></article>
  })}</div>}</div>
}
