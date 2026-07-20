import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeftRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Film,
  Layers3,
  Lightbulb,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Save,
  ShieldCheck,
  UserRound,
} from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import {
  enhanceShotPrompt,
  fetchCharacterCandidates,
  fetchRuntimeConfig,
  type RuntimeConfig,
} from '../api/client'
import { Button, FormField, Modal, ProgressBar, SelectControl, StatusBadge, Tab, TabList, Tabs } from '../components/ui'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import { GlossaryTip } from '../components/GlossaryTip'
import type { CharacterRecord, IdentityReviewDecision, IdentityReviewIssue } from '../types'
import { resolveCharacterReferencePreview } from '../utils/characterReferencePreview'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const DEFAULT_IMAGE_MODELS = [
  { id: 'doubao-seedream-5-0-260128', label: 'Seedream 5.0 Pro' },
  { id: 'doubao-seedream-5-0-lite-260128', label: 'Seedream 5.0 Lite' },
  { id: 'doubao-seedream-4-5-251128', label: 'Seedream 4.5' },
  { id: 'doubao-seedream-4-0-250828', label: 'Seedream 4.0' },
]

type ImageResolution = '1K' | '2K' | '3K' | '4K'
type ImageAspectRatio = '1:1' | '4:3' | '3:4' | '16:9' | '9:16' | '3:2' | '2:3' | '21:9'

const IMAGE_RESOLUTIONS_BY_MODEL: Record<string, ImageResolution[]> = {
  'doubao-seedream-5-0-260128': ['1K', '2K'],
  'doubao-seedream-5-0-lite-260128': ['2K', '3K', '4K'],
  'doubao-seedream-4-5-251128': ['2K', '4K'],
  'doubao-seedream-4-0-250828': ['2K', '3K', '4K'],
}

const IMAGE_ASPECT_RATIOS: Array<{ id: ImageAspectRatio; label: string }> = [
  { id: '1:1', label: '正方形' },
  { id: '4:3', label: '横向标准' },
  { id: '3:4', label: '竖向标准' },
  { id: '16:9', label: '横屏宽画幅' },
  { id: '9:16', label: '竖屏短视频' },
  { id: '3:2', label: '横向摄影' },
  { id: '2:3', label: '竖向摄影' },
  { id: '21:9', label: '超宽银幕' },
]

const IDENTITY_ISSUES: Array<{ id: IdentityReviewIssue; label: string; hint: string }> = [
  { id: 'FACE_SHAPE', label: '脸型轮廓', hint: '下颌、颧骨或脸部宽窄不一致' },
  { id: 'FACIAL_FEATURES', label: '五官特征', hint: '眼、鼻、嘴的比例或辨识点有差异' },
  { id: 'HAIR', label: '发型与发色', hint: '发型、发际线或发色不一致' },
  { id: 'AGE_IMPRESSION', label: '年龄感', hint: '看起来比参考角色明显更年轻或年长' },
  { id: 'WARDROBE', label: '服装造型', hint: '服装与当前造型版本不一致' },
  { id: 'BODY_PROPORTIONS', label: '身形比例', hint: '身高、体型或身体比例有差异' },
  { id: 'SIGNATURE_ACCESSORIES', label: '标志性配饰', hint: '眼镜、首饰等识别元素缺失或错误' },
]

const REVIEW_DECISION_LABELS: Record<IdentityReviewDecision, string> = {
  APPROVE_AND_APPLY: '确认一致并已应用',
  REGENERATE: '发现差异，已重新生成',
  OVERRIDE_AND_APPLY: '人工说明后仍然应用',
}

function displayLookVersion(value?: string): string {
  return (value ?? 'Look V1').replace(/^Look\s*V?(\d+)$/i, '造型第 $1 版')
}

function displayActor(value: string): string {
  return value === 'demo-user' ? '演示用户' : value
}

