import { ArrowLeft, CloudOff, RefreshCw, Settings } from 'lucide-react'
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
        <span className="service-required__icon" aria-hidden="true">
          <CloudOff size={22} />
        </span>
        {connected ? (
          <>
            <h2>「{feature}」暂时没有返回数据</h2>
            <p>服务端已连接，但这个页面的数据读取失败了。重新加载通常可以恢复；持续失败请到生成任务页查看具体原因。</p>
          </>
        ) : (
          <>
            <h2>「{feature}」需要连接本地服务端</h2>
            <p>
              当前是浏览器演示模式，可以直接体验经典镜头工作流（剧集、场景与镜头制作）。
              {feature}属于五阶段制作流，数据由本地服务端生成并持久化。
            </p>
            <div className="service-required__how">
              <strong>如何开启</strong>
              <ol>
                <li>在项目根目录运行 <code>docker compose up --build</code>，或 <code>uv run uvicorn app.main:app --port 8000</code></li>
                <li>回到本页刷新，右上角状态变为「已连接」后即可使用</li>
              </ol>
            </div>
          </>
        )}
        <div className="service-required__actions">
          {connected ? (
            <button className="button button--primary button--md" onClick={() => window.location.reload()} type="button">
              <RefreshCw size={16} />
              重新加载
            </button>
          ) : null}
          {!connected && demoWorkspaceHref ? (
            <Link className="button button--primary button--md" to={demoWorkspaceHref}>
              继续镜头工作流（演示可用）
            </Link>
          ) : null}
          {!connected && !demoWorkspaceHref ? (
            <Link className="button button--secondary button--md" to="/projects">
              <ArrowLeft size={16} />
              返回项目列表
            </Link>
          ) : null}
          {connected ? (
            <Link className="button button--secondary button--md" to="/projects">
              <ArrowLeft size={16} />
              返回项目列表
            </Link>
          ) : (
            <Link className="button button--ghost button--md" to="/settings">
              <Settings size={16} />
              系统设置
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
