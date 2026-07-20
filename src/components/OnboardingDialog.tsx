import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Bell,
  ChevronLeft,
  Clapperboard,
  CloudOff,
  Film,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'

const ONBOARDING_KEY = 'studio-onboarding-v1'

export function shouldShowOnboarding(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_KEY) !== 'done'
  } catch {
    return false
  }
}

export function markOnboardingDone() {
  try {
    window.localStorage.setItem(ONBOARDING_KEY, 'done')
  } catch {
    // 存储不可用时仅在本次会话内生效。
  }
}

const STEPS = [
  {
    title: '先建立一个心智模型',
    lead: '从故事想法到可播放小样，只需要理解五层结构：',
    visual: (
      <div className="onboarding-model" aria-hidden="true">
        {['项目', '剧集', '场景', '镜头', '版本'].map((label, index) => (
          <span className="onboarding-model__node" key={label} style={{ animationDelay: `${index * 90}ms` }}>
            {label}
            {index < 4 ? <i /> : null}
          </span>
        ))}
      </div>
    ),
    body: '镜头是最小创作单元；每个镜头可以生成多个版本，当前版本永远可播放，候选版本复核后才会应用——大胆生成，不会搞砸。',
  },
  {
    title: '两条工作流，各管一段',
    lead: '产品里有两种制作方式，入口上都有明确标注：',
    visual: (
      <div className="onboarding-flows" aria-hidden="true">
        <div>
          <span className="onboarding-flows__icon onboarding-flows__icon--ok"><Film size={17} /></span>
          <strong>经典镜头工作流</strong>
          <small>剧集 → 场景 → 镜头 → 生成 → 复核<br />浏览器演示模式即可完整体验</small>
        </div>
        <div>
          <span className="onboarding-flows__icon onboarding-flows__icon--lock"><CloudOff size={17} /></span>
          <strong>五阶段制作流</strong>
          <small>简报 → 故事 → 前期 → 分镜 → 制作<br />阶段条带锁的项目，连接服务端后解锁</small>
        </div>
      </div>
    ),
    body: '顶栏「本地模式」徽章随时可以告诉你当前处于哪种环境，悬停有详细说明。',
  },
  {
    title: '三个高频操作',
    lead: '掌握这三件事，就可以开始创作了：',
    visual: (
      <ul className="onboarding-tips">
        <li><Sparkles size={15} /><span><strong>生成新版本</strong>镜头工作台右下角的蓝色按钮，成本与影响会先讲清楚</span></li>
        <li><Bell size={15} /><span><strong>任务有回音</strong>生成完成、失败或取消，右上角都会实时通知</span></li>
        <li><ShieldCheck size={15} /><span><strong>批量复核</strong>审核中心可以按场景分组、筛选差异并批量批准</span></li>
        <li><ChevronLeft size={15} /><span><strong>键盘切换</strong>镜头工作台里用 ← / → 在镜头之间快速跳转</span></li>
      </ul>
    ),
    body: '这份引导可以在右上角账户菜单里随时重新打开。',
  },
]

export function OnboardingDialog({
  open,
  onFinish,
}: {
  open: boolean
  onFinish: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [step, setStep] = useState(0)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
    if (open) setStep(0)
  }, [open])

  const last = step === STEPS.length - 1
  const current = STEPS[step]

  return (
    <dialog
      aria-label="新手引导"
      className="onboarding"
      ref={ref}
      onCancel={(event) => {
        event.preventDefault()
        onFinish()
      }}
      onClose={onFinish}
    >
      <header className="onboarding__header">
        <span className="onboarding__brand"><Clapperboard size={15} />快速上手</span>
        <button className="onboarding__skip" onClick={onFinish} type="button">跳过引导</button>
        <button aria-label="关闭引导" className="onboarding__close" onClick={onFinish} type="button"><X size={16} /></button>
      </header>
      <div className="onboarding__stage" key={step}>
        <p className="eyebrow">第 {step + 1} 步 · 共 {STEPS.length} 步</p>
        <h2>{current.title}</h2>
        <p className="onboarding__lead">{current.lead}</p>
        {current.visual}
        <p className="onboarding__body">{current.body}</p>
      </div>
      <footer className="onboarding__footer">
        <div className="onboarding__dots" aria-hidden="true">
          {STEPS.map((item, index) => (
            <button
              aria-label={`跳到第 ${index + 1} 步`}
              className={index === step ? 'active' : ''}
              key={item.title}
              onClick={() => setStep(index)}
              type="button"
            />
          ))}
        </div>
        <div className="onboarding__actions">
          {step > 0 ? (
            <button className="button button--ghost button--sm" onClick={() => setStep((value) => value - 1)} type="button">
              上一步
            </button>
          ) : (
            <button className="button button--ghost button--sm" onClick={onFinish} type="button">跳过</button>
          )}
          <button
            className="button button--primary button--sm"
            onClick={() => (last ? onFinish() : setStep((value) => value + 1))}
            type="button"
          >
            {last ? '开始创作' : '下一步'}
            <ArrowRight size={14} />
          </button>
        </div>
      </footer>
    </dialog>
  )
}
