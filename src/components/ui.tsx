import { useEffect, useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type ComponentPropsWithoutRef, type ReactNode } from 'react'
import { AlertCircle, AlertTriangle, Check, CheckCircle2, ChevronDown, CircleDot, Info, LoaderCircle, X } from 'lucide-react'

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  children,
  type,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}) {
  return (
    <button className={`button button--${variant} button--${size} ${className}`} type={type ?? 'button'} {...props}>
      {children}
    </button>
  )
}

export function SelectControl({ children, className = '', onChange, ...props }: ComponentPropsWithoutRef<'select'>) {
  const selectRef = useRef<HTMLSelectElement>(null)
  const [selectedLabel, setSelectedLabel] = useState('')

  useLayoutEffect(() => {
    setSelectedLabel(selectRef.current?.selectedOptions[0]?.textContent?.trim() ?? '')
  }, [children, props.value])

  return <span className="select-control">
    <select
      {...props}
      className={className}
      onChange={(event) => {
        setSelectedLabel(event.currentTarget.selectedOptions[0]?.textContent?.trim() ?? '')
        onChange?.(event)
      }}
      ref={selectRef}
      title={props.title ?? selectedLabel}
    >{children}</select>
    <span aria-hidden="true" className="select-control__value" title={selectedLabel}>{selectedLabel}</span>
    <ChevronDown aria-hidden="true" className="select-control__chevron" size={14} strokeWidth={1.8} />
  </span>
}

