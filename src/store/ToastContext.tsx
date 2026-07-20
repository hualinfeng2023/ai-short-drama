import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Toast } from '../components/ui'

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastOptions {
  undo?: () => void
}

interface ToastItem {
  id: number
  message: string
  tone: ToastTone
  leaving: boolean
  undo?: () => void
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone, options?: ToastOptions) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const MAX_VISIBLE = 3
const AUTO_DISMISS_MS = 3600
const EXIT_MS = 180
const OVERFLOW_HINT_MS = 3200

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [overflowHint, setOverflowHint] = useState(0)
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())
  const removalTimers = useRef(new Map<number, number>())
  const overflowTimer = useRef<number | null>(null)

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer === undefined || removalTimers.current.has(id)) return
    window.clearTimeout(timer)
    timers.current.delete(id)
    setToasts((current) => current.map((item) => {
      if (item.id !== id || item.leaving) return item
      return { ...item, leaving: true }
    }))
    const removalTimer = window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id))
      removalTimers.current.delete(id)
    }, EXIT_MS)
    removalTimers.current.set(id, removalTimer)
  }, [])

  const notify = useCallback(
    (message: string, tone: ToastTone = 'success', options?: ToastOptions) => {
      const id = nextId.current++
      setToasts((current) => {
        const displaced = current.slice(0, Math.max(0, current.length - (MAX_VISIBLE - 1)))
        displaced.forEach((item) => {
          const timer = timers.current.get(item.id)
          if (timer !== undefined) window.clearTimeout(timer)
          timers.current.delete(item.id)
          const removalTimer = removalTimers.current.get(item.id)
          if (removalTimer !== undefined) window.clearTimeout(removalTimer)
          removalTimers.current.delete(item.id)
        })
        if (displaced.length > 0) {
          setOverflowHint((count) => count + displaced.length)
          if (overflowTimer.current !== null) window.clearTimeout(overflowTimer.current)
          overflowTimer.current = window.setTimeout(() => {
            setOverflowHint(0)
            overflowTimer.current = null
          }, OVERFLOW_HINT_MS)
        }
        return [
          ...current.slice(-(MAX_VISIBLE - 1)),
          { id, message, tone, leaving: false, undo: options?.undo },
        ]
      })
      // 错误通知需手动关闭
      if (tone !== 'error') {
        const timer = window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
        timers.current.set(id, timer)
      }
    },
    [dismiss],
  )

  useEffect(() => () => {
    timers.current.forEach((timer) => window.clearTimeout(timer))
    timers.current.clear()
    removalTimers.current.forEach((timer) => window.clearTimeout(timer))
    removalTimers.current.clear()
    if (overflowTimer.current !== null) window.clearTimeout(overflowTimer.current)
  }, [])

  const value = useMemo(() => ({ notify }), [notify])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {toasts.length > 0 || overflowHint > 0 ? (
        <div className="toast-region" aria-label="通知">
          {overflowHint > 0 ? (
            <p className="toast-region__overflow" role="status">另有 {overflowHint} 条较早通知已关闭</p>
          ) : null}
          {toasts.map((item) => (
            <Toast
              key={item.id}
              className={item.leaving ? 'toast--leaving' : ''}
              message={item.message}
              onDismiss={() => dismiss(item.id)}
              onUndo={item.undo ? () => {
                item.undo?.()
                dismiss(item.id)
              } : undefined}
              tone={item.tone}
            />
          ))}
        </div>
      ) : null}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast 必须在 ToastProvider 内使用')
  return context
}
