import { AlertTriangle, LoaderCircle } from 'lucide-react'
import { type ReactNode } from 'react'
import { Button, Modal } from './ui'

export interface ConfirmModalProps {
  cancelLabel?: string
  children?: ReactNode
  className?: string
  confirmLabel?: string
  confirmDisabled?: boolean
  confirmVariant?: 'primary' | 'danger' | 'secondary'
  description?: string
  loading?: boolean
  onClose: () => void
  onConfirm: () => void
  open: boolean
  title: string
}

/** A 级不可逆操作确认弹窗 */
export function ConfirmModal({
  cancelLabel = '取消',
  children,
  className = '',
  confirmLabel = '确认',
  confirmDisabled = false,
  confirmVariant = 'primary',
  description,
  loading = false,
  onClose,
  onConfirm,
  open,
  title,
}: ConfirmModalProps) {
  return (
    <Modal
      className={className}
      description={description}
      footer={<>
        <Button disabled={loading} onClick={onClose} variant="secondary">{cancelLabel}</Button>
        <Button disabled={loading || confirmDisabled} onClick={onConfirm} variant={confirmVariant}>
          {loading ? <LoaderCircle className="spin" size={16} /> : null}
          {confirmLabel}
        </Button>
      </>}
      onClose={() => { if (!loading) onClose() }}
      open={open}
      title={title}
    >
      {children ?? null}
    </Modal>
  )
}

export interface ImpactConfirmItem {
  detail: string
  icon?: ReactNode
  title: string
}

/** 阶段门禁 / 影响预览确认弹窗 */
export function ImpactConfirmModal({
  cancelLabel = '取消',
  confirmLabel,
  confirmVariant = 'primary',
  description,
  items,
  loading = false,
  onClose,
  onConfirm,
  open,
  subtitle,
  title,
}: {
  cancelLabel?: string
  confirmLabel: string
  confirmVariant?: 'primary' | 'danger' | 'secondary'
  description?: string
  items: ImpactConfirmItem[]
  loading?: boolean
  onClose: () => void
  onConfirm: () => void
  open: boolean
  subtitle?: string
  title: string
}) {
  return (
    <Modal
      className="modal--impact-confirm"
      description={description}
      footer={<>
        <Button disabled={loading} onClick={onClose} variant="secondary">{cancelLabel}</Button>
        <Button disabled={loading} onClick={onConfirm} variant={confirmVariant}>
          {loading ? <LoaderCircle className="spin" size={16} /> : null}
          {confirmLabel}
        </Button>
      </>}
      onClose={() => { if (!loading) onClose() }}
      open={open}
      title={title}
    >
      {subtitle ? <p className="impact-confirm__subtitle">{subtitle}</p> : null}
      <div className="impact-list">
        {items.map((item) => (
          <span key={item.title}>
            {item.icon ?? <AlertTriangle size={16} />}
            <div><strong>{item.title}</strong><p>{item.detail}</p></div>
          </span>
        ))}
      </div>
    </Modal>
  )
}