export function ShotWorkspacePage() {
  const { notify } = useToast()
  const { sceneId } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const {
    apiStatus,
    project,
    jobs,
    updateShot,
    generateTake,
    generateVideo,
    reviewCandidateIdentity,
    updateShotCharacterBindings,
  } = useStudio()
  const scene = project.scenes.find((item) => item.id === sceneId) ?? project.scenes[0]
  const sceneShots = useMemo(() => project.shots.filter((shot) => scene.shotIds.includes(shot.id)), [project.shots, scene.shotIds])
  const queryShot = searchParams.get('shot')
  const currentShot = project.shots.find((shot) => shot.id === queryShot) ?? sceneShots[0]
  const [description, setDescription] = useState(currentShot.description)
  const [dialogue, setDialogue] = useState(currentShot.dialogue)
  const [dirty, setDirty] = useState(false)
  const [generateOpen, setGenerateOpen] = useState(false)
  const [videoOpen, setVideoOpen] = useState(false)
  const [videoPrompt, setVideoPrompt] = useState('')
  const [videoImageUrl, setVideoImageUrl] = useState('')
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [selectedImageModel, setSelectedImageModel] = useState('doubao-seedream-5-0-260128')
  const [selectedImageResolution, setSelectedImageResolution] = useState<ImageResolution>('2K')
  const [selectedImageAspectRatio, setSelectedImageAspectRatio] = useState<ImageAspectRatio>(project.aspectRatio)
  const [enhancingDescription, setEnhancingDescription] = useState(false)
  const [descriptionBeforeEnhance, setDescriptionBeforeEnhance] = useState<string | null>(null)
  const [enhanceNote, setEnhanceNote] = useState<string | null>(null)
  const [compareOpen, setCompareOpen] = useState(false)
  const [inspectorTab, setInspectorTab] = useState<'shot' | 'continuity' | 'versions'>('shot')
  const [playing, setPlaying] = useState(false)
  const [characters, setCharacters] = useState<CharacterRecord[]>([])
  const [boundCharacterIds, setBoundCharacterIds] = useState<string[]>(
    currentShot.characterIds ?? [],
  )
  const [lookVersion, setLookVersion] = useState(
    displayLookVersion(currentShot.characterLookVersion),
  )
  const [bindingSaving, setBindingSaving] = useState(false)
  const [identityReviewing, setIdentityReviewing] = useState(false)
  const [identityReviewOpen, setIdentityReviewOpen] = useState(false)
  const [identityIssues, setIdentityIssues] = useState<IdentityReviewIssue[]>([])
  const [identityReviewNote, setIdentityReviewNote] = useState('')
  const [showIdentityOverride, setShowIdentityOverride] = useState(false)
  const [identityReviewError, setIdentityReviewError] = useState<string | null>(null)
  const [bindingNote, setBindingNote] = useState<string | null>(null)
  const runningJob = jobs.find((job) => job.entity.includes(currentShot.id) && job.status === 'RUNNING')
  const activeImageJob = jobs.find((job) =>
    job.entity.includes(currentShot.id)
    && job.jobType === 'GENERATE_SHOT_IMAGE'
    && ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
  )
  const activeVideoJob = jobs.find((job) =>
    job.entity.includes(currentShot.id)
    && job.jobType === 'GENERATE_SHOT_VIDEO'
    && ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
  )
  const imageModels = runtime?.capabilities.imageModels ?? DEFAULT_IMAGE_MODELS
  const imageResolutions = IMAGE_RESOLUTIONS_BY_MODEL[selectedImageModel] ?? ['2K']
  const lockedCharacters = characters.filter((character) => character.lockedCandidateId)
  const boundCharacterCount = currentShot.characterBindings?.length ?? 0
  const bindingsDirty = (
    [...boundCharacterIds].sort().join('|') !== [...(currentShot.characterIds ?? [])].sort().join('|')
    || lookVersion !== displayLookVersion(currentShot.characterLookVersion)
  )
  const referencePreviewUrls = useMemo(() => new Map(
    (currentShot.characterBindings ?? []).map((binding) => [
      binding.id,
      resolveCharacterReferencePreview(project.shots, binding),
    ]),
  ), [currentShot.characterBindings, project.shots])
  const modelLabel = (modelId?: string) => {
    if (!modelId) return '未记录模型'
    return imageModels.find((option) => option.id === modelId)?.label ?? modelId
  }

  function selectImageModel(modelId: string) {
    setSelectedImageModel(modelId)
    const supportedResolutions = IMAGE_RESOLUTIONS_BY_MODEL[modelId] ?? ['2K']
    setSelectedImageResolution((current) => (
      supportedResolutions.includes(current) ? current : supportedResolutions[0]
    ))
  }

  useEffect(() => {
    if (apiStatus !== 'connected') {
      setRuntime(null)
      return
    }
    const controller = new AbortController()
    let retryTimer: ReturnType<typeof setTimeout> | undefined

    const loadRuntime = async (remainingAttempts: number) => {
      try {
        const config = await fetchRuntimeConfig(controller.signal)
        setRuntime(config)
        setSelectedImageModel(config.capabilities.imageModel)
      } catch {
        if (controller.signal.aborted) return
        setRuntime(null)
        if (remainingAttempts > 1) {
          retryTimer = setTimeout(() => void loadRuntime(remainingAttempts - 1), 1000)
        }
      }
    }

    void loadRuntime(3)
    return () => {
      controller.abort()
      if (retryTimer !== undefined) clearTimeout(retryTimer)
    }
  }, [apiStatus])

  useEffect(() => {
    if (apiStatus !== 'connected') {
      setCharacters([])
      return
    }
    const controller = new AbortController()
    void fetchCharacterCandidates(project.id, controller.signal)
      .then(setCharacters)
      .catch(() => setCharacters([]))
    return () => controller.abort()
  }, [apiStatus, project.id])

  useEffect(() => {
    setDescription(currentShot.description)
    setDialogue(currentShot.dialogue)
    setVideoPrompt('')
    setVideoImageUrl('')
    setDescriptionBeforeEnhance(null)
    setEnhanceNote(null)
    setDirty(false)
    setIdentityReviewOpen(false)
    setIdentityIssues([])
    setIdentityReviewNote('')
    setShowIdentityOverride(false)
    setIdentityReviewError(null)
  }, [currentShot.description, currentShot.dialogue, currentShot.id])

  useEffect(() => {
    setBoundCharacterIds(currentShot.characterIds ?? [])
    setLookVersion(displayLookVersion(currentShot.characterLookVersion))
    setBindingNote(null)
  }, [
    currentShot.characterIds?.join('|'),
    currentShot.characterLookVersion,
    currentShot.id,
  ])

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  // ← / → 在同场景镜头间快速切换（输入控件聚焦或弹窗打开时不劫持按键）。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (target?.isContentEditable) return
      if (generateOpen || videoOpen || compareOpen || identityReviewOpen) return
      const index = sceneShots.findIndex((shot) => shot.id === currentShot.id)
      const nextShot = event.key === 'ArrowRight' ? sceneShots[index + 1] : sceneShots[index - 1]
      if (!nextShot) return
      event.preventDefault()
      selectShot(nextShot.id)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  function selectShot(shotId: string, targetSceneId = scene.id) {
    if (dirty && !window.confirm('当前镜头有未保存修改，确定切换吗？')) return
    navigate(`/projects/${project.id}/episodes/${project.episodeId}/scenes/${targetSceneId}?shot=${shotId}`)
  }

  function saveShot() {
    updateShot(currentShot.id, { description, dialogue })
    setDirty(false)
  }

  async function intelligentlyEnhanceDescription() {
    if (enhancingDescription || description.trim().length < 3 || apiStatus !== 'connected') return
    setEnhancingDescription(true)
    setEnhanceNote(null)
    try {
      const result = await enhanceShotPrompt(currentShot.id, description.trim())
      setDescriptionBeforeEnhance(description)
      setDescription(result.enhanced)
      setDirty(true)
      setEnhanceNote(
        result.provider === 'volcengine-ark'
          ? `已由 ${result.model} 智能改写`
          : `已使用本地智能增强 · ${result.warning ?? result.model}`,
      )
    } catch (error) {
      setEnhanceNote(error instanceof Error ? error.message : '智能改写失败，请稍后重试')
    } finally {
      setEnhancingDescription(false)
    }
  }

  function undoDescriptionEnhancement() {
    if (descriptionBeforeEnhance === null) return
    setDescription(descriptionBeforeEnhance)
    setDescriptionBeforeEnhance(null)
    setEnhanceNote('已撤销本次智能改写')
    setDirty(true)
  }

  async function saveCharacterBindings() {
    if (bindingSaving || !bindingsDirty) return
    setBindingSaving(true)
    setBindingNote(null)
    try {
      await updateShotCharacterBindings(currentShot.id, boundCharacterIds, lookVersion)
      setBindingNote('出场角色与造型版本已保存；下次生图会自动使用锁定参考图。')
    } catch (error) {
      setBindingNote(error instanceof Error ? error.message : '保存角色绑定失败')
    } finally {
      setBindingSaving(false)
    }
  }

  async function submitIdentityReview(decision: IdentityReviewDecision) {
    if (identityReviewing) return
    if (decision === 'REGENERATE' && identityIssues.length === 0) {
      setIdentityReviewError('请先勾选至少一项需要调整的地方，系统会把这些要求带入新版本。')
      return
    }
    if (decision === 'OVERRIDE_AND_APPLY' && !identityReviewNote.trim()) {
      setIdentityReviewError('请说明为什么仍然应用这个版本，方便后续追溯。')
      return
    }
    setIdentityReviewing(true)
    setIdentityReviewError(null)
    try {
      await reviewCandidateIdentity(
        currentShot.id,
        decision,
        identityIssues,
        identityReviewNote,
      )
      setIdentityReviewOpen(false)
      setBindingNote(
        decision === 'REGENERATE'
          ? '已根据你标记的差异开始生成新版本；当前版本会继续保留。'
          : `已完成复核，第 ${currentShot.candidateTake ?? ''} 版已成为当前版本。`,
      )
    } catch (error) {
      setIdentityReviewError(error instanceof Error ? error.message : '提交复核结果失败，请稍后重试')
    } finally {
      setIdentityReviewing(false)
    }
  }

  return (
    <div className="shot-workspace">
      <aside className="scene-tree">
        <header><p className="eyebrow">第 1 集 · 验证样片</p><h2>场景与镜头</h2></header>
        <div className="scene-tree__list">
          {project.scenes.map((item) => {
            const open = item.id === scene.id
            return (
              <section className={open ? 'open' : ''} key={item.id}>
                <button onClick={() => selectShot(item.shotIds[0], item.id)}>
                  {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  <span><small>场景 {item.code}</small><strong>{item.title}</strong></span>
                  <em>{item.durationSec} 秒</em>
                </button>
                {open ? (
                  <div>
                    {sceneShots.map((shot) => (
                      <button className={shot.id === currentShot.id ? 'active' : ''} key={shot.id} onClick={() => selectShot(shot.id)}>
                        <span className={`shot-status-dot shot-status-dot--${shot.status.toLowerCase()}`} />
                        <span><small>{shot.code}</small><strong>{shot.title}</strong></span>
                        <StatusBadge status={shot.status} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </section>
            )
          })}
        </div>
        <footer><span><Check size={14} />自动保存已开启</span><small>最后同步：刚刚</small></footer>
      </aside>

      <section className="shot-canvas">
        <header className="shot-toolbar">
          <div><p className="eyebrow">场景 {scene.code} · {scene.title}</p><h1>{currentShot.code} · {currentShot.title}</h1></div>
          <div><StatusBadge status={currentShot.status} />
            <Button disabled={!dirty} onClick={saveShot} size="sm" variant="secondary"><Save size={15} />{dirty ? '保存修改' : '已保存'}</Button>
          </div>
        </header>

        <div className={`player-frame player-frame--${scene.code} ${playing ? 'player-frame--playing' : ''}`}>
          {currentShot.currentVideoUrl
            ? <video className="player-frame__image" controls poster={currentShot.currentImageUrl} src={currentShot.currentVideoUrl} />
            : currentShot.currentImageUrl
              ? <img alt={`${currentShot.code} 当前画面`} className="player-frame__image" src={currentShot.currentImageUrl} />
              : null}
          <div className="player-frame__top"><span>{currentShot.currentVideoUrl ? 'Seedance · 当前版本' : currentShot.currentImageUrl ? `${modelLabel(currentShot.currentImageModel)} · 当前版本` : '分镜 / 平移缩放效果'}</span><span>{currentShot.currentVideoUrl ? '5 秒' : '小样'} · {project.aspectRatio}</span></div>
          <div className="player-frame__copy"><small>{currentShot.location} · {currentShot.timeOfDay}</small><strong>{currentShot.description}</strong>{currentShot.dialogue ? <blockquote>“{currentShot.dialogue}”</blockquote> : null}</div>
          {!currentShot.currentVideoUrl ? <button aria-label={playing ? '暂停当前镜头' : '播放当前镜头'} className="player-button" onClick={() => setPlaying((value) => !value)}>{playing ? <Pause fill="currentColor" size={24} /> : <Play fill="currentColor" size={24} />}</button> : null}
          <div className="player-time"><span>00:26.00</span><div><i style={{ width: `${Math.min(100, currentShot.ordinal * 12)}%` }} /></div><span>01:00.00</span></div>
        </div>

        <div className="filmstrip" aria-label="镜头胶片条">
          {project.shots.map((shot) => (
            <button className={shot.id === currentShot.id ? 'active' : ''} key={shot.id} onClick={() => selectShot(shot.id, shot.sceneId)}>
              <div className={`filmstrip__thumb filmstrip__thumb--${shot.sceneId.slice(-2)}`}>{shot.currentImageUrl ? <img alt="" src={shot.currentImageUrl} /> : null}<span>{shot.code}</span><small>{shot.durationSec} 秒</small></div>
              <strong>{shot.title}</strong>
              <span className={`shot-status-dot shot-status-dot--${shot.status.toLowerCase()}`} />
            </button>
          ))}
        </div>
        <p className="shot-workspace__kbd-hint" aria-hidden="true"><kbd>←</kbd><kbd>→</kbd> 切换镜头</p>

        <section className="versions-panel">
          <div className="section-heading"><div><p className="eyebrow">素材版本</p><h2>版本</h2></div><Button disabled={!currentShot.candidateTake} onClick={() => setCompareOpen(true)} size="sm" variant="ghost"><ArrowLeftRight size={15} />比较版本</Button></div>
          <div className="version-row version-row--current"><span>第 {currentShot.currentTake} 版</span><div><strong>当前可播放版本</strong><small>{currentShot.currentVideoUrl ? 'Seedance 视频' : currentShot.currentImageUrl ? `${modelLabel(currentShot.currentImageModel)} · ${currentShot.currentImageModel ?? '模型未知'}` : '模拟分镜'} · 时间线第 {project.timelineVersion} 版</small></div><StatusBadge status="APPROVED" label="当前版本" /></div>
          {currentShot.candidateTake ? <div className="version-row"><span>第 {currentShot.candidateTake} 版</span><div><strong>待确认的新版本</strong><small>{currentShot.candidateImageModel ? `${modelLabel(currentShot.candidateImageModel)} · ${currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? '请与参考角色对比后决定是否应用' : '未发现明显角色差异'}` : currentShot.status === 'GENERATING' ? `${modelLabel(selectedImageModel)} 正在生成` : '正在检查画面'}</small></div><StatusBadge status={currentShot.candidateIdentityStatus ?? currentShot.status} /></div> : null}
        </section>
      </section>

      <aside className="inspector">
        <Tabs>
          <TabList aria-label="镜头检查器" className="inspector__tabs">
          <Tab className={inspectorTab === 'shot' ? 'active' : ''} onClick={() => setInspectorTab('shot')} selected={inspectorTab === 'shot'}>镜头</Tab>
          <Tab className={inspectorTab === 'continuity' ? 'active' : ''} onClick={() => setInspectorTab('continuity')} selected={inspectorTab === 'continuity'}>
            <GlossaryTip label="连续性" tip="角色与场景的一致性检查：管理出场角色、造型版本与光线等连续性规则，避免镜头之间「穿帮」。" />
            {boundCharacterCount > 0 ? <em className="inspector__tab-badge">{boundCharacterCount}</em> : null}
          </Tab>
          <Tab className={inspectorTab === 'versions' ? 'active' : ''} onClick={() => setInspectorTab('versions')} selected={inspectorTab === 'versions'}>
            <GlossaryTip label="版本" tip="当前版本始终可播放；新生成的画面先作为候选版本，复核通过后才会应用，失败不会覆盖当前版本。" />
            <em className="inspector__tab-badge">{1 + (currentShot.candidateTake ? 1 : 0)}</em>
          </Tab>
          </TabList>
        </Tabs>
        <div className="inspector__body">
          {inspectorTab === 'shot' ? <section>
            <p className="eyebrow">镜头参数</p>
            <details className="params-fold">
              <summary>
                <span>生成参数</span>
                <small>{modelLabel(selectedImageModel)} · {selectedImageResolution} · {selectedImageAspectRatio}</small>
                <ChevronDown className="params-fold__chevron" size={14} />
              </summary>
              <FormField className="field" label="生图模型"><SelectControl aria-label="生图模型" onChange={(event) => selectImageModel(event.target.value)} value={selectedImageModel}>{imageModels.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.id}</option>)}</SelectControl></FormField>
              <div className="field-grid"><FormField label="分辨率"><SelectControl aria-label="分辨率" onChange={(event) => setSelectedImageResolution(event.target.value as ImageResolution)} value={selectedImageResolution}>{imageResolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}</SelectControl></FormField><FormField label="画面比例"><SelectControl aria-label="画面比例" onChange={(event) => setSelectedImageAspectRatio(event.target.value as ImageAspectRatio)} value={selectedImageAspectRatio}>{IMAGE_ASPECT_RATIOS.map((option) => <option key={option.id} value={option.id}>{option.id} · {option.label}</option>)}</SelectControl></FormField></div>
            </details>
            <div className="field-grid"><label>景别<SelectControl aria-label="景别" value={currentShot.shotSize} onChange={(event) => updateShot(currentShot.id, { shotSize: event.target.value as typeof currentShot.shotSize })}><option value="WS">全景（WS）</option><option value="MS">中景（MS）</option><option value="MCU">中近景（MCU）</option><option value="CU">近景（CU）</option></SelectControl></label><label>运动<SelectControl aria-label="镜头运动" value={currentShot.cameraMovement} onChange={(event) => updateShot(currentShot.id, { cameraMovement: event.target.value as typeof currentShot.cameraMovement })}><option value="STATIC">固定镜头</option><option value="PAN">摇镜</option><option value="DOLLY_IN">推镜</option><option value="TRACK">跟拍</option><option value="HANDHELD">手持</option></SelectControl></label></div>
            <label className="field"><span className="field__heading"><span>画面描述</span><span className="field__actions"><button disabled={apiStatus !== 'connected' || enhancingDescription || description.trim().length < 3} onClick={(event) => { event.preventDefault(); void intelligentlyEnhanceDescription() }} type="button">{enhancingDescription ? <LoaderCircle className="spin" size={12} /> : <Lightbulb size={12} />}{enhancingDescription ? '正在优化' : '优化画面描述'}</button>{descriptionBeforeEnhance !== null ? <button onClick={(event) => { event.preventDefault(); undoDescriptionEnhancement() }} type="button"><RotateCcw size={12} />撤销</button> : null}</span></span><textarea onChange={(event) => { setDescription(event.target.value); setDirty(true); setEnhanceNote(null) }} value={description} />{enhanceNote ? <small className="field__note">{enhanceNote}</small> : null}</label>
            <FormField className="field" label="对白" optional><textarea onChange={(event) => { setDialogue(event.target.value); setDirty(true) }} placeholder="无对白" value={dialogue} /></FormField>
          </section> : null}
          {inspectorTab === 'continuity' ? <>
            <section className="continuity-box identity-lock-box">
              <div><ShieldCheck size={16} /><strong>角色参考与造型</strong><StatusBadge status={currentShot.candidateIdentityStatus ?? (boundCharacterCount > 0 ? 'LOCKED' : 'NOT_APPLICABLE')} label={currentShot.candidateIdentityStatus ? undefined : boundCharacterCount > 0 ? `已绑定 ${boundCharacterCount} 位角色` : '未绑定角色'} /></div>
              {(currentShot.characterBindings ?? []).length > 0 ? <div className="identity-reference-list">{currentShot.characterBindings?.map((binding) => <div className="identity-reference" key={binding.id}><img alt={`${binding.name} 项目角色参考`} src={referencePreviewUrls.get(binding.id) ?? binding.referenceAssetUrl} /><span><strong>{binding.name} · {localizeDisplayText(binding.role)}</strong><small>{displayLookVersion(binding.lookVersion)} · 项目角色参考帧</small></span></div>)}</div> : <p className="identity-empty">当前分镜未绑定锁定角色；无人物镜头可保持为空。</p>}
              <div className="identity-binding-heading"><strong>本镜头出场角色</strong><small>勾选画面内可见的所有角色</small></div>
              <div className="identity-binding-list">
                {lockedCharacters.map((character) => <label key={character.id}><input checked={boundCharacterIds.includes(character.id)} onChange={(event) => setBoundCharacterIds((current) => event.target.checked ? [...current, character.id] : current.filter((id) => id !== character.id))} type="checkbox" /><UserRound size={14} /><span>{character.name}<small>{localizeDisplayText(character.role)} · 已锁定参考图</small></span></label>)}
              </div>
              <FormField className="field" label={<GlossaryTip label="本镜头使用的造型版本" tip="同一角色可登记多套服装与造型；生成画面时以所选版本为参考，避免角色形象在镜头之间漂移。" />}><input onChange={(event) => setLookVersion(event.target.value)} value={lookVersion} /></FormField>
              <Button disabled={!bindingsDirty || bindingSaving || currentShot.status === 'GENERATING'} onClick={() => void saveCharacterBindings()} size="sm" variant="secondary">{bindingSaving ? <LoaderCircle className="spin" size={14} /> : <Save size={14} />}保存角色与造型</Button>
              {currentShot.candidateIdentityMessage ? <small className={currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? 'warning' : ''}>{currentShot.candidateIdentityScore === undefined ? '' : `角色相似度 ${Math.round(currentShot.candidateIdentityScore * 100)}% · `}{currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? '系统发现可能存在差异，请对照参考图确认。' : '系统未发现明显差异。'}</small> : <small>生成新画面时会自动参考已锁定的角色图和当前造型版本。</small>}
              {currentShot.latestIdentityReview ? <div className="identity-review-record" role="status"><Check size={14} /><span><strong>{REVIEW_DECISION_LABELS[currentShot.latestIdentityReview.decision]}</strong><small>{displayActor(currentShot.latestIdentityReview.actor)} · {new Date(currentShot.latestIdentityReview.reviewedAt).toLocaleString('zh-CN')}{currentShot.latestIdentityReview.lookVersion ? ` · ${displayLookVersion(currentShot.latestIdentityReview.lookVersion)}` : ''}</small>{currentShot.latestIdentityReview.issues.length > 0 ? <small>标记差异：{currentShot.latestIdentityReview.issues.map((id) => IDENTITY_ISSUES.find((issue) => issue.id === id)?.label ?? id).join('、')}</small> : null}{currentShot.latestIdentityReview.note ? <small>说明：{currentShot.latestIdentityReview.note}</small> : null}</span></div> : null}
              {bindingNote ? <small>{bindingNote}</small> : null}
            </section>
            <section className="continuity-box"><div><Layers3 size={16} /><strong>场景连续性</strong><StatusBadge status={currentShot.continuity === 'RISK' ? 'PENDING_REVIEW' : 'APPROVED'} label={currentShot.continuity === 'RISK' ? '需确认' : '结构通过'} /></div><ul><li className={currentShot.continuity === 'RISK' ? 'warning' : ''}>{currentShot.continuity === 'RISK' ? <AlertTriangle size={14} /> : <Check size={14} />}光线方向 · 规则估算</li></ul></section>
            <section className="suggestion-box"><div><Lightbulb size={16} /><strong>镜头建议</strong></div><p>将推进速度降低 12%，让动作落在对白前；预计增加 0.6 秒。</p><button onClick={() => { setDescription(`${description} 镜头推进稍慢，先落动作再进入对白。`); setDirty(true); setInspectorTab('shot') }}>接受并写入描述</button></section>
          </> : null}
          {inspectorTab === 'versions' ? <section className="inspector-version-summary">
            <p className="eyebrow">当前版本</p>
            <h2>第 {currentShot.currentTake} 版始终保持可播放</h2>
            <p>候选版本只有在成功并通过质量检查后才能应用；失败不会覆盖当前时间线。</p>
            <Button disabled={!currentShot.candidateTake} onClick={() => setCompareOpen(true)} variant="secondary"><ArrowLeftRight size={16} />比较当前与候选</Button>
          </section> : null}
          <div className="inspector-actions"><Button disabled={Boolean(activeImageJob)} onClick={() => setGenerateOpen(true)}><Layers3 size={16} />{activeImageJob ? '图片生成中' : currentShot.candidateTake ? `生成第 ${currentShot.candidateTake + 1} 版` : `生成第 ${currentShot.currentTake + 1} 版`}</Button><Button disabled={Boolean(activeVideoJob)} onClick={() => setVideoOpen(true)} variant="secondary"><Film size={16} />{activeVideoJob ? '视频生成中' : '生成动态视频（可选）'}</Button>{currentShot.candidateTake && currentShot.status === 'PENDING_REVIEW' ? <Button disabled={identityReviewing} onClick={() => setIdentityReviewOpen(true)} variant="secondary"><ShieldCheck size={16} />复核并决定是否应用第 {currentShot.candidateTake} 版</Button> : null}</div>
        </div>
      </aside>

      <footer className="generation-dock"><div><span className={runningJob ? 'generation-dock__pulse' : ''}>{runningJob ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}</span><div><strong>{runningJob ? localizeDisplayText(runningJob.label) : '当前没有运行中的镜头任务'}</strong><small>{runningJob ? localizeDisplayText(runningJob.stage) : '当前版本可安全播放'}</small></div></div>{runningJob ? <div className="generation-dock__progress"><ProgressBar value={runningJob.progress} /><span><Clock3 size={14} />约 {runningJob.estimatedSeconds} 秒</span></div> : <Link to={`/tasks?project=${project.id}`}>查看历史任务 <ChevronRight size={15} /></Link>}</footer>

      <Modal open={generateOpen} onClose={() => setGenerateOpen(false)} title={`生成 ${currentShot.code} 的新版本`} description="新画面会作为待确认版本生成；你确认采用前，当前版本不会改变。" footer={<><Button onClick={() => setGenerateOpen(false)} variant="secondary">取消</Button><Button onClick={() => { generateTake(currentShot.id, { model: selectedImageModel, resolution: selectedImageResolution, aspectRatio: selectedImageAspectRatio }); setGenerateOpen(false); notify(`${currentShot.code} 新版本生成任务已提交，当前版本不受影响。`) }}><Film size={16} />开始生成 · 48 积分</Button></>}><label className="field"><span>生成模型</span><SelectControl aria-label="生成模型" onChange={(event) => selectImageModel(event.target.value)} value={selectedImageModel}>{imageModels.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.id}</option>)}</SelectControl></label><div className="field-grid"><label>分辨率<SelectControl aria-label="生成分辨率" onChange={(event) => setSelectedImageResolution(event.target.value as ImageResolution)} value={selectedImageResolution}>{imageResolutions.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}</SelectControl></label><label>画面比例<SelectControl aria-label="生成画面比例" onChange={(event) => setSelectedImageAspectRatio(event.target.value as ImageAspectRatio)} value={selectedImageAspectRatio}>{IMAGE_ASPECT_RATIOS.map((option) => <option key={option.id} value={option.id}>{option.id} · {option.label}</option>)}</SelectControl></label></div><div className="impact-list"><span><Film size={16} /><div><strong>生成方式</strong><p>{modelLabel(selectedImageModel)} · {selectedImageModel}</p></div></span><span><Layers3 size={16} /><div><strong>沿用的角色</strong><p>{(currentShot.characterBindings ?? []).length > 0 ? currentShot.characterBindings?.map((binding) => `${binding.name} · ${displayLookVersion(binding.lookVersion)}`).join('、') : '当前镜头没有绑定角色'}</p></div></span><span><Layers3 size={16} /><div><strong>画面规格</strong><p>{selectedImageResolution} · {selectedImageAspectRatio}</p></div></span><span><RotateCcw size={16} /><div><strong>当前版本不变</strong><p>新画面会单独保存为第 {(currentShot.candidateTake ?? currentShot.currentTake) + 1} 版；生成失败也不会影响第 {currentShot.currentTake} 版。</p></div></span></div></Modal>

      <Modal open={videoOpen} onClose={() => setVideoOpen(false)} title={`生成 ${currentShot.code} 的 5 秒视频`} description={`基于第 ${currentShot.candidateTake ?? currentShot.currentTake} 版素材创建可选的 Seedance 图生视频任务。`} footer={<><Button onClick={() => setVideoOpen(false)} variant="secondary">取消</Button><Button onClick={() => { generateVideo(currentShot.id, videoPrompt, videoImageUrl); setVideoOpen(false); notify(`${currentShot.code} 视频生成任务已提交。`) }}><Film size={16} />提交视频任务</Button></>}><div className="video-generation-form"><label className="field"><span>运动描述（可选）</span><textarea onChange={(event) => setVideoPrompt(event.target.value)} placeholder="无人机高速穿越场景，保持人物和构图稳定……" value={videoPrompt} /></label><label className="field"><span>公网源图地址</span><input onChange={(event) => setVideoImageUrl(event.target.value)} placeholder="https://…" type="url" value={videoImageUrl} /></label><small>此可选能力要求服务端配置 ARK_API_KEY，并提供公网可访问的 HTTPS 源图；完整的模拟流程不依赖它。</small></div></Modal>

      <Modal
        className="modal--identity-review"
        description={`对照已锁定的角色参考，决定是否让第 ${currentShot.candidateTake ?? ''} 版替换当前版本。`}
        footer={<>
          <Button disabled={identityReviewing} onClick={() => { setIdentityReviewOpen(false); setInspectorTab('continuity') }} variant="ghost">调整角色参考或造型</Button>
          <Button disabled={identityReviewing || identityIssues.length === 0} onClick={() => void submitIdentityReview('REGENERATE')} variant="secondary">{identityReviewing ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}按标记差异重新生成</Button>
          <Button disabled={identityReviewing} onClick={() => void submitIdentityReview('APPROVE_AND_APPLY')}>{identityReviewing ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}一致并应用第 {currentShot.candidateTake} 版</Button>
        </>}
        onClose={() => { if (!identityReviewing) setIdentityReviewOpen(false) }}
        open={identityReviewOpen}
        title="复核角色一致性"
      >
        <div className="identity-review">
          <section className="identity-review__comparison" aria-label="角色参考与版本对比">
            <article>
              <div className="identity-review__label"><span>1</span><strong>锁定的角色参考</strong></div>
              <div className="identity-review__references">
                {(currentShot.characterBindings ?? []).length > 0
                  ? currentShot.characterBindings?.map((binding) => <figure key={binding.id}><img alt={`${binding.name} 的项目角色参考图`} src={referencePreviewUrls.get(binding.id) ?? binding.referenceAssetUrl} /><figcaption><strong>{binding.name}</strong><small>{displayLookVersion(binding.lookVersion)} · 项目参考帧</small></figcaption></figure>)
                  : <div className="identity-review__empty"><UserRound size={22} /><span>当前镜头没有绑定角色</span></div>}
              </div>
            </article>
            <article>
              <div className="identity-review__label"><span>2</span><strong>当前使用第 {currentShot.currentTake} 版</strong></div>
              <div className="identity-review__frame">{currentShot.currentImageUrl ? <img alt={`当前使用的第 ${currentShot.currentTake} 版`} src={currentShot.currentImageUrl} /> : <span>当前版本没有预览图</span>}</div>
            </article>
            <article>
              <div className="identity-review__label"><span>3</span><strong>准备应用第 {currentShot.candidateTake} 版</strong></div>
              <div className="identity-review__frame identity-review__frame--candidate">{currentShot.candidateImageUrl ? <img alt={`准备应用的第 ${currentShot.candidateTake} 版`} src={currentShot.candidateImageUrl} /> : <span>新版本预览还未就绪</span>}</div>
            </article>
          </section>

          <section className={`identity-review__signal ${currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? 'is-warning' : 'is-clear'}`} role="status">
            {currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? <AlertTriangle size={18} /> : <Check size={18} />}
            <div><strong>{currentShot.candidateIdentityStatus === 'REVIEW_REQUIRED' ? '系统发现角色可能存在差异' : '系统未发现明显角色差异'}</strong><p>{currentShot.candidateIdentityScore === undefined ? '请以锁定参考图为准做最终判断。' : `角色相似度 ${Math.round(currentShot.candidateIdentityScore * 100)}%。请重点对照脸型、五官、发型和当前造型。`}</p></div>
          </section>

          <section className="identity-review__issues">
            <div><h3>如果不一致，哪里需要调整？</h3><p>勾选的内容会直接加入下一版生成要求。</p></div>
            <div className="identity-review__issue-grid">
              {IDENTITY_ISSUES.map((issue) => <label className={identityIssues.includes(issue.id) ? 'selected' : ''} key={issue.id}><input checked={identityIssues.includes(issue.id)} onChange={(event) => setIdentityIssues((current) => event.target.checked ? [...current, issue.id] : current.filter((item) => item !== issue.id))} type="checkbox" /><span><strong>{issue.label}</strong><small>{issue.hint}</small></span></label>)}
            </div>
          </section>

          <label className="identity-review__note"><span>补充说明（选填）</span><textarea onChange={(event) => setIdentityReviewNote(event.target.value)} placeholder="例如：脸型基本一致，但刘海需要更贴近参考图……" value={identityReviewNote} /></label>
          {identityReviewError ? <p className="identity-review__error" role="alert"><AlertTriangle size={15} />{identityReviewError}</p> : null}

          <section className="identity-review__override">
            <button onClick={() => setShowIdentityOverride((current) => !current)} type="button">{showIdentityOverride ? <ChevronDown size={15} /> : <ChevronRight size={15} />}仍然应用此版本（人工覆盖）</button>
            {showIdentityOverride ? <div><p>只有在你确认差异不会影响角色辨识时使用。系统会保存你的说明和操作时间。</p><Button disabled={identityReviewing || !identityReviewNote.trim()} onClick={() => void submitIdentityReview('OVERRIDE_AND_APPLY')} size="sm" variant="danger">说明原因并仍然应用第 {currentShot.candidateTake} 版</Button></div> : null}
          </section>
        </div>
      </Modal>

      <Modal open={compareOpen} onClose={() => setCompareOpen(false)} title="版本比较" description={`${currentShot.code} · 当前第 ${currentShot.currentTake} 版与候选第 ${currentShot.candidateTake ?? currentShot.currentTake} 版`} footer={<Button onClick={() => setCompareOpen(false)}>完成比较</Button>}>
        <div className="compare-grid">
          <div><span>当前第 {currentShot.currentTake} 版</span><div className={`compare-frame compare-frame--${scene.code}`} style={currentShot.currentImageUrl ? { backgroundImage: `linear-gradient(180deg, transparent, rgb(0 0 0 / 0.72)), url(${currentShot.currentImageUrl})` } : undefined}>{currentShot.currentVideoUrl ? <video controls poster={currentShot.currentImageUrl} src={currentShot.currentVideoUrl} /> : null}<strong>当前可播放版本</strong><small>已用于时间线第 {project.timelineVersion} 版</small></div></div>
          <div><span>候选第 {currentShot.candidateTake ?? currentShot.currentTake} 版</span><div className={`compare-frame compare-frame--${scene.code}`} style={currentShot.candidateImageUrl ? { backgroundImage: `linear-gradient(180deg, transparent, rgb(0 0 0 / 0.72)), url(${currentShot.candidateImageUrl})` } : undefined}>{currentShot.candidateVideoUrl ? <video controls poster={currentShot.candidateImageUrl} src={currentShot.candidateVideoUrl} /> : null}<strong>{currentShot.candidateVideoUrl ? 'Seedance 视频结果' : currentShot.candidateImageUrl ? modelLabel(currentShot.candidateImageModel) : '正在生成'}</strong><small>{currentShot.candidateVideoUrl ? '5 秒 · 待应用' : currentShot.candidateImageUrl ? `${currentShot.candidateImageModel ?? '模型未知'} · 待应用` : '结果就绪后可比较'}</small></div></div>
        </div>
      </Modal>
    </div>
  )
}