const statusMeta: Record<string, { label: string; tone: string; icon: ReactNode }> = {
  DRAFT: { label: '草稿', tone: 'neutral', icon: <CircleDot size={12} /> },
  READY: { label: '待开始', tone: 'neutral', icon: <CircleDot size={12} /> },
  QUEUED: { label: '排队中', tone: 'info', icon: <LoaderCircle size={12} /> },
  GENERATING: { label: '生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  GENERATED: { label: '已生成', tone: 'info', icon: <Check size={12} /> },
  PENDING_REVIEW: { label: '待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  APPROVED: { label: '已批准', tone: 'success', icon: <Check size={12} /> },
  FAILED: { label: '失败', tone: 'danger', icon: <X size={12} /> },
  BLOCKED: { label: '已阻断', tone: 'danger', icon: <AlertTriangle size={12} /> },
  RUNNING: { label: '运行中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  RETRY_WAIT: { label: '等待重试', tone: 'warning', icon: <LoaderCircle size={12} /> },
  CANCEL_REQUESTED: { label: '取消中', tone: 'warning', icon: <LoaderCircle size={12} /> },
  SUCCEEDED: { label: '已完成', tone: 'success', icon: <Check size={12} /> },
  CANCELLED: { label: '已取消', tone: 'neutral', icon: <X size={12} /> },
  PENDING: { label: '等待中', tone: 'neutral', icon: <CircleDot size={12} /> },
  PRODUCING: { label: '制作中', tone: 'info', icon: <LoaderCircle size={12} /> },
  PROPOSAL_RUNNING: { label: '方案生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  PROPOSAL_READY: { label: '方案待批准', tone: 'warning', icon: <AlertTriangle size={12} /> },
  STORY_STRUCTURE_RUNNING: { label: '故事结构生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  RELATIONSHIP_READY: { label: '角色关系待确认', tone: 'warning', icon: <AlertTriangle size={12} /> },
  CHARACTER_VISUAL_READY: { label: '角色形象待锁定', tone: 'warning', icon: <AlertTriangle size={12} /> },
  SCRIPT_PACKAGE_RUNNING: { label: '分集大纲与剧本生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  STORY_APPROVED: { label: '故事已批准', tone: 'success', icon: <Check size={12} /> },
  CHARACTER_LOCKED: { label: '角色已锁定', tone: 'success', icon: <Check size={12} /> },
  STORY_PACKAGE_RUNNING: { label: '故事资料生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  SCRIPT_READY: { label: '剧本待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PREPRODUCTION_READY: { label: '前期资产待批准', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PREPRODUCTION_APPROVED: { label: '前期资产已批准', tone: 'success', icon: <Check size={12} /> },
  STORYBOARD_READY: { label: '分镜待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  STORYBOARD_APPROVED: { label: '分镜已批准', tone: 'success', icon: <Check size={12} /> },
  EXPORTING: { label: '导出中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  ARCHIVED: { label: '已归档', tone: 'neutral', icon: <CircleDot size={12} /> },
  ACTIVE: { label: '进行中', tone: 'info', icon: <LoaderCircle size={12} /> },
  CANDIDATES_READY: { label: '候选已就绪', tone: 'success', icon: <Check size={12} /> },
  NOT_GENERATED: { label: '尚未生成', tone: 'neutral', icon: <CircleDot size={12} /> },
  PENDING_SELECTION: { label: '待选择', tone: 'warning', icon: <AlertTriangle size={12} /> },
  TEXT_CHANGED: { label: '文字设定已变化', tone: 'warning', icon: <AlertTriangle size={12} /> },
  RE_REVIEW_REQUIRED: { label: '需要重新审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  GENERATION_FAILED: { label: '生成失败', tone: 'danger', icon: <X size={12} /> },
  DEGRADED: { label: '已降级', tone: 'warning', icon: <AlertTriangle size={12} /> },
  GENERATING_CANDIDATES: { label: '候选生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  IN_PROGRESS: { label: '进行中', tone: 'info', icon: <LoaderCircle size={12} /> },
  LOCKED: { label: '已锁定', tone: 'success', icon: <Check size={12} /> },
  PARSING: { label: '解析中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  PASSED_WITH_DEGRADATION: { label: '降级后通过', tone: 'warning', icon: <AlertTriangle size={12} /> },
  QC_PASSED: { label: '质量检查通过', tone: 'success', icon: <Check size={12} /> },
  READY_FOR_G5: { label: '可进入第 5 阶段', tone: 'success', icon: <Check size={12} /> },
  READY_FOR_REVIEW: { label: '待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  RESTRICTED_DEMO: { label: '仅限演示', tone: 'warning', icon: <AlertTriangle size={12} /> },
  SELECTED: { label: '已选择', tone: 'info', icon: <Check size={12} /> },
  STORYBOARDING: { label: '分镜生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  SYNTHETIC_ALLOWED: { label: '允许合成', tone: 'success', icon: <Check size={12} /> },
  SYNTHETIC_OWNED: { label: '自有合成素材', tone: 'success', icon: <Check size={12} /> },
  UPLOADING: { label: '上传中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  WAITING_FOR_GATE: { label: '等待审批', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PASSED: { label: '角色一致', tone: 'success', icon: <Check size={12} /> },
  REVIEW_REQUIRED: { label: '角色需要复核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  NOT_APPLICABLE: { label: '没有绑定角色', tone: 'neutral', icon: <CircleDot size={12} /> },
  PREVIEW_READY: { label: '小样就绪', tone: 'success', icon: <Check size={12} /> },
  EXPORTED: { label: '已导出', tone: 'success', icon: <Check size={12} /> },
}

export function getStatusLabel(status: string): string {
  return statusMeta[status]?.label ?? status
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const meta = statusMeta[status] ?? statusMeta.DRAFT
  const displayLabel = label ?? meta.label
  return (
    <span className={`status-badge status-badge--${meta.tone}`} data-tone={meta.tone} title={displayLabel}>
      {meta.icon}
      {displayLabel}
    </span>
  )
}

export function Toast({
  message,
  onDismiss,
  tone = 'success',
  className = '',
}: {
  message: string
  onDismiss: () => void
  tone?: 'success' | 'error' | 'info'
  className?: string
}) {
  const title = tone === 'success' ? '操作成功' : tone === 'error' ? '操作失败' : '提示'
  const icon = tone === 'success' ? <CheckCircle2 size={18} /> : tone === 'error' ? <AlertCircle size={18} /> : <Info size={18} />

  return (
    <div
      aria-atomic="true"
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
      className={`toast toast--${tone} ${className}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <span className="toast__icon" aria-hidden="true">{icon}</span>
      <div className="toast__content">
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      <button aria-label="关闭通知" className="toast__close" onClick={onDismiss} type="button">
        <X size={15} />
      </button>
    </div>
  )
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const safeValue = Math.max(0, Math.min(100, value))
  return (
    <div className="progress-wrap">
      {label ? (
        <div className="progress-label">
          <span>{label}</span>
          <strong>{safeValue}%</strong>
        </div>
      ) : null}
      <div
        className="progress-track"
        aria-label={label ?? '进度'}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={safeValue}
        role="progressbar"
      >
        <span style={{ width: `${safeValue}%` }} />
      </div>
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="page-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  )
}

export function Modal({
  open,
  title,
  description,
  className = '',
  onClose,
  children,
  footer,
}: {
  open: boolean
  title: string
  description?: string
  className?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const [closing, setClosing] = useState(false)
  const closeTimer = useRef<number | null>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open) {
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current)
        closeTimer.current = null
      }
      setClosing(false)
      if (!dialog.open) dialog.showModal()
    } else if (dialog.open) {
      dialog.close()
    }
  }, [open])

  useEffect(() => () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
  }, [])

  const requestClose = () => {
    if (closing) return
    setClosing(true)
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null
      setClosing(false)
      onClose()
    }, 150)
  }

  return (
    <dialog
      aria-describedby={description ? descriptionId : undefined}
      aria-labelledby={titleId}
      className={`modal ${className} ${closing ? 'modal--closing' : ''}`}
      ref={ref}
      onCancel={(event) => {
        event.preventDefault()
        requestClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) requestClose()
      }}
      onClose={onClose}
    >
      <div className="modal__header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description ? <p id={descriptionId}>{description}</p> : null}
        </div>
        <Button aria-label="关闭" onClick={requestClose} size="sm" variant="ghost">
          <X size={18} />
        </Button>
      </div>
      <div className="modal__body">{children}</div>
      {footer ? <div className="modal__footer">{footer}</div> : null}
    </dialog>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state" role="status">
      <span className="empty-state__mark" aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  )
}
