import { ArrowLeft, RefreshCw, ServerOff, Settings } from 'lucide-react'
import { Link } from 'react-router'
import { EPISODE_ID, PROJECT_ID } from '../data/demo'
import { useStudio } from '../store/StudioContext'

/**
 * 五阶段制作流页面的统一不可用占位。
 * - 离线（演示模式）：说明原因 + 开启步骤，出口只指向确定可用的页面
 *   （演示项目的镜头工作流 / 项目列表），绝不指向另一个不可用页面。
 * - 已连接但读取失败：提供「重新加载」作为主行动。
 */
export function ServiceRequiredState({
  feature,
  projectId,
}: {
  feature: string
  projectId?: string | null
}) {
  const { apiStatus } = useStudio()
  const connected = apiStatus === 'connected'
  const demoWorkspaceHref = projectId === PROJECT_ID
    ? `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}`
    : null

  return (
    <div className="page">
      <div className="service-required" role="status">
        <header className="service-required__header">
          <span className="service-required__icon" aria-hidden="true">
            <ServerOff size={20} />
          </span>
          <div>
            <p className="service-required__kicker">
              {feature} · {connected ? '数据读取异常' : '本地服务未连接'}
            </p>
            <h2>{connected ? '暂时无法加载此页面' : '连接服务后继续'}</h2>
          </div>
        </header>

        <p className="service-required__description">
          {connected
            ? '服务已经连接，但页面数据没有成功返回。重新加载通常可以恢复，已有内容不会受到影响。'
            : '此页面依赖本地项目数据。启动服务并重新检测后，即可继续使用完整制作流程。'}
        </p>

        {!connected ? (
          <div className="service-required__how">
            <strong>启动服务</strong>
            <code>docker compose up --build</code>
            <span>也可以运行 uv run uvicorn app.main:app --port 8000</span>
          </div>
        ) : null}

        <div className="service-required__actions">
          <button className="button button--primary button--md" onClick={() => window.location.reload()} type="button">
            <RefreshCw size={16} />
            {connected ? '重新加载' : '重新检测'}
          </button>
          {!connected && demoWorkspaceHref ? (
            <Link className="button button--secondary button--md" to={demoWorkspaceHref}>
              继续镜头工作流（演示可用）
            </Link>
          ) : null}
          {!demoWorkspaceHref ? (
            <Link className="button button--secondary button--md" to="/projects">
              <ArrowLeft size={16} />
              返回项目列表
            </Link>
          ) : null}
          {!connected ? (
            <Link className="button button--ghost button--md" to="/settings">
              <Settings size={16} />
              系统设置
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  )
}
