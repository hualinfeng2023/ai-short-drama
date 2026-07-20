import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  Check,
  ChevronRight,
  FileText,
  LoaderCircle,
  Paperclip,
  Save,
  Send,
  UploadCloud,
  X,
} from 'lucide-react'
import { useNavigate } from 'react-router'
import { Button, StatusBadge } from '../components/ui'
import { fetchProject, updateProjectDraft, uploadProjectAsset } from '../api/client'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'

const examples = [
  '一名被裁员的产品经理接手旧工作室，在第一位客户上门时发现合伙人的秘密。',
  '两位立场相反的社区工作者，必须在台风登陆前共同完成一次撤离。',
  '暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。',
]

export function NewProjectPage() {
  const { notify } = useToast()
  const [idea, setIdea] = useState(() => localStorage.getItem('drama-draft-idea') ?? '')
  const [mode, setMode] = useState<'chat' | 'template'>('chat')
  const [asset, setAsset] = useState<{
    file: File
    name: string
    size: string
    status: 'SELECTED' | 'UPLOADING' | 'PARSING' | 'READY' | 'FAILED'
    error?: string
  } | null>(null)
  const [assetRightsConfirmed, setAssetRightsConfirmed] = useState(false)
  const [saved, setSaved] = useState(false)
  const [creating, setCreating] = useState(false)
  const [creationError, setCreationError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const idempotencyKeyRef = useRef(crypto.randomUUID())
  const { apiStatus, createProject } = useStudio()
  const navigate = useNavigate()
  const isValid = idea.trim().length >= 10

  useEffect(() => {
    setSaved(false)
    idempotencyKeyRef.current = crypto.randomUUID()
  }, [idea])

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setAsset({
      file,
      name: file.name,
      size: `${Math.max(1, Math.round(file.size / 1024))} KB`,
      status: 'SELECTED',
    })
    setAssetRightsConfirmed(false)
  }

  function saveDraft() {
    localStorage.setItem('drama-draft-idea', idea)
    setSaved(true)
    notify('故事草稿已保存到浏览器。')
  }

  async function confirmProject() {
    if (!isValid || creating) return
    if (apiStatus !== 'connected') {
      setCreationError('项目服务当前不可用。请启动 FastAPI 后刷新页面；本地草稿仍保留。')
      return
    }
    setCreating(true)
    setCreationError(null)
    try {
      const created = await createProject(idea, idempotencyKeyRef.current)
      if (asset) {
        setAsset((current) => current ? { ...current, status: 'UPLOADING', error: undefined } : null)
        const uploaded = await uploadProjectAsset(created.id, asset.file)
        setAsset((current) => current ? { ...current, status: 'PARSING' } : null)
        const latest = await fetchProject(created.id)
        await updateProjectDraft(created.id, {
          expected_version: latest.lockVersion,
          reference_asset_ids: [uploaded.id],
        })
        setAsset((current) => current ? { ...current, status: 'READY' } : null)
      }
      localStorage.removeItem('drama-draft-idea')
      navigate(`/projects/${created.id}`)
    } catch (error) {
      setAsset((current) => current ? {
        ...current,
        status: 'FAILED',
        error: error instanceof Error ? error.message : '素材上传或解析失败',
      } : null)
      setCreationError(error instanceof Error ? error.message : '项目创建失败，请稍后重试。')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="new-project-layout">
      <section className="creation-panel">
        <header className="creation-panel__header">
          <div>
            <p className="eyebrow">新建项目</p>
            <h1>先讲一个你想看到的故事</h1>
            <p>不需要写镜头或模型参数。系统会先生成一份可修改的导演方案。</p>
          </div>
          <div className="segmented" aria-label="创建模式">
            <button className={mode === 'chat' ? 'active' : ''} onClick={() => setMode('chat')}>对话创建</button>
            <button className={mode === 'template' ? 'active' : ''} onClick={() => setMode('template')}>模板创建</button>
          </div>
        </header>

        {mode === 'template' ? (
          <div className="template-picker">
            <p className="eyebrow">内置模板</p>
            {[
              ['都市转折', '一个决定改变人物原本的生活轨迹'],
              ['关系悬念', '两个人都没有说出的事实推动结尾'],
              ['微型惊悚', '一个日常空间里逐步失去安全感'],
            ].map(([title, copy], index) => (
              <button
                key={title}
                onClick={() => {
                  setIdea(examples[index])
                  setMode('chat')
                }}
              >
                <span>0{index + 1}</span><strong>{title}</strong><small>{copy}</small><ChevronRight size={17} />
              </button>
            ))}
          </div>
        ) : (
          <div className="conversation">
            <div className="assistant-message">
              <span className="assistant-avatar"><FileText size={17} /></span>
              <div>
                <strong>我会先替你整理故事方向</strong>
                <p>告诉我：谁遇到了什么变化？你希望观众在最后一秒感受到什么？</p>
              </div>
            </div>

            <div className="brief-examples">
              <span>试试这些开头</span>
              {examples.map((example) => (
                <button key={example} onClick={() => setIdea(example)}>{example}</button>
              ))}
            </div>

            {asset ? (
              <div className="asset-row">
                <span className="asset-row__icon"><FileText size={18} /></span>
                <div><strong>{asset.name}</strong><small>{asset.size} · {asset.status === 'SELECTED' ? '待确认后安全上传' : asset.status === 'UPLOADING' ? '正在流式上传' : asset.status === 'PARSING' ? '正在解析并写入项目简报' : asset.status === 'READY' ? '安全检查与解析完成' : asset.error ?? '上传失败，可重试'}</small></div>
                <StatusBadge status={asset.status === 'FAILED' ? 'FAILED' : asset.status === 'READY' ? 'APPROVED' : asset.status === 'SELECTED' ? 'READY' : 'GENERATING'} label={asset.status === 'FAILED' ? '失败' : asset.status === 'READY' ? '可用' : asset.status === 'SELECTED' ? '待上传' : '处理中'} />
                <Button aria-label="移除素材" disabled={creating} onClick={() => { setAsset(null); setAssetRightsConfirmed(false); if (fileRef.current) fileRef.current.value = '' }} size="sm" variant="ghost"><X size={16} /></Button>
              </div>
            ) : null}
            {asset ? <label className="asset-rights-confirm"><input checked={assetRightsConfirmed} disabled={creating} onChange={(event) => setAssetRightsConfirmed(event.target.checked)} type="checkbox" /><span><Check size={13} /></span><small>我确认对该素材拥有本次创作所需的使用权。</small></label> : null}

            <div className={`composer ${idea.length > 0 ? 'composer--active' : ''}`}>
              <textarea
                aria-label="故事想法"
                onChange={(event) => setIdea(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                    void confirmProject()
                  }
                }}
                placeholder="例如：一名普通人接过旧工作室的钥匙，却发现前任主人留下了未完成的委托……"
                value={idea}
              />
              <div className="composer__footer">
                <div>
                  <input
                    accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp,.mp4"
                    hidden
                    onChange={handleFile}
                    ref={fileRef}
                    type="file"
                  />
                  <Button onClick={() => fileRef.current?.click()} size="sm" variant="ghost">
                    <Paperclip size={16} /> 添加素材
                  </Button>
                  <span className="composer__hint">{idea.trim().length} 字 · 建议 10–200 字 · ⌘ Enter 创建</span>
                </div>
                <Button
                  disabled={!isValid || creating || apiStatus !== 'connected' || Boolean(asset && !assetRightsConfirmed)}
                  onClick={() => void confirmProject()}
                >
                  {creating ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}
                  {creating ? '正在创建项目简报' : '创建项目简报'}
                </Button>
              </div>
            </div>
            {!isValid && idea.length > 0 ? <p className="field-note field-note--warning">再补充一点，至少输入 10 个字。</p> : null}
            {creationError ? <p className="creation-error" role="alert">{creationError}</p> : apiStatus !== 'connected' ? <p className="creation-error" role="status">{apiStatus === 'loading' ? '正在连接项目服务，连接后即可创建项目简报。' : '项目服务不可用；当前只保存浏览器草稿，不会生成正式项目或任务。'}</p> : null}
          </div>
        )}

        <footer className="creation-footer">
          <Button onClick={saveDraft} variant="secondary"><Save size={16} />{saved ? '草稿已保存' : '保存草稿'}</Button>
          <span>故事想法可先保存在浏览器；确认后，项目与项目简报将写入本地 SQLite。</span>
        </footer>
      </section>

      <aside className="proposal-panel" aria-label="创建流程" aria-live="polite">
        <div className="proposal-empty">
          <header className="proposal-empty__header">
            <span><UploadCloud size={21} /></span>
            <div>
              <p className="eyebrow">安全创建流程</p>
              <h2>创建后，系统会做什么？</h2>
            </div>
          </header>
          <p className="proposal-empty__intro">项目与项目简报保存到 SQLite 后，正式方案、任务和版本将统一由服务端生成并持续保存。</p>
          <ol>
            <li><strong>保存创作起点</strong><span>创建项目与不可变的项目简报第 1 版</span></li>
            <li><strong>补全创作要求</strong><span>确认受众、市场、题材、风格、时长与平台</span></li>
            <li><strong>生成导演方案</strong><span>启动可恢复、可追踪的服务端任务</span></li>
          </ol>
          <footer className="proposal-empty__footer">
            <StatusBadge
              status={apiStatus === 'connected' ? 'APPROVED' : apiStatus === 'loading' ? 'GENERATING' : 'BLOCKED'}
              label={apiStatus === 'connected' ? '服务端已连接' : apiStatus === 'loading' ? '正在连接' : '仅可保存本地草稿'}
            />
            <p className="proposal-persistence-note">创建后先进入项目简报确认页；未经确认，不会启动正式故事或媒体任务。</p>
          </footer>
        </div>
      </aside>
    </div>
  )
}
