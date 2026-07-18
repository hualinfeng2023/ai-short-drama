import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Cloud,
  CloudOff,
  Database,
  Eye,
  EyeOff,
  HardDrive,
  KeyRound,
  LockKeyhole,
  LoaderCircle,
  MonitorCog,
  PlugZap,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react'
import {
  fetchProviderSettings,
  fetchRuntimeConfig,
  saveProviderSettings,
  testProviderConnection,
  type ProviderConnectionResult,
  type ProviderSettings,
  type RuntimeConfig,
} from '../api/client'
import { Button, Modal, PageHeader, SelectControl, StatusBadge } from '../components/ui'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { VisualMode } from '../types'

const modes: Array<{ id: VisualMode; title: string; description: string; tone: string }> = [
  { id: 'standard', title: '标准工作台', description: '规范基线：浅灰画布、白色面板、蓝色单主色。', tone: '适合完整流程' },
  { id: 'focus', title: '专注模式', description: '更紧凑的间距与更少的辅助说明，优先信息密度。', tone: '适合高频审核' },
  { id: 'cinema', title: '暗房模式', description: '深色工作区与更强画面层级，突出小样和版本比较。', tone: '适合媒体审阅' },
]

const PROMPT_MODEL_OPTIONS = [
  { id: 'doubao-seed-2-0-lite-260215', label: '豆包 Seed 2.0 Lite' },
]
const IMAGE_MODEL_OPTIONS = [
  { id: 'doubao-seedream-5-0-260128', label: 'Seedream 5.0 Pro' },
  { id: 'doubao-seedream-5-0-lite-260128', label: 'Seedream 5.0 Lite' },
  { id: 'doubao-seedream-4-5-251128', label: 'Seedream 4.5' },
  { id: 'doubao-seedream-4-0-250828', label: 'Seedream 4.0' },
]
const VIDEO_MODEL_OPTIONS = [
  { id: 'doubao-seedance-1-5-pro-251215', label: 'Seedance 1.5 Pro' },
]

function includeConfiguredModel(
  options: Array<{ id: string; label: string }>,
  configuredModel: string,
) {
  return options.some((option) => option.id === configuredModel)
    ? options
    : [{ id: configuredModel, label: '当前自定义配置' }, ...options]
}

function sourceLabel(source: 'saved' | 'environment' | 'default') {
  if (source === 'saved') return '设置页保存'
  if (source === 'environment') return '环境变量'
  return '尚未配置'
}

function SecretField({
  label,
  configured,
  hint,
  source,
  value,
  clear,
  optional = false,
  wide = false,
  onChange,
  onClear,
}: {
  label: string
  configured: boolean
  hint: string | null
  source?: 'saved' | 'environment' | 'default'
  value: string
  clear: boolean
  optional?: boolean
  wide?: boolean
  onChange: (value: string) => void
  onClear: (value: boolean) => void
}) {
  const [visible, setVisible] = useState(false)
  return <label className={`api-field api-field--secret${wide ? ' api-field--wide' : ''}`}>
    <span>{label}{optional ? <small>可选</small> : null}</span>
    <div className="api-secret-input">
      <input
        autoComplete="off"
        disabled={clear}
        onChange={(event) => {
          onClear(false)
          onChange(event.target.value)
        }}
        placeholder={configured ? `已保存 ${hint ?? '密钥'}` : '输入后保存到服务端'}
        type={visible ? 'text' : 'password'}
        value={value}
      />
      <button
        aria-label={visible ? `隐藏${label}` : `显示${label}`}
        onClick={() => setVisible((current) => !current)}
        type="button"
      >{visible ? <EyeOff size={15} /> : <Eye size={15} />}</button>
    </div>
    <small>{clear ? '保存后将清除此密钥' : configured ? `来源：${source ? sourceLabel(source) : '服务端'}；留空表示保持不变` : '密钥不会从服务端回传明文'}</small>
    {configured ? <button className={clear ? 'api-clear-secret active' : 'api-clear-secret'} onClick={() => {
      const nextClear = !clear
      if (nextClear) onChange('')
      onClear(nextClear)
    }} type="button">{clear ? '撤销清除' : '清除已保存值'}</button> : null}
  </label>
}

