import { Suspense, useEffect, useState } from 'react'
import {
  Bell,
  ChevronDown,
  CircleUserRound,
  Clapperboard,
  CloudOff,
  FolderKanban,
  ListChecks,
  LoaderCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  Wifi,
} from 'lucide-react'
import { Link, NavLink, Outlet, useLocation } from 'react-router'
import { fetchJobs, fetchProjectReadiness } from '../api/client'
import { ProjectReadinessContext } from '../store/ProjectReadinessContext'
import { useStudio } from '../store/StudioContext'
import type { Job, ProjectReadiness } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import { Button, getStatusLabel } from './ui'
import { ProjectWorkflowBar } from './ProjectWorkflowBar'

const navigation = [
  { to: '/projects', label: '项目', icon: FolderKanban },
  { to: '/tasks', label: '生成任务', icon: ListChecks },
  { to: '/reviews', label: '审核中心', icon: ShieldCheck },
  { to: '/settings', label: '设置', icon: Settings },
]

function breadcrumb(pathname: string, projectName: string): string[] {
  if (pathname === '/projects/new') return ['项目', 'AI 新建项目']
  if (pathname.includes('/scenes/')) return [projectName, '第 1 集', '场景工作台']
  if (pathname.endsWith('/preview')) return [projectName, '第 1 集', '完整小样']
  if (pathname.endsWith('/story')) return [projectName, '故事与剧本']
  if (pathname.endsWith('/characters')) return [projectName, '角色形象生成与锁定']
  if (pathname.endsWith('/preproduction')) return [projectName, '第 3 阶段 · 前期资产']
  if (pathname.endsWith('/storyboard')) return [projectName, '第 4 阶段 · 动态分镜']
  if (pathname.endsWith('/production')) return [projectName, '第 5 阶段 · 正式制作与交付']
  if (pathname.includes('/episodes/')) return [projectName, '第 1 集']
  if (pathname === '/tasks') return ['生成任务']
  if (pathname === '/reviews') return ['审核中心']
  if (pathname === '/settings') return ['系统设置']
  return ['项目']
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [globalJobs, setGlobalJobs] = useState<Job[]>([])
  const [readiness, setReadiness] = useState<ProjectReadiness | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(false)
  const { apiStatus, jobs: contextJobs, project, projectSummaries } = useStudio()
  const location = useLocation()
  const pathProjectId = location.pathname.match(/^\/projects\/([^/]+)/)?.[1]
  const queryProjectId = new URLSearchParams(location.search).get('project')
  const routeProjectId = queryProjectId ?? (pathProjectId && pathProjectId !== 'new' ? pathProjectId : null)
  const contextualProject = projectSummaries.find(
    (item) => item.id === routeProjectId,
  )
  const taskHref = routeProjectId ? `/tasks?project=${routeProjectId}` : '/tasks'
  const currentProjectName = contextualProject?.name ?? project.name
  const currentProjectLink = contextualProject && contextualProject.id !== project.id
    ? `/projects/${contextualProject.id}`
    : `/projects/${project.id}/episodes/${project.episodeId}`
  const currentProjectMeta = contextualProject && contextualProject.id !== project.id
    ? getStatusLabel(contextualProject.status)
    : '第 1 集 · 验证样片'
  const visibleJobs = apiStatus === 'connected'
    ? (routeProjectId
      ? globalJobs.filter((job) => job.projectId === routeProjectId)
      : globalJobs)
    : (routeProjectId && routeProjectId !== project.id ? [] : contextJobs)
  const activeJobCount = visibleJobs.filter((job) =>
    ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
  ).length
  const latestJob = visibleJobs[0]
  const crumbs = breadcrumb(location.pathname, currentProjectName)

  useEffect(() => {
    if (apiStatus !== 'connected') {
      setGlobalJobs([])
      setReadiness(null)
      return
    }
    let active = true
    let refreshInFlight = false
    const refresh = async () => {
      if (refreshInFlight) return
      refreshInFlight = true
      try {
        if (routeProjectId) setReadinessLoading(true)
        const [latest, nextReadiness] = await Promise.all([
          fetchJobs(),
          routeProjectId ? fetchProjectReadiness(routeProjectId) : Promise.resolve(null),
        ])
        if (active) {
          setGlobalJobs(latest)
          setReadiness(nextReadiness)
        }
      } catch {
        // Keep the last successful snapshot; the task page reports fetch errors in detail.
      } finally {
        if (active) setReadinessLoading(false)
        refreshInFlight = false
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, 3000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [apiStatus, routeProjectId])

  useEffect(() => {
    setAccountOpen(false)
    setNotificationsOpen(false)
  }, [location.pathname])

  return (
    <div className={`app-shell ${collapsed ? 'app-shell--collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar" aria-label="主导航">
        <Link className="brand" to="/projects" aria-label="剧创 AI 首页">
          <span className="brand__mark"><Clapperboard size={20} /></span>
          <span className="brand__text">
            <strong>剧创 AI</strong>
            <small>创作工作台</small>
          </span>
          <span className="beta">测试版</span>
        </Link>

        <nav className="sidebar__nav">
          <p className="sidebar__label">工作区</p>
          {navigation.map(({ to, label, icon: Icon }) => {
            const href = label === '生成任务' ? taskHref : to
            return (
            <NavLink
              className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
              key={to}
              to={href}
              title={collapsed ? label : undefined}
            >
              <Icon size={18} strokeWidth={1.8} />
              <span>{label}</span>
              {label === '生成任务' && activeJobCount > 0 ? <em>{activeJobCount}</em> : null}
            </NavLink>
            )
          })}
        </nav>

        <div className="sidebar__project">
          <p className="sidebar__label">当前项目</p>
          <Link to={currentProjectLink}>
            <span className="project-monogram">{currentProjectName.slice(0, 1)}</span>
            <span>
              <strong>{currentProjectName}</strong>
              <small>{currentProjectMeta}</small>
            </span>
          </Link>
        </div>

        <button
          aria-expanded={!collapsed}
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
          className="sidebar__collapse"
          onClick={() => setCollapsed((value) => !value)}
          type="button"
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          <span>{collapsed ? '展开' : '收起侧栏'}</span>
        </button>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar__leading">
            <Link className="mobile-brand" to="/projects" aria-label="剧创 AI 首页">
              <span><Clapperboard size={17} /></span>
              <strong>剧创 AI</strong>
            </Link>
            <nav className="breadcrumbs" aria-label="面包屑">
              {crumbs.map((crumb, index) => (
                <span key={`${crumb}-${index}`}>
                  {index > 0 ? <i>/</i> : null}
                  {crumb}
                </span>
              ))}
            </nav>
          </div>
          <div className="topbar__actions">
            <span
              className={`system-status system-status--${apiStatus}`}
              title={apiStatus === 'connected' ? '后端服务已连接' : apiStatus === 'loading' ? '正在连接后端服务' : '当前使用本地回退数据'}
            >
              {apiStatus === 'connected' ? <Wifi size={14} /> : <CloudOff size={14} />}
              <span>{apiStatus === 'connected' ? '已连接' : apiStatus === 'loading' ? '连接中' : '本地模式'}</span>
            </span>
            <div className="credit-balance" title="演示积分，不对应真实货币">
              <span />
              <strong>{project.availablePoints.toLocaleString('zh-CN')}</strong>
              <small>可用积分</small>
            </div>
            <div className="popover-wrap">
              <Button
                aria-controls="notification-popover"
                aria-expanded={notificationsOpen}
                aria-haspopup="dialog"
                aria-label="通知"
                onClick={() => setNotificationsOpen((value) => !value)}
                size="sm"
                variant="ghost"
              >
                <Bell size={18} />
                {latestJob ? <span className="notification-dot" /> : null}
              </Button>
              {notificationsOpen ? (
                <div className="popover popover--notification" id="notification-popover" role="status">
                  <strong>{latestJob ? localizeDisplayText(latestJob.label) : '当前没有任务通知'}</strong>
                  <p>{latestJob ? `${localizeDisplayText(latestJob.stage)} · ${Math.round(latestJob.progress)}%` : '创建项目后，持久化任务进度会显示在这里。'}</p>
                  <Link to={taskHref} onClick={() => setNotificationsOpen(false)}>查看任务</Link>
                </div>
              ) : null}
            </div>
            <div className="popover-wrap">
              <button
                aria-label="演示用户账户"
                className="account-button"
                aria-controls="account-popover"
                aria-expanded={accountOpen}
                aria-haspopup="menu"
                onClick={() => setAccountOpen((value) => !value)}
                type="button"
              >
                <CircleUserRound size={22} />
                <span>演示用户</span>
                <ChevronDown size={15} />
              </button>
              {accountOpen ? (
                <div className="popover" id="account-popover" role="menu">
                  <p className="popover__meta">本地单用户模式</p>
                  <Link role="menuitem" to="/settings" onClick={() => setAccountOpen(false)}>打开系统设置</Link>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <ProjectReadinessContext.Provider value={{ loading: readinessLoading, readiness }}>
          <main className="content-area" id="main-content" tabIndex={-1}>
            {pathProjectId && pathProjectId !== 'new' ? <ProjectWorkflowBar /> : null}
            <Suspense fallback={
              <div className="route-loading" role="status">
                <LoaderCircle className="spin" size={20} />
                <span>正在打开工作区…</span>
              </div>
            }>
              <Outlet />
            </Suspense>
          </main>
        </ProjectReadinessContext.Provider>
      </div>
    </div>
  )
}
