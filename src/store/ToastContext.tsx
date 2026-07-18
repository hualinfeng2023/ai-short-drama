import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Toast } from '../components/ui'

export type ToastTone = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  message: string
  tone: ToastTone
  leaving: boolean
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const MAX_VISIBLE = 3
const AUTO_DISMISS_MS = 3600
const EXIT_MS = 180

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.map((item) => (item.id === id ? { ...item, leaving: true } : item)))
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id))
      const timer = timers.current.get(id)
      if (timer) window.clearTimeout(timer)
      timers.current.delete(id)
    }, EXIT_MS)
  }, [])

  const notify = useCallback(
    (message: string, tone: ToastTone = 'success') => {
      const id = nextId.current++
      setToasts((current) => [...current.slice(-(MAX_VISIBLE - 1)), { id, message, tone, leaving: false }])
      const timer = window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
      timers.current.set(id, timer)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ notify }), [notify])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {toasts.length > 0 ? (
        <div className="toast-region" aria-label="通知" aria-live="polite">
          {toasts.map((item) => (
            <Toast
              key={item.id}
              className={item.leaving ? 'toast--leaving' : ''}
              message={item.message}
              onDismiss={() => dismiss(item.id)}
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
