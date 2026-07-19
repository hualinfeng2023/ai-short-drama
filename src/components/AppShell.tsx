import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bell,
  BookOpenText,
  Boxes,
  ChevronDown,
  CircleUserRound,
  Clapperboard,
  CloudOff,
  Film,
  FolderKanban,
  Images,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  PanelLeftClose,
  PanelLeftOpen,
  Rocket,
  Settings,
  ShieldCheck,
  Users,
  Wifi,
} from 'lucide-react'
import { Link, NavLink, Outlet, useLocation } from 'react-router'
import { fetchJobs, fetchProjectReadiness } from '../api/client'
import { ProjectReadinessContext } from '../store/ProjectReadinessContext'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { Job, JobStatus, ProjectReadiness } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import { buildLocalReadiness } from '../utils/localReadiness'
import { Button, getStatusLabel } from './ui'
import { ErrorBoundary } from './ErrorBoundary'
import { GlossaryTip } from './GlossaryTip'
import { OnboardingDialog, markOnboardingDone, shouldShowOnboarding } from './OnboardingDialog'
import { ProjectWorkflowBar } from './ProjectWorkflowBar'

const ACTIVE_JOB_STATUSES: JobStatus[] = ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED']
const DATA_REFRESH_INTERVAL_MS = 3_000
const DATA_REQUEST_TIMEOUT_MS = 5_000

async function requestWithTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), DATA_REQUEST_TIMEOUT_MS)
  try {
    return await request(controller.signal)
  } finally {
    window.clearTimeout(timeoutId)
  }
}

const navigation = [
  { to: '/projects', label: '项目', icon: FolderKanban },
  { to: '/tasks', label: '生成任务', icon: ListChecks },
  { to: '/reviews', label: '审核中心', icon: ShieldCheck },
  { to: '/settings', label: '设置', icon: Settings },
]

interface Crumb {
  label: string
  to?: string
}

function breadcrumb(pathname: string, projectName: string, projectHref: string): Crumb[] {
  if (pathname === '/projects/new') return [{ label: '项目', to: '/projects' }, { label: '新建项目' }]
  if (pathname.includes('/scenes/')) return [{ label: projectName, to: projectHref }, { label: '第 1 集', to: projectHref }, { label: '场景工作台' }]
  if (pathname.endsWith('/preview')) return [{ label: projectName, to: projectHref }, { label: '第 1 集', to: projectHref }, { label: '完整小样' }]
  if (pathname.endsWith('/story')) return [{ label: projectName, to: projectHref }, { label: '故事与剧本' }]
  if (pathname.endsWith('/characters')) return [{ label: projectName, to: projectHref }, { label: '角色形象生成与锁定' }]
  if (pathname.endsWith('/preproduction')) return [{ label: projectName, to: projectHref }, { label: '前期资产' }]
  if (pathname.endsWith('/storyboard')) return [{ label: projectName, to: projectHref }, { label: '动态分镜' }]
  if (pathname.endsWith('/production')) return [{ label: projectName, to: projectHref }, { label: '正式制作与交付' }]
  if (pathname.includes('/episodes/')) return [{ label: projectName, to: projectHref }, { label: '第 1 集' }]
  if (pathname === '/tasks') return [{ label: '生成任务' }]
  if (pathname === '/reviews') return [{ label: '审核中心' }]
  if (pathname === '/settings') return [{ label: '系统设置' }]
  return [{ label: '项目' }]
}

const SIDEBAR_COLLAPSED_KEY = 'studio-sidebar-collapsed'