function ConnectionResult({ result }: { result: ProviderConnectionResult | null }) {
  if (!result) return null
  const status = result.status === 'connected' ? 'APPROVED' : result.status === 'error' ? 'FAILED' : 'READY'
  return <div className={`provider-test-result provider-test-result--${result.status}`}>
    <StatusBadge label={result.status === 'connected' ? '连接成功' : result.status === 'error' ? '连接失败' : '未配置'} status={status} />
    <span>{result.message}</span>
  </div>
}

export function SettingsPage() {
  const { apiStatus, project, visualMode, setVisualMode, resetDemo, resyncCurrentProject } = useStudio()
  const { notify } = useToast()
  const [settingsSection, setSettingsSection] = useState<'appearance' | 'runtime' | 'providers' | 'data'>('appearance')
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [providerSettings, setProviderSettings] = useState<ProviderSettings | null>(null)
  const [draft, setDraft] = useState<ProviderSettings | null>(null)
  const [arkApiKey, setArkApiKey] = useState('')
  const [tosAccessKey, setTosAccessKey] = useState('')
  const [tosSecretKey, setTosSecretKey] = useState('')
  const [tosSecurityToken, setTosSecurityToken] = useState('')
  const [clearArkApiKey, setClearArkApiKey] = useState(false)
  const [clearTosAccessKey, setClearTosAccessKey] = useState(false)
  const [clearTosSecretKey, setClearTosSecretKey] = useState(false)
  const [clearTosSecurityToken, setClearTosSecurityToken] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<'ark' | 'tos' | null>(null)
  const [testResults, setTestResults] = useState<Record<'ark' | 'tos', ProviderConnectionResult | null>>({ ark: null, tos: null })
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; message: string } | null>(null)
  const [activeProvider, setActiveProvider] = useState<'ark' | 'tos'>('ark')
  const [recoveryOpen, setRecoveryOpen] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [recoveryNotice, setRecoveryNotice] = useState<{ tone: 'success' | 'error'; message: string } | null>(null)
  const apiConnected = apiStatus === 'connected'

  useEffect(() => {
    if (!apiConnected) {
      setRuntime(null)
      setProviderSettings(null)
      setDraft(null)
      return
    }
    const controller = new AbortController()
    void Promise.all([
      fetchRuntimeConfig(controller.signal),
      fetchProviderSettings(controller.signal),
    ]).then(([nextRuntime, nextSettings]) => {
      setRuntime(nextRuntime)
      setProviderSettings(nextSettings)
      setDraft(nextSettings)
    }).catch(() => {
      setRuntime(null)
      setProviderSettings(null)
      setDraft(null)
    })
    return () => controller.abort()
  }, [apiConnected])

  const capabilities = runtime?.capabilities
  const imageLabel = capabilities
    ? `${capabilities.imageModel} · ${capabilities.imageProvider}`
    : apiConnected ? '读取运行配置中' : '模拟模式 · 确定性生成'
  const videoLabel = capabilities?.providerCalls
    ? `${capabilities.videoModel} · ${capabilities.videoProvider}`
    : 'Seedance 可选 · 需配置方舟 API Key'
  const providerChanges = useMemo(() => {
    if (!draft || !providerSettings) return { ark: false, tos: false }
    const ark = JSON.stringify(draft.ark) !== JSON.stringify(providerSettings.ark)
      || Boolean(arkApiKey) || clearArkApiKey
    const tos = JSON.stringify(draft.tos) !== JSON.stringify(providerSettings.tos)
      || Boolean(tosAccessKey || tosSecretKey || tosSecurityToken)
      || clearTosAccessKey || clearTosSecretKey || clearTosSecurityToken
    return { ark, tos }
  }, [arkApiKey, clearArkApiKey, clearTosAccessKey, clearTosSecretKey, clearTosSecurityToken, draft, providerSettings, tosAccessKey, tosSecretKey, tosSecurityToken])
  const hasProviderChanges = providerChanges.ark || providerChanges.tos

  function updateArk<K extends keyof ProviderSettings['ark']>(
    key: K,
    value: ProviderSettings['ark'][K],
  ) {
    setDraft((current) => current ? { ...current, ark: { ...current.ark, [key]: value } } : current)
  }

  function updateTos<K extends keyof ProviderSettings['tos']>(
    key: K,
    value: ProviderSettings['tos'][K],
  ) {
    setDraft((current) => current ? { ...current, tos: { ...current.tos, [key]: value } } : current)
  }

  async function saveSettings() {
    if (!draft) return
    setSaving(true)
    setNotice(null)
    try {
      const saved = await saveProviderSettings({
        ark: {
          ...(arkApiKey.trim() ? { apiKey: arkApiKey.trim() } : {}),
          clearApiKey: clearArkApiKey,
          responsesUrl: draft.ark.responsesUrl,
          promptModel: draft.ark.promptModel,
          imagesUrl: draft.ark.imagesUrl,
          imageModel: draft.ark.imageModel,
          videoTasksUrl: draft.ark.videoTasksUrl,
          videoModel: draft.ark.videoModel,
          requestTimeoutSeconds: draft.ark.requestTimeoutSeconds,
          videoPollIntervalSeconds: draft.ark.videoPollIntervalSeconds,
          videoTimeoutSeconds: draft.ark.videoTimeoutSeconds,
          sourceUrlFastPathSeconds: draft.ark.sourceUrlFastPathSeconds,
          identityQcEnabled: draft.ark.identityQcEnabled,
          identityAutoPassThreshold: draft.ark.identityAutoPassThreshold,
        },
        tos: {
          enabled: draft.tos.enabled,
          ...(tosAccessKey.trim() ? { accessKey: tosAccessKey.trim() } : {}),
          clearAccessKey: clearTosAccessKey,
          ...(tosSecretKey.trim() ? { secretKey: tosSecretKey.trim() } : {}),
          clearSecretKey: clearTosSecretKey,
          ...(tosSecurityToken.trim() ? { securityToken: tosSecurityToken.trim() } : {}),
          clearSecurityToken: clearTosSecurityToken,
          endpoint: draft.tos.endpoint,
          region: draft.tos.region,
          bucket: draft.tos.bucket,
          presignTtlSeconds: draft.tos.presignTtlSeconds,
          objectPrefix: draft.tos.objectPrefix,
          objectExpiresDays: draft.tos.objectExpiresDays,
          cleanupOnCompletion: draft.tos.cleanupOnCompletion,
        },
      })
      setProviderSettings(saved)
      setDraft(saved)
      setArkApiKey('')
      setTosAccessKey('')
      setTosSecretKey('')
      setTosSecurityToken('')
      setClearArkApiKey(false)
      setClearTosAccessKey(false)
      setClearTosSecretKey(false)
      setClearTosSecurityToken(false)
      setRuntime(await fetchRuntimeConfig())
      setTestResults({ ark: null, tos: null })
      setNotice({ tone: 'success', message: 'API 设置已保存到服务端，并对后续任务即时生效。' })
    } catch (reason) {
      setNotice({ tone: 'error', message: reason instanceof Error ? reason.message : 'API 设置保存失败' })
    } finally {
      setSaving(false)
    }
  }

  async function testConnection(provider: 'ark' | 'tos') {
    setTesting(provider)
    setNotice(null)
    try {
      const result = await testProviderConnection(provider)
      setTestResults((current) => ({ ...current, [provider]: result }))
    } catch (reason) {
      setTestResults((current) => ({
        ...current,
        [provider]: {
          provider,
          status: 'error',
          message: reason instanceof Error ? reason.message : '连接测试失败',
        },
      }))
    } finally {
      setTesting(null)
    }
  }

  async function recoverCurrentProject() {
    if (recovering) return
    setRecovering(true)
    setRecoveryNotice(null)
    try {
      await resyncCurrentProject()
      setRecoveryOpen(false)
      setRecoveryNotice({ tone: 'success', message: '本地缓存已更新，当前项目已从 SQLite 重新同步。' })
    } catch (reason) {
      setRecoveryNotice({
        tone: 'error',
        message: reason instanceof Error ? reason.message : '重新同步失败，本地缓存未被清除。',
      })
    } finally {
      setRecovering(false)
    }
  }

  const arkConfigured = draft?.ark.apiKeyConfigured ?? false
  const tosConfigured = Boolean(draft?.tos.accessKeyConfigured && draft.tos.secretKeyConfigured && draft.tos.bucket)
  const promptModelOptions = includeConfiguredModel(PROMPT_MODEL_OPTIONS, draft?.ark.promptModel ?? '')
  const runtimeImageModels = runtime?.capabilities.imageModels.filter((option) => option.id.startsWith('doubao-seedream-')) ?? []
  const imageModelOptions = includeConfiguredModel(
    runtimeImageModels.length > 0 ? runtimeImageModels : IMAGE_MODEL_OPTIONS,
    draft?.ark.imageModel ?? '',
  )
  const videoModelOptions = includeConfiguredModel(VIDEO_MODEL_OPTIONS, draft?.ark.videoModel ?? '')

  return <div className="page page--settings">
    <PageHeader
      description="外观、运行环境、服务凭证与数据恢复，分门别类管理。"
      eyebrow="工作台配置"
      title="系统设置"
    />
    <div className="settings-shell">
      <nav aria-label="设置分类" className="settings-nav">
        {([
          { id: 'appearance', label: '外观与模式', description: '主题密度与画面层级', icon: Eye },
          { id: 'runtime', label: '运行环境', description: apiConnected ? '服务端已连接' : '浏览器演示模式', icon: MonitorCog },
          { id: 'providers', label: '服务与凭证', description: apiConnected ? (arkConfigured ? '方舟已配置' : '待配置') : '连接服务端后可用', icon: PlugZap },
          { id: 'data', label: '数据与恢复', description: apiConnected ? '缓存与重新同步' : '演示数据管理', icon: Database },
        ] as const).map(({ id, label, description, icon: Icon }) => (
          <button
            aria-current={settingsSection === id ? 'page' : undefined}
            className={settingsSection === id ? 'active' : ''}
            key={id}
            onClick={() => setSettingsSection(id)}
            type="button"
          >
            <span className="settings-nav__icon"><Icon size={16} /></span>
            <span className="settings-nav__text">
              <strong>{label}</strong>
              <small>{description}</small>
            </span>
            <ChevronRight className="settings-nav__chevron" size={14} />
          </button>
        ))}
      </nav>

      <div className="settings-content" key={settingsSection}>
        {settingsSection === 'appearance' ? (
          <section>
            <div className="section-heading"><div><h2>界面模式</h2></div></div>
            <p className="settings-copy">三种模式共享同一信息架构与交互规则，切换只影响信息密度和画面层级。</p>
            <div className="mode-grid">{modes.map((mode) => <button className={visualMode === mode.id ? 'active' : ''} key={mode.id} onClick={() => { if (visualMode !== mode.id) { setVisualMode(mode.id); notify(`已切换到「${mode.title}」，界面密度与层级已更新。`, 'info') } }}><span className={`mode-preview mode-preview--${mode.id}`}><i /><i /><i /></span><div><strong>{mode.title}</strong><p>{mode.description}</p><small>{mode.tone}</small></div>{visualMode === mode.id ? <em><Check size={14} />当前</em> : null}</button>)}</div>
          </section>
        ) : null}

        {settingsSection === 'runtime' ? (
          <section className="runtime-card">
            <div className="section-heading"><div><h2>本地工作台</h2></div><StatusBadge label={apiConnected ? 'API 已连接' : apiStatus === 'loading' ? '连接中' : '离线回退'} status={apiConnected ? 'APPROVED' : apiStatus === 'loading' ? 'GENERATING' : 'READY'} /></div>
            <dl><div><dt><MonitorCog size={15} />图片模型</dt><dd>{imageLabel}</dd></div><div><dt><MonitorCog size={15} />视频模型</dt><dd>{videoLabel}</dd></div><div><dt><Database size={15} />数据来源</dt><dd>{apiConnected ? 'FastAPI + SQLite' : '浏览器本地存储'}</dd></div><div><dt><ShieldCheck size={15} />持久化流程</dt><dd>{capabilities?.mediaPipeline ? '任务 + 媒体 + 版本 + 导出' : apiConnected ? '读取能力中' : '不可用'}</dd></div></dl>
            <small>未配置真实服务商时仍可走通确定性的模拟流程。保存方舟 API Key 后，文本、图片和视频任务会由后端 Worker 调用真实服务。</small>
          </section>
        ) : null}

        {settingsSection === 'data' ? (
          <div className="settings-stack">
            {apiConnected ? <section className="reset-card">
              <p className="eyebrow">故障恢复</p>
              <h2>重新同步当前项目</h2>
              <p>清除当前项目的浏览器缓存，再从 SQLite 重新读取项目、镜头与任务；不会删除服务端数据。</p>
              <Button onClick={() => { setRecoveryNotice(null); setRecoveryOpen(true) }} variant="secondary"><RotateCcw size={16} />清除本地缓存并重新同步</Button>
              {recoveryNotice ? <small className={`settings-recovery-notice settings-recovery-notice--${recoveryNotice.tone}`} role={recoveryNotice.tone === 'error' ? 'alert' : 'status'}>{recoveryNotice.message}</small> : null}
            </section> : apiStatus === 'mock_fallback' ? <section className="reset-card">
              <p className="eyebrow">离线演示</p>
              <h2>恢复演示项目</h2>
              <p>恢复浏览器中的内置演示场景；此模式没有连接 SQLite。</p>
              <Button onClick={() => { resetDemo(); notify('已恢复内置演示项目。') }} variant="secondary"><RotateCcw size={16} />恢复演示项目</Button>
            </section> : null}
            <section className="reset-card">
              <p className="eyebrow">浏览器本地数据</p>
              <h2>本机保存的内容</h2>
              <p>故事草稿、界面模式偏好、演示项目副本与新手引导标记都只保存在当前浏览器中，不会上传。</p>
              <Button onClick={() => window.dispatchEvent(new Event('studio:show-onboarding'))} variant="secondary"><Eye size={16} />重新观看新手引导</Button>
            </section>
          </div>
        ) : null}

        {settingsSection === 'providers' ? (
          <section className="provider-settings-panel">
            <header className="provider-settings-header">
              <div><h2>服务与凭证</h2><p>集中管理创作模型和媒体存储。设置只保存在本机服务端。</p></div>
              <div className="provider-settings-header__actions">
                <span className={hasProviderChanges ? 'settings-sync-state settings-sync-state--dirty' : 'settings-sync-state'}>
                  <i />{hasProviderChanges ? '有未保存更改' : providerSettings?.storage.updatedAt ? '配置已同步' : '使用环境配置'}
                </span>
                <Button disabled={!draft || !hasProviderChanges || saving || !apiConnected} onClick={() => void saveSettings()}>
                  {saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
                  {saving ? '正在保存' : '保存更改'}
                </Button>
              </div>
            </header>
            {notice ? <div className={`brief-save-message brief-save-message--${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>{notice.message}</div> : null}
            {!draft ? (apiConnected
              ? <div className="provider-settings-loading"><LoaderCircle className="spin" size={20} />正在读取服务端 API 设置…</div>
              : <div className="provider-offline" role="status">
                  <span className="provider-offline__icon"><CloudOff size={22} /></span>
                  <strong>连接服务端后可管理 API 凭证</strong>
                  <p>演示模式下全部生成走模拟流程，无需配置密钥。启动本地服务端后，这里可以接入火山方舟（文本 · 图片 · 视频）与火山 TOS 媒体中转。</p>
                  <div className="provider-offline__how">
                    <strong>如何连接</strong>
                    <ol>
                      <li>在项目根目录运行 <code>docker compose up --build</code>，或 <code>uv run uvicorn app.main:app --port 8000</code></li>
                      <li>刷新本页，右上角状态变为「已连接」</li>
                    </ol>
                  </div>
                </div>
            ) : <div className="api-settings-shell">
              <nav aria-label="API 服务商" className="provider-sidebar">
                <p className="provider-sidebar__label">服务商</p>
                <button aria-pressed={activeProvider === 'ark'} className={activeProvider === 'ark' ? 'active' : ''} onClick={() => setActiveProvider('ark')} type="button">
                  <span className="provider-nav-icon"><PlugZap size={18} /></span>
                  <span><strong>火山方舟</strong><small>文本 · 图片 · 视频</small></span>
                  <span className={arkConfigured ? 'provider-nav-status provider-nav-status--ready' : 'provider-nav-status'}>{arkConfigured ? '已连接' : '未配置'}</span>
                  <ChevronRight size={15} />
                </button>
                <button aria-pressed={activeProvider === 'tos'} className={activeProvider === 'tos' ? 'active' : ''} onClick={() => setActiveProvider('tos')} type="button">
                  <span className="provider-nav-icon"><Cloud size={18} /></span>
                  <span><strong>火山 TOS</strong><small>私有媒体中转</small></span>
                  <span className={tosConfigured && draft.tos.enabled ? 'provider-nav-status provider-nav-status--ready' : 'provider-nav-status'}>{draft.tos.enabled ? tosConfigured ? '已启用' : '待补全' : '未启用'}</span>
                  <ChevronRight size={15} />
                </button>
                <div className="provider-security-note"><LockKeyhole size={18} /><div><strong>本机安全存储</strong><p>密钥不会进入浏览器，只返回掩码和配置状态。</p></div></div>
              </nav>

              <article className="provider-editor">
                <header className="provider-editor__header">
                  <div className="provider-editor__identity">
                    <span className="provider-editor__icon">{activeProvider === 'ark' ? <PlugZap size={20} /> : <Cloud size={20} />}</span>
                    <div><div><h3>{activeProvider === 'ark' ? '火山方舟' : '火山 TOS'}</h3><StatusBadge label={activeProvider === 'ark' ? arkConfigured ? '已配置' : '模拟模式' : draft.tos.enabled ? tosConfigured ? '已启用' : '待补全' : '未启用'} status={activeProvider === 'ark' ? arkConfigured ? 'APPROVED' : 'READY' : draft.tos.enabled ? tosConfigured ? 'APPROVED' : 'FAILED' : 'READY'} /></div><p>{activeProvider === 'ark' ? '统一管理文本创作、Seedream 图片、身份检查与 Seedance 视频。' : '为私有关键帧生成 Seedance 可访问的短期签名地址。'}</p></div>
                  </div>
                  <Button disabled={saving || testing !== null || hasProviderChanges} onClick={() => void testConnection(activeProvider)} variant="secondary">{testing === activeProvider ? <LoaderCircle className="spin" size={15} /> : <PlugZap size={15} />}{hasProviderChanges ? '保存后测试' : '测试连接'}</Button>
                </header>
                <ConnectionResult result={testResults[activeProvider]} />

                {activeProvider === 'ark' ? <>
                  <section className="provider-config-section">
                    <div className="provider-config-section__intro"><KeyRound size={17} /><div><h4>认证凭证</h4><p>用于全部方舟模型请求</p></div></div>
                    <div className="provider-config-section__body"><SecretField clear={clearArkApiKey} configured={draft.ark.apiKeyConfigured} hint={draft.ark.apiKeyHint} label="API Key" onChange={setArkApiKey} onClear={setClearArkApiKey} source={draft.ark.apiKeySource} value={arkApiKey} /></div>
                  </section>
                  <section className="provider-config-section">
                    <div className="provider-config-section__intro"><SlidersHorizontal size={17} /><div><h4>模型路由</h4><p>按任务类型选择模型</p></div></div>
                    <div className="provider-config-section__body api-form-grid api-form-grid--stack">
                      <label className="api-field"><span>文本模型</span><SelectControl aria-label="文本模型" onChange={(event) => updateArk('promptModel', event.target.value)} value={draft.ark.promptModel}>{promptModelOptions.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.id}</option>)}</SelectControl><small>故事、剧本与提示词</small></label>
                      <label className="api-field"><span>图片模型</span><SelectControl aria-label="图片模型" onChange={(event) => updateArk('imageModel', event.target.value)} value={draft.ark.imageModel}>{imageModelOptions.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.id}</option>)}</SelectControl><small>关键帧与角色候选</small></label>
                      <label className="api-field"><span>视频模型</span><SelectControl aria-label="视频模型" onChange={(event) => updateArk('videoModel', event.target.value)} value={draft.ark.videoModel}>{videoModelOptions.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.id}</option>)}</SelectControl><small>单镜头视频生成</small></label>
                    </div>
                  </section>
                  <section className="provider-config-section">
                    <div className="provider-config-section__intro"><ShieldCheck size={17} /><div><h4>质量门禁</h4><p>控制角色一致性审核</p></div></div>
                    <div className="provider-config-section__body api-form-grid api-form-grid--stack">
                      <div className="provider-switch provider-switch--setting"><div><strong>角色一致性自动检查</strong><small>开启后，候选镜头将在应用前自动检查角色一致性。</small></div><label aria-label="角色一致性自动检查"><input checked={draft.ark.identityQcEnabled} onChange={(event) => updateArk('identityQcEnabled', event.target.checked)} type="checkbox" /><span /></label></div>
                      <label className="api-field"><span>自动通过阈值</span><input max="1" min="0.5" onChange={(event) => updateArk('identityAutoPassThreshold', Number(event.target.value))} step="0.01" type="number" value={draft.ark.identityAutoPassThreshold} /><small>{Math.round(draft.ark.identityAutoPassThreshold * 100)}% 及以上自动通过</small></label>
                    </div>
                  </section>
                  <details className="provider-advanced">
                    <summary><span><SlidersHorizontal size={16} />高级设置</span><small>接口地址、超时与轮询</small></summary>
                    <div className="api-form-grid">
                      <label className="api-field api-field--wide"><span>Responses API 地址</span><input onChange={(event) => updateArk('responsesUrl', event.target.value)} type="url" value={draft.ark.responsesUrl} /></label>
                      <label className="api-field api-field--wide"><span>图片生成 API 地址</span><input onChange={(event) => updateArk('imagesUrl', event.target.value)} type="url" value={draft.ark.imagesUrl} /></label>
                      <label className="api-field api-field--wide"><span>视频任务 API 地址</span><input onChange={(event) => updateArk('videoTasksUrl', event.target.value)} type="url" value={draft.ark.videoTasksUrl} /></label>
                      <label className="api-field"><span>请求超时（秒）</span><input min="5" onChange={(event) => updateArk('requestTimeoutSeconds', Number(event.target.value))} type="number" value={draft.ark.requestTimeoutSeconds} /></label>
                      <label className="api-field"><span>轮询间隔（秒）</span><input min="1" onChange={(event) => updateArk('videoPollIntervalSeconds', Number(event.target.value))} type="number" value={draft.ark.videoPollIntervalSeconds} /></label>
                      <label className="api-field"><span>视频超时（秒）</span><input min="30" onChange={(event) => updateArk('videoTimeoutSeconds', Number(event.target.value))} type="number" value={draft.ark.videoTimeoutSeconds} /></label>
                      <label className="api-field"><span>源图直连窗口（秒）</span><input min="60" onChange={(event) => updateArk('sourceUrlFastPathSeconds', Number(event.target.value))} type="number" value={draft.ark.sourceUrlFastPathSeconds} /></label>
                    </div>
                  </details>
          </> : <>
            <section className="provider-config-section">
              <div className="provider-config-section__intro"><Cloud size={17} /><div><h4>媒体中转</h4><p>控制私有存储桶接入</p></div></div>
              <div className="provider-config-section__body"><div className="provider-switch"><div><strong>启用私有媒体中转</strong><small>配置完整后，后续视频任务自动使用短期签名地址。</small></div><label><input checked={draft.tos.enabled} onChange={(event) => updateTos('enabled', event.target.checked)} type="checkbox" /><span /></label></div></div>
            </section>
            <section className="provider-config-section">
              <div className="provider-config-section__intro"><KeyRound size={17} /><div><h4>认证凭证</h4><p>AK、SK 与临时令牌</p></div></div>
              <div className="provider-config-section__body api-form-grid">
                <SecretField clear={clearTosAccessKey} configured={draft.tos.accessKeyConfigured} hint={draft.tos.accessKeyHint} label="Access Key" onChange={setTosAccessKey} onClear={setClearTosAccessKey} source={draft.tos.accessKeySource} value={tosAccessKey} />
                <SecretField clear={clearTosSecretKey} configured={draft.tos.secretKeyConfigured} hint={draft.tos.secretKeyHint} label="Secret Key" onChange={setTosSecretKey} onClear={setClearTosSecretKey} value={tosSecretKey} />
                <SecretField clear={clearTosSecurityToken} configured={draft.tos.securityTokenConfigured} hint={null} label="Security Token" onChange={setTosSecurityToken} onClear={setClearTosSecurityToken} optional value={tosSecurityToken} wide />
              </div>
            </section>
            <section className="provider-config-section">
              <div className="provider-config-section__intro"><HardDrive size={17} /><div><h4>存储位置</h4><p>选择目标桶与地域</p></div></div>
              <div className="provider-config-section__body api-form-grid api-form-grid--three">
                <label className="api-field"><span>Bucket</span><input onChange={(event) => updateTos('bucket', event.target.value)} placeholder="私有存储桶名称" value={draft.tos.bucket} /></label>
                <label className="api-field"><span>Endpoint</span><input onChange={(event) => updateTos('endpoint', event.target.value)} value={draft.tos.endpoint} /></label>
                <label className="api-field"><span>Region</span><input onChange={(event) => updateTos('region', event.target.value)} value={draft.tos.region} /></label>
              </div>
            </section>
            <details className="provider-advanced">
              <summary><span><SlidersHorizontal size={16} />高级设置</span><small>对象生命周期与签名</small></summary>
              <div className="api-form-grid">
                <label className="api-field api-field--wide"><span>对象前缀</span><input onChange={(event) => updateTos('objectPrefix', event.target.value)} value={draft.tos.objectPrefix} /></label>
                <label className="api-field"><span>签名有效期（秒）</span><input min="900" onChange={(event) => updateTos('presignTtlSeconds', Number(event.target.value))} type="number" value={draft.tos.presignTtlSeconds} /></label>
                <label className="api-field"><span>对象过期天数</span><input max="7" min="1" onChange={(event) => updateTos('objectExpiresDays', Number(event.target.value))} type="number" value={draft.tos.objectExpiresDays} /></label>
                <label className="api-field api-field--toggle"><span>任务完成后清理对象</span><input checked={draft.tos.cleanupOnCompletion} onChange={(event) => updateTos('cleanupOnCompletion', event.target.checked)} type="checkbox" /><small>推荐开启，减少临时媒体残留</small></label>
              </div>
            </details>
          </>}

          <footer className="provider-editor__footer"><div><HardDrive size={16} /><span>{providerSettings?.storage.updatedAt ? `上次保存：${new Date(providerSettings.storage.updatedAt).toLocaleString('zh-CN')}` : '当前使用服务端环境配置'}</span></div><small>文件权限 0600 · 浏览器不接收密钥明文</small></footer>
        </article>
      </div>}
    </section>
        ) : null}
      </div>
    </div>
    <Modal
      description="这会放弃尚未写入服务端的浏览器缓存，然后从 SQLite 重新读取当前项目。服务端项目和版本记录不会被删除。"
      footer={<>
        <Button disabled={recovering} onClick={() => setRecoveryOpen(false)} variant="secondary">取消</Button>
        <Button disabled={recovering} onClick={() => void recoverCurrentProject()}>
          {recovering ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />}
          {recovering ? '正在重新同步…' : '确认清除并重新同步'}
        </Button>
      </>}
      onClose={() => { if (!recovering) setRecoveryOpen(false) }}
      open={recoveryOpen}
      title="重新同步当前项目？"
    >
      <div className="project-delete-confirmation">
        <span><AlertTriangle size={20} /></span>
        <div><strong>{project.name}</strong><p>如页面状态异常或与服务端不一致，可使用此操作恢复到 SQLite 中的最新状态。</p></div>
      </div>
    </Modal>
  </div>
}
