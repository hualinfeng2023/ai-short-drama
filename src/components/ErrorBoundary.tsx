import { Component, type ReactNode } from 'react'
import { RotateCcw, TriangleAlert } from 'lucide-react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: unknown) {
    console.error('[ui] 页面渲染失败', error)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="route-error" role="alert">
          <span className="route-error__icon" aria-hidden="true">
            <TriangleAlert size={22} />
          </span>
          <h2>这个页面暂时无法显示</h2>
          <p>界面渲染时发生意外错误，你的项目数据不受影响。可以刷新重试，或先返回项目列表。</p>
          <div className="route-error__actions">
            <button onClick={() => window.location.reload()} type="button">
              <RotateCcw size={15} />
              刷新页面
            </button>
            <a href="/projects">返回项目列表</a>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