export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1'
    } catch {
      return false
    }
  })
  const [accountOpen, setAccountOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(false)

  useEffect(() => {
    if (!shouldShowOnboarding()) return
    const timer = window.setTimeout(() => setOnboardingOpen(true), 700)
    return () => window.clearTimeout(timer)
  }, [])

  useEffect(() => {
    const onShowOnboarding = () => setOnboardingOpen(true)
    window.addEventListener('studio:show-onboarding', onShowOnboarding)
    return () => window.removeEventListener('studio:show-onboarding', onShowOnboarding)
  }, [])

  const finishOnboarding = () => {
    markOnboardingDone()
    setOnboardingOpen(false)
  }
  const [globalJobs, setGlobalJobs] = useState<Job[]>([])
  const [readiness, setReadiness] = useState<ProjectReadiness | null>(null)
  const [readinessErrorProjectId, setReadinessErrorProjectId] = useState<string | null>(null)
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
  const { notify } = useToast()
  const visibleJobs = apiStatus === 'connected'
    ? (routeProjectId
      ? globalJobs.filter((job) => job.projectId === routeProjectId)
      : globalJobs)
    : (routeProjectId && routeProjectId !== project.id ? [] : contextJobs)
  const activeJobCount = visibleJobs.filter((job) =>
    ACTIVE_JOB_STATUSES.includes(job.status),
  ).length
  const fallbackReadiness = useMemo(() => (
    apiStatus !== 'connected' && routeProjectId
      ? buildLocalReadiness({
        projectId: routeProjectId,
        episodeId: routeProjectId === project.id ? project.episodeId : null,
        activeJobCount,
      })
      : null
  ), [apiStatus, routeProjectId, project.id, project.episodeId, activeJobCount])
  const routeReadiness = readiness?.projectId === routeProjectId ? readiness : null
  const contextReadiness = apiStatus === 'connected' ? routeReadiness : fallbackReadiness
  const contextReadinessError = apiStatus === 'connected'
    && readinessErrorProjectId === routeProjectId
  const contextReadinessLoading = apiStatus === 'connected'
    ? readinessLoading || (
      Boolean(routeProjectId)
      && !routeReadiness
      && !contextReadinessError
    )
    : false
  const latestJob = visibleJobs[0]
  const crumbs = breadcrumb(location.pathname, currentProjectName, currentProjectLink)

  useEffect(() => {
    if (apiStatus !== 'connected') {
      setGlobalJobs([])
      return
    }
    let active = true
    let refreshInFlight = false
    const refresh = async () => {
      if (refreshInFlight) return
      refreshInFlight = true
      try {
        const latest = await requestWithTimeout((signal) => fetchJobs(signal))
        if (active) setGlobalJobs(latest)
      } catch {
        // Keep the last successful task snapshot; project-stage polling is independent.
      } finally {
        refreshInFlight = false
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, DATA_REFRESH_INTERVAL_MS)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [apiStatus])

  useEffect(() => {
    if (apiStatus !== 'connected' || !routeProjectId) {
      setReadiness(null)
      setReadinessErrorProjectId(null)
      setReadinessLoading(false)
      return
    }

    let active = true
    let refreshInFlight = false
    let hasSnapshot = false
    setReadiness(null)
    setReadinessErrorProjectId(null)
    setReadinessLoading(true)

    const refresh = async () => {
      if (refreshInFlight) return
      refreshInFlight = true
      try {
        const nextReadiness = await requestWithTimeout(
          (signal) => fetchProjectReadiness(routeProjectId, signal),
        )
        if (active) {
          hasSnapshot = true
          setReadiness(nextReadiness)
          setReadinessErrorProjectId(null)
        }
      } catch {
        if (active && !hasSnapshot) {
          setReadinessErrorProjectId(routeProjectId)
        }
      } finally {
        if (active) setReadinessLoading(false)
        refreshInFlight = false
      }
    }

    void refresh()
    const interval = window.setInterval(refresh, DATA_REFRESH_INTERVAL_MS)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [apiStatus, routeProjectId])

  useEffect(() => {
    setAccountOpen(false)
    setNotificationsOpen(false)
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior })
  }, [location.pathname])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0')
    } catch {
      // 本地存储不可用时静默失败，折叠状态仅在本次会话内生效。
    }
  }, [collapsed])

  useEffect(() => {
    if (!accountOpen && !notificationsOpen) return
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Element)) return
      if (!event.target.closest('.popover-wrap')) {
        setAccountOpen(false)
        setNotificationsOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setAccountOpen(false)
        setNotificationsOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [accountOpen, notificationsOpen])

  const jobStatusSnapshot = useRef<Map<string, JobStatus> | null>(null)
  useEffect(() => {
    const nextSnapshot = new Map(visibleJobs.map((job) => [job.id, job.status]))
    const previousSnapshot = jobStatusSnapshot.current
    jobStatusSnapshot.current = nextSnapshot
    if (previousSnapshot === null) return
    for (const job of visibleJobs) {
      const previousStatus = previousSnapshot.get(job.id)
      if (!previousStatus || previousStatus === job.status) continue
      if (!ACTIVE_JOB_STATUSES.includes(previousStatus)) continue
      const label = localizeDisplayText(job.label)
      if (job.status === 'SUCCEEDED') notify(`${label} 已完成。`)
      else if (job.status === 'FAILED') notify(`${label} 失败，可在生成任务页查看原因。`, 'error')
      else if (job.status === 'CANCELLED') notify(`${label} 已取消。`, 'info')
    }
  }, [visibleJobs, notify])

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
          {routeProjectId ? (
            <nav aria-label="项目内导航" className="sidebar__subnav">
              {([
                { label: '样片工作台', to: currentProjectLink, icon: Film, offlineReady: true },
                { label: '故事与剧本', to: `/projects/${routeProjectId}/story`, icon: BookOpenText, offlineReady: false },
                { label: '角色', to: `/projects/${routeProjectId}/characters`, icon: Users, offlineReady: false },
                { label: '前期资产', to: `/projects/${routeProjectId}/preproduction`, icon: Boxes, offlineReady: false },
                { label: '动态分镜', to: `/projects/${routeProjectId}/storyboard`, icon: Images, offlineReady: false },
                { label: '正式制作', to: `/projects/${routeProjectId}/production`, icon: Rocket, offlineReady: false },
              ]).map(({ label, to, icon: Icon, offlineReady }) => {
                const locked = apiStatus !== 'connected' && !offlineReady
                return (
                  <NavLink
                    className={({ isActive }) => `sidebar__subnav-item ${isActive ? 'sidebar__subnav-item--active' : ''}`}
                    end={label === '样片工作台'}
                    key={to}
                    title={locked ? `${label} · 连接服务端后可用` : label}
                    to={to}
                  >
                    <Icon size={15} strokeWidth={1.8} />
                    <span>{label}</span>
                    {locked ? <LockKeyhole aria-label="连接服务端后可用" className="sidebar__subnav-lock" size={12} /> : null}
                  </NavLink>
                )
              })}
            </nav>
          ) : null}
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
                <span key={`${crumb.label}-${index}`}>
                  {index > 0 ? <i>/</i> : null}
                  {crumb.to && index < crumbs.length - 1 ? (
                    <Link to={crumb.to}>{crumb.label}</Link>
                  ) : (
                    crumb.label
                  )}
                </span>
              ))}
            </nav>
          </div>
          <div className="topbar__actions">
            <span
              className={`system-status system-status--${apiStatus}`}
              title={apiStatus === 'loading' ? '正在连接后端服务' : undefined}
            >
              {apiStatus === 'connected' ? <Wifi size={14} /> : <CloudOff size={14} />}
              {apiStatus === 'connected' ? <span>已连接</span> : apiStatus === 'loading' ? <span>连接中</span> : (
                <GlossaryTip
                  focusable
                  align="end"
                  label="本地模式"
                  tip="当前是浏览器演示模式：数据只保存在本浏览器中，可直接体验镜头制作全流程；启动本地服务端后解锁五阶段制作流与数据持久化。"
                />
              )}
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
                onClick={() => {
                  setNotificationsOpen((value) => !value)
                  setAccountOpen(false)
                }}
                size="sm"
                variant="ghost"
              >
                <Bell size={18} />
                {activeJobCount > 0 ? <span className="notification-dot" /> : null}
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
                className={`account-button ${accountOpen ? 'account-button--open' : ''}`}
                aria-controls="account-popover"
                aria-expanded={accountOpen}
                aria-haspopup="menu"
                onClick={() => {
                  setAccountOpen((value) => !value)
                  setNotificationsOpen(false)
                }}
                type="button"
              >
                <CircleUserRound size={22} />
                <span>演示用户</span>
                <ChevronDown className="account-button__chevron" size={15} />
              </button>
              {accountOpen ? (
                <div className="popover" id="account-popover" role="menu">
                  <p className="popover__meta">本地单用户模式</p>
                  <Link role="menuitem" to="/settings" onClick={() => setAccountOpen(false)}>打开系统设置</Link>
                  <button
                    role="menuitem"
                    onClick={() => {
                      setAccountOpen(false)
                      setOnboardingOpen(true)
                    }}
                    type="button"
                  >
                    查看新手引导
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <ProjectReadinessContext.Provider value={{
          error: contextReadinessError,
          loading: contextReadinessLoading,
          readiness: contextReadiness,
        }}>
          <main className="content-area" id="main-content" tabIndex={-1}>
            {pathProjectId && pathProjectId !== 'new' ? <ProjectWorkflowBar /> : null}
            <ErrorBoundary key={location.pathname}>
              <Suspense fallback={
                <div className="route-loading" role="status">
                  <LoaderCircle className="spin" size={20} />
                  <span>正在打开工作区…</span>
                </div>
              }>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </main>
        </ProjectReadinessContext.Provider>
      </div>
      <OnboardingDialog open={onboardingOpen} onFinish={finishOnboarding} />
    </div>
  )
}
