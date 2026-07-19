import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Clapperboard,
  Database,
  FilePenLine,
  ListChecks,
  ListPlus,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
} from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  ApiError,
  approveDirectorProposal,
  fetchBriefVersions,
  fetchDirectorProposals,
  fetchProject,
  fetchWorkspace,
  generateStoryDirections,
  rewriteBriefStory,
  suggestBriefAvoidances,
  suggestBriefRequirements,
  suggestProjectName,
  updateProjectDraft,
} from '../api/client'
import { Button, PageHeader, SelectControl, StatusBadge } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import type {
  BriefVersionRecord,
  DirectorProposal,
  EmotionalReward,
  NarrativeProtagonist,
  ProductionFormat,
  ProjectRecord,
  TargetAudience,
} from '../types'
import { recommendGenre } from '../utils/briefTargetingRecommendation'
import {
  getGenerativeRevealCharacterCount,
  getGenerativeRevealDuration,
  getGenerativeRevealProgress,
} from '../utils/generativeTextMotion'
import { recommendVisualStyle } from '../utils/visualStyleRecommendation'
import {
  EMOTIONAL_REWARD_OPTIONS,
  NARRATIVE_PROTAGONIST_OPTIONS,
  PRODUCTION_FORMAT_OPTIONS,
  TARGET_AUDIENCE_OPTIONS,
  TOPIC_SLATE_MIX,
  narrativeTargetingMissing,
} from '../utils/narrativeTargeting'
type GenreOption = readonly [value: string, label: string]

const GENRE_OPTION_GROUPS: ReadonlyArray<{
  label: string
  options: ReadonlyArray<GenreOption>
}> = [
  {
    label: '都市',
    options: [
      ['urban_drama', '都市情感'],
      ['urban_romance', '都市甜宠'],
      ['urban_suspense', '都市悬疑'],
      ['family_drama', '家庭伦理'],
      ['workplace', '职场故事'],
    ],
  },
  {
    label: '古装',
    options: [
      ['costume_romance', '古装爱情'],
      ['costume_intrigue', '古装权谋'],
    ],
  },
  {
    label: '强情节',
    options: [
      ['revenge', '复仇逆袭'],
      ['action_crime', '动作犯罪'],
      ['suspense', '悬疑'],
    ],
  },
  {
    label: '类型',
    options: [
      ['comedy', '喜剧'],
      ['fantasy', '奇幻'],
      ['sci_fi', '科幻'],
    ],
  },
]
const GENRE_OPTIONS = GENRE_OPTION_GROUPS.flatMap((group) => group.options)
const VISUAL_STYLE_OPTIONS = [
  ['realistic_cinematic', '写实电影感'],
  ['premium_commercial', '高级商业广告感'],
  ['warm_healing', '温暖治愈'],
  ['dark_suspense', '悬疑暗调'],
  ['documentary', '纪录片质感'],
  ['handheld_realism', '手持纪实'],
  ['retro_film', '复古胶片'],
  ['cyberpunk', '赛博朋克'],
  ['fantasy_epic', '奇幻史诗'],
  ['anime_2d', '二维动画'],
  ['chinese_ink', '东方水墨'],
  ['high_saturation_comic', '高饱和漫画感'],
] as const
const MARKET_OPTIONS = [['CN', '中国大陆'], ['SG', '新加坡'], ['MY', '马来西亚'], ['US', '美国'], ['GB', '英国']] as const
const LANGUAGE_OPTIONS = [['zh-CN', '简体中文'], ['en-SG', '英语（新加坡）'], ['ms-MY', '马来语'], ['en-US', '英语（美国）']] as const
const PLATFORM_OPTIONS = [['douyin', '抖音'], ['kuaishou', '快手'], ['reels', 'Instagram Reels'], ['youtube_shorts', 'YouTube Shorts']] as const
const IDEA_GENERATION_STAGES = [
  { title: '正在理解人物与冲突', detail: '保留当前原文，生成完成前不会覆盖。' },
  { title: '正在校准情绪与节奏', detail: '梳理钩子、转折和人物行动逻辑。' },
  { title: '正在组织可拍摄叙事', detail: '把创意收束成清晰、连续的故事表达。' },
] as const
type IdeaGenerationPhase = 'idle' | 'thinking' | 'writing'
type BriefSectionKey = 'audience' | 'delivery' | 'guardrails'

interface BriefForm {
  name: string
  idea: string
  genre: string
  style: string
  targetDurationSec: number
  aspectRatio: '9:16' | '16:9'
  targetPlatform: string
  secondaryPlatforms: string[]
  narrativeProtagonist: NarrativeProtagonist
  targetAudience: TargetAudience
  emotionalRewards: EmotionalReward[]
  audienceProfile: string
  productionFormat: ProductionFormat
  primaryMarket: string
  secondaryMarkets: string[]
  canonicalLanguage: string
  localizationTargets: string[]
  contentRequirements: string
  contentAvoidances: string
  blockingQuestions: string
}

function BriefDisclosure({
  children,
  description,
  meta,
  onToggle,
  open,
  title,
}: {
  children: ReactNode
  description: string
  meta: string
  onToggle: () => void
  open: boolean
  title: string
}) {
  return (
    <section className={`brief-disclosure${open ? ' is-open' : ''}`}>
      <button
        aria-expanded={open}
        className="brief-disclosure__trigger"
        onClick={onToggle}
        type="button"
      >
        <span>
          <strong>{title}</strong>
          <small>{description}</small>
        </span>
        <span className="brief-disclosure__meta">
          {meta}
          <ChevronDown aria-hidden="true" size={16} />
        </span>
      </button>
      {open ? <div className="brief-disclosure__content brief-form-grid">{children}</div> : null}
    </section>
  )
}

function toForm(project: ProjectRecord, brief?: BriefVersionRecord): BriefForm {
  return {
    name: project.name,
    idea: project.idea,
    genre: project.genre,
    style: project.style,
    targetDurationSec: project.targetDurationSec,
    aspectRatio: project.aspectRatio,
    targetPlatform: brief?.targetPlatform ?? project.targetPlatform,
    secondaryPlatforms: brief?.platformTargets
      ?.filter((target) => target.priority === 'SECONDARY')
      .map((target) => target.platform) ?? [],
    narrativeProtagonist: brief?.narrativeProtagonist ?? 'unspecified',
    targetAudience: brief?.targetAudience ?? 'general',
    emotionalRewards: brief?.emotionalRewards ?? [],
    audienceProfile: brief?.audienceProfile ?? '',
    productionFormat: brief?.productionFormat ?? 'live_action',
    primaryMarket: brief?.primaryMarket ?? 'CN',
    secondaryMarkets: brief?.secondaryMarkets ?? [],
    canonicalLanguage: brief?.canonicalLanguage ?? 'zh-CN',
    localizationTargets: brief?.localizationTargets ?? [],
    contentRequirements: brief?.contentRequirements?.join('\n') ?? '',
    contentAvoidances: brief?.contentAvoidances?.join('\n') ?? '',
    blockingQuestions: brief?.blockingQuestions?.join('\n') ?? '',
  }
}

function splitLines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean)
}

function fitStoryIdeaTextarea(textarea: HTMLTextAreaElement): void {
  const styles = window.getComputedStyle(textarea)
  const minHeight = Number.parseFloat(styles.minHeight) || 200
  const maxAutoHeight = 280

  textarea.style.height = 'auto'
  const contentHeight = textarea.scrollHeight
  textarea.style.height = `${Math.min(Math.max(contentHeight, minHeight), maxAutoHeight)}px`
  textarea.style.overflowY = contentHeight > maxAutoHeight ? 'auto' : 'hidden'
}

export function ProjectBriefPage() {
  const { projectId } = useParams()
  const { apiStatus, project: activeProject, activateProject, refreshProjects } = useStudio()
  const navigate = useNavigate()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [form, setForm] = useState<BriefForm | null>(null)
  const [baselineForm, setBaselineForm] = useState<BriefForm | null>(null)
  const [briefVersion, setBriefVersion] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [suggestingName, setSuggestingName] = useState(false)
  const [nameBeforeSuggestion, setNameBeforeSuggestion] = useState<string | null>(null)
  const [nameSuggestionNote, setNameSuggestionNote] = useState<string | null>(null)
  const [rewritingIdea, setRewritingIdea] = useState(false)
  const [ideaBeforeRewrite, setIdeaBeforeRewrite] = useState<string | null>(null)
  const [ideaRewriteNote, setIdeaRewriteNote] = useState<string | null>(null)
  const [ideaGenerationPhase, setIdeaGenerationPhase] = useState<IdeaGenerationPhase>('idle')
  const [ideaGenerationStage, setIdeaGenerationStage] = useState(0)
  const [ideaGenerationProgress, setIdeaGenerationProgress] = useState(0)
  const [draftingRequirements, setDraftingRequirements] = useState(false)
  const [requirementsBeforeDraft, setRequirementsBeforeDraft] = useState<string | null>(null)
  const [requirementsDraftNote, setRequirementsDraftNote] = useState<string | null>(null)
  const [draftingAvoidances, setDraftingAvoidances] = useState(false)
  const [avoidancesBeforeDraft, setAvoidancesBeforeDraft] = useState<string | null>(null)
  const [avoidancesDraftNote, setAvoidancesDraftNote] = useState<string | null>(null)
  const [approving, setApproving] = useState(false)
  const [assumptionsConfirmed, setAssumptionsConfirmed] = useState(false)
  const [proposal, setProposal] = useState<DirectorProposal | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openBriefSection, setOpenBriefSection] = useState<BriefSectionKey | null>(null)
  const storyIdeaRef = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    if (storyIdeaRef.current) fitStoryIdeaTextarea(storyIdeaRef.current)
  }, [form?.idea])

  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    let active = true
    setLoading(true)
    setError(null)

    const loadBriefData = async () => {
      const retryDelays = [0, 350, 900]
      let lastError: unknown

      for (const delay of retryDelays) {
        if (delay > 0) {
          await new Promise<void>((resolve) => {
            const timeout = window.setTimeout(resolve, delay)
            controller.signal.addEventListener('abort', () => {
              window.clearTimeout(timeout)
              resolve()
            }, { once: true })
          })
        }
        if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError')

        try {
          return await Promise.all([
            fetchProject(projectId, controller.signal),
            fetchDirectorProposals(projectId, controller.signal),
            fetchBriefVersions(projectId, controller.signal),
          ])
        } catch (reason) {
          if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
          lastError = reason
          const retryable = !(reason instanceof ApiError)
            || reason.status === 408
            || reason.status === 429
            || reason.status >= 500
          if (!retryable) throw reason
        }
      }

      throw lastError
    }

    loadBriefData()
      .then(([result, proposals, briefs]) => {
        if (!active) return
        const persistedForm = toForm(result, briefs[0])
        const suggestedGenre = recommendGenre(persistedForm.idea)
        const canApplySmartDefaults = result.status === 'DRAFT' || result.status === 'PROPOSAL_READY'
        const smartGenre = canApplySmartDefaults && persistedForm.genre === 'urban_drama'
          ? suggestedGenre
          : persistedForm.genre
        const nextForm = {
          ...persistedForm,
          genre: smartGenre,
        }
        setProject(result)
        setForm(nextForm)
        setBaselineForm(persistedForm)
        setBriefVersion(briefs[0]?.version ?? null)
        setProposal(proposals[0] ?? null)
        const missingTargeting = narrativeTargetingMissing(
          persistedForm.narrativeProtagonist,
          persistedForm.emotionalRewards,
        )
        setOpenBriefSection(
          missingTargeting.length
            ? 'audience'
            : splitLines(persistedForm.blockingQuestions).length
              ? 'guardrails'
              : null,
        )
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        if (!active) return
        setError(reason instanceof Error ? reason.message : '项目读取失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      controller.abort()
    }
  }, [projectId])

  useEffect(() => {
    if (!rewritingIdea || ideaGenerationPhase !== 'thinking') return
    const interval = window.setInterval(() => {
      setIdeaGenerationStage((current) => (current + 1) % IDEA_GENERATION_STAGES.length)
    }, 1100)
    return () => window.clearInterval(interval)
  }, [ideaGenerationPhase, rewritingIdea])

  const editable = project?.status === 'DRAFT' || project?.status === 'PROPOSAL_READY'
  const dirty = useMemo(() => {
    if (!form || !baselineForm) return false
    return JSON.stringify(form) !== JSON.stringify(baselineForm)
  }, [baselineForm, form])

  function updateField<K extends keyof BriefForm>(key: K, value: BriefForm[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current)
    setNotice(null)
    setError(null)
  }

  function updateGenre(value: string) {
    updateField('genre', value)
    setNotice(null)
    setError(null)
  }

  function toggleSelection(
    key: 'secondaryPlatforms' | 'secondaryMarkets' | 'localizationTargets',
    value: string,
  ) {
    setForm((current) => {
      if (!current) return current
      const values = current[key]
      return {
        ...current,
        [key]: values.includes(value)
          ? values.filter((item) => item !== value)
          : [...values, value],
      }
    })
    setNotice(null)
    setError(null)
  }

  function toggleEmotionalReward(value: EmotionalReward) {
    setForm((current) => {
      if (!current) return current
      return {
        ...current,
        emotionalRewards: current.emotionalRewards.includes(value)
          ? current.emotionalRewards.filter((item) => item !== value)
          : [...current.emotionalRewards, value],
      }
    })
    setNotice(null)
    setError(null)
  }

  function toggleBriefSection(section: BriefSectionKey) {
    setOpenBriefSection((current) => current === section ? null : section)
  }

  async function save() {
    if (!projectId || !project || !form || !editable || !dirty || saving || rewritingIdea || draftingRequirements || draftingAvoidances) return
    setSaving(true)
    setNotice(null)
    setError(null)
    try {
      const result = await updateProjectDraft(projectId, {
        expected_version: project.lockVersion,
        name: form.name,
        idea: form.idea,
        genre: form.genre,
        style: form.style,
        target_duration_sec: form.targetDurationSec,
        aspect_ratio: form.aspectRatio,
        target_platform: form.targetPlatform,
        narrative_protagonist: form.narrativeProtagonist,
        target_audience: form.targetAudience,
        emotional_rewards: form.emotionalRewards,
        audience_profile: form.audienceProfile,
        production_format: form.productionFormat,
        primary_market: form.primaryMarket,
        secondary_markets: form.secondaryMarkets,
        canonical_language: form.canonicalLanguage,
        localization_targets: form.localizationTargets,
        platform_targets: [
          {
            platform: form.targetPlatform,
            priority: 'PRIMARY',
            aspect_ratio: form.aspectRatio,
            target_duration_sec: form.targetDurationSec,
            caption_mode: 'BOTH',
          },
          ...form.secondaryPlatforms.map((platform) => ({
            platform,
            priority: 'SECONDARY' as const,
            aspect_ratio: form.aspectRatio,
            target_duration_sec: form.targetDurationSec,
            caption_mode: 'BOTH' as const,
          })),
        ],
        content_requirements: splitLines(form.contentRequirements),
        content_avoidances: splitLines(form.contentAvoidances),
        blocking_questions: splitLines(form.blockingQuestions),
      })
      setProject(result.project)
      setForm(form)
      setBaselineForm(form)
      setNameBeforeSuggestion(null)
      setNameSuggestionNote(null)
      setIdeaBeforeRewrite(null)
      setIdeaRewriteNote(null)
      setRequirementsBeforeDraft(null)
      setRequirementsDraftNote(null)
      setAvoidancesBeforeDraft(null)
      setAvoidancesDraftNote(null)
      setBriefVersion(result.briefVersion)
      setNotice(`项目简报第 ${result.briefVersion} 版已保存，项目版本更新为第 ${result.project.lockVersion} 版。`)
      try {
        await refreshProjects()
      } catch {
        setNotice(`项目简报第 ${result.briefVersion} 版已保存；项目列表将在下次刷新时更新。`)
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
        setError('检测到版本冲突。请重新载入最新项目，再应用你的修改。')
      } else {
        setError(reason instanceof Error ? reason.message : '项目保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  async function intelligentlyRenameProject() {
    if (!projectId || !form || !editable || suggestingName || form.idea.trim().length < 10) return
    setSuggestingName(true)
    setNameSuggestionNote(null)
    setError(null)
    try {
      const result = await suggestProjectName(projectId, {
        current_name: form.name,
        idea: form.idea,
        genre: form.genre,
        style: form.style,
        narrative_protagonist: form.narrativeProtagonist,
        target_audience: form.targetAudience,
        emotional_rewards: form.emotionalRewards,
        audience_profile: form.audienceProfile,
        production_format: form.productionFormat,
        primary_market: form.primaryMarket,
        canonical_language: form.canonicalLanguage,
        target_duration_sec: form.targetDurationSec,
        aspect_ratio: form.aspectRatio,
        target_platform: form.targetPlatform,
        content_requirements: splitLines(form.contentRequirements),
        content_avoidances: splitLines(form.contentAvoidances),
      })
      setNameBeforeSuggestion(form.name)
      setForm((current) => current ? { ...current, name: result.suggested } : current)
      setNameSuggestionNote(
        `已根据当前故事想法生成新的候选名称。确认后请保存新版本。`,
      )
      setNotice(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目名称智能生成失败')
    } finally {
      setSuggestingName(false)
    }
  }

  function undoProjectNameSuggestion() {
    if (nameBeforeSuggestion === null) return
    setForm((current) => current ? { ...current, name: nameBeforeSuggestion } : current)
    setNameBeforeSuggestion(null)
    setNameSuggestionNote('已撤销候选名称。')
    setNotice(null)
    setError(null)
  }

  async function intelligentlyRewriteIdea() {
    if (!projectId || !form || !editable || rewritingIdea || form.idea.trim().length < 10) return
    const originalIdea = form.idea
    setRewritingIdea(true)
    setIdeaGenerationPhase('thinking')
    setIdeaGenerationStage(0)
    setIdeaGenerationProgress(0)
    setIdeaRewriteNote(null)
    setError(null)
    try {
      const result = await rewriteBriefStory(projectId, {
        idea: form.idea,
        genre: form.genre,
        style: form.style,
        target_duration_sec: form.targetDurationSec,
        aspect_ratio: form.aspectRatio,
        target_platform: form.targetPlatform,
        secondary_platforms: form.secondaryPlatforms,
        narrative_protagonist: form.narrativeProtagonist,
        target_audience: form.targetAudience,
        emotional_rewards: form.emotionalRewards,
        audience_profile: form.audienceProfile,
        production_format: form.productionFormat,
        primary_market: form.primaryMarket,
        secondary_markets: form.secondaryMarkets,
        canonical_language: form.canonicalLanguage,
        localization_targets: form.localizationTargets,
        content_requirements: splitLines(form.contentRequirements),
        content_avoidances: splitLines(form.contentAvoidances),
      })
      setIdeaBeforeRewrite(originalIdea)
      setIdeaGenerationPhase('writing')
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (reduceMotion) {
        setForm((current) => current ? { ...current, idea: result.rewritten } : current)
        setIdeaGenerationProgress(100)
      } else {
        const duration = getGenerativeRevealDuration(result.rewritten.length)
        const startedAt = performance.now()
        await new Promise<void>((resolve) => {
          const reveal = (now: number) => {
            const elapsed = now - startedAt
            const count = getGenerativeRevealCharacterCount(
              result.rewritten.length,
              elapsed,
              duration,
            )
            const progress = getGenerativeRevealProgress(elapsed, duration)
            setForm((current) => current
              ? { ...current, idea: result.rewritten.slice(0, count) }
              : current)
            setIdeaGenerationProgress(Math.min(100, Math.round(progress * 100)))
            if (count >= result.rewritten.length) {
              resolve()
              return
            }
            window.requestAnimationFrame(reveal)
          }
          window.requestAnimationFrame(reveal)
        })
      }
      setIdeaRewriteNote(
        `Doubao Seed 已严格按当前 Brief 重构叙事，并完成 ${result.logicChecks.length} 项逻辑检查。确认后请保存新版本。`,
      )
      setNotice(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Doubao Seed 叙事重构失败')
    } finally {
      setRewritingIdea(false)
      setIdeaGenerationPhase('idle')
      setIdeaGenerationStage(0)
      setIdeaGenerationProgress(0)
    }
  }

  function undoIdeaRewrite() {
    if (ideaBeforeRewrite === null) return
    setForm((current) => current ? { ...current, idea: ideaBeforeRewrite } : current)
    setIdeaBeforeRewrite(null)
    setIdeaRewriteNote('已撤销 Seed 叙事重构。')
    setNotice(null)
    setError(null)
  }

  async function intelligentlyDraftRequirements() {
    if (!projectId || !form || !editable || draftingRequirements || form.idea.trim().length < 10) return
    setDraftingRequirements(true)
    setRequirementsDraftNote(null)
    setError(null)
    try {
      const existing = splitLines(form.contentRequirements)
      const result = await suggestBriefRequirements(projectId, {
        idea: form.idea,
        genre: form.genre,
        style: form.style,
        target_duration_sec: form.targetDurationSec,
        aspect_ratio: form.aspectRatio,
        target_platform: form.targetPlatform,
        narrative_protagonist: form.narrativeProtagonist,
        target_audience: form.targetAudience,
        emotional_rewards: form.emotionalRewards,
        audience_profile: form.audienceProfile,
        production_format: form.productionFormat,
        primary_market: form.primaryMarket,
        canonical_language: form.canonicalLanguage,
        existing_requirements: existing,
        content_avoidances: splitLines(form.contentAvoidances),
      })
      const merged = [...existing]
      result.items.forEach((item) => {
        if (!merged.includes(item)) merged.push(item)
      })
      setRequirementsBeforeDraft(form.contentRequirements)
      setForm((current) => current ? { ...current, contentRequirements: merged.join('\n') } : current)
      setRequirementsDraftNote(
        result.warning
          ? `已补充 ${merged.length - existing.length} 条建议（本地智能回退：${result.warning}）。确认后请保存新版本。`
          : `已补充 ${merged.length - existing.length} 条可执行要求。确认后请保存新版本。`,
      )
      setNotice(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目简报要求智能代写失败')
    } finally {
      setDraftingRequirements(false)
    }
  }

  function undoRequirementsDraft() {
    if (requirementsBeforeDraft === null) return
    setForm((current) => current ? { ...current, contentRequirements: requirementsBeforeDraft } : current)
    setRequirementsBeforeDraft(null)
    setRequirementsDraftNote('已撤销补充内容。')
    setNotice(null)
    setError(null)
  }

  async function intelligentlyDraftAvoidances() {
    if (!projectId || !form || !editable || draftingAvoidances || form.idea.trim().length < 10) return
    setDraftingAvoidances(true)
    setAvoidancesDraftNote(null)
    setError(null)
    try {
      const existing = splitLines(form.contentAvoidances)
      const result = await suggestBriefAvoidances(projectId, {
        idea: form.idea,
        genre: form.genre,
        style: form.style,
        target_duration_sec: form.targetDurationSec,
        aspect_ratio: form.aspectRatio,
        target_platform: form.targetPlatform,
        narrative_protagonist: form.narrativeProtagonist,
        target_audience: form.targetAudience,
        emotional_rewards: form.emotionalRewards,
        audience_profile: form.audienceProfile,
        production_format: form.productionFormat,
        primary_market: form.primaryMarket,
        canonical_language: form.canonicalLanguage,
        content_requirements: splitLines(form.contentRequirements),
        existing_avoidances: existing,
      })
      const merged = [...existing]
      result.items.forEach((item) => {
        if (!merged.includes(item)) merged.push(item)
      })
      setAvoidancesBeforeDraft(form.contentAvoidances)
      setForm((current) => current ? { ...current, contentAvoidances: merged.join('\n') } : current)
      setAvoidancesDraftNote(
        result.warning
          ? `已补充 ${merged.length - existing.length} 条建议（本地智能回退：${result.warning}）。确认后请保存新版本。`
          : `已补充 ${merged.length - existing.length} 条可核验规避项。确认后请保存新版本。`,
      )
      setNotice(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目简报规避项智能建议失败')
    } finally {
      setDraftingAvoidances(false)
    }
  }

  function undoAvoidancesDraft() {
    if (avoidancesBeforeDraft === null) return
    setForm((current) => current ? { ...current, contentAvoidances: avoidancesBeforeDraft } : current)
    setAvoidancesBeforeDraft(null)
    setAvoidancesDraftNote('已撤销规避项建议。')
    setNotice(null)
    setError(null)
  }

  async function generateProposal() {
    if (!projectId || !project || dirty || generating || project.status !== 'DRAFT') return
    setGenerating(true)
    setNotice(null)
    setError(null)
    try {
      const job = await generateStoryDirections(
        projectId,
        project.lockVersion,
        crypto.randomUUID(),
      )
      setNotice(`3 个故事方向任务已持久化：${job.stage}`)
      navigate(`/tasks?project=${projectId}`)
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
        setError('项目版本已变化，请重新载入后再生成导演方案。')
      } else {
        setError(reason instanceof Error ? reason.message : '故事方向任务创建失败')
      }
    } finally {
      setGenerating(false)
    }
  }

  async function approveProposal() {
    if (!projectId || !project || !proposal || !assumptionsConfirmed || approving) return
    setApproving(true)
    setNotice(null)
    setError(null)
    try {
      await approveDirectorProposal(projectId, proposal.version, project.lockVersion)
      await refreshProjects()
      navigate(`/projects/${projectId}/characters`)
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
        setError('项目版本已变化，请重新载入后再批准方案。')
      } else {
        setError(reason instanceof Error ? reason.message : '导演方案批准失败')
      }
    } finally {
      setApproving(false)
    }
  }

  async function enterWorkspace(target: 'episode' | 'preview') {
    if (!projectId) return
    setError(null)
    try {
      const workspace = await fetchWorkspace(projectId)
      await activateProject(projectId)
      navigate(`/projects/${projectId}/episodes/${workspace.project.episodeId}${target === 'preview' ? '/preview' : ''}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '工作台尚未准备完成')
    }
  }

  if (loading) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取项目与项目简报…</strong></div>
  }

  if (!project || !form) {
    return <ServiceRequiredState feature="项目简报" projectId={projectId} />
  }

  const isActiveWorkspace = activeProject.id === project.id
  const hasKnownGenre = GENRE_OPTIONS.some(([value]) => value === form.genre)
  const hasKnownVisualStyle = VISUAL_STYLE_OPTIONS.some(([value]) => value === form.style)
  const recommendedGenre = recommendGenre(form.idea)
  const recommendedGenreLabel = GENRE_OPTIONS.find(([value]) => value === recommendedGenre)?.[1] ?? '都市情感'
  const recommendedVisualStyle = recommendVisualStyle(form.idea, form.genre)
  const recommendedVisualStyleLabel = VISUAL_STYLE_OPTIONS.find(
    ([value]) => value === recommendedVisualStyle,
  )?.[1] ?? '写实电影感'
  const targetingMissing = narrativeTargetingMissing(
    form.narrativeProtagonist,
    form.emotionalRewards,
  )
  const slateMix = TOPIC_SLATE_MIX[form.productionFormat]
  const shotCount = proposal?.scenes.reduce((sum, scene) => sum + scene.shots.length, 0) ?? 0
  const runningJobType = project.status === 'STORY_STRUCTURE_RUNNING'
    ? 'GENERATE_STORY_STRUCTURE'
    : project.status === 'SCRIPT_PACKAGE_RUNNING'
      ? 'GENERATE_SCRIPT_PACKAGE'
      : project.status === 'STORY_PACKAGE_RUNNING'
        ? 'GENERATE_STORY_PACKAGE'
    : project.status === 'PROPOSAL_RUNNING'
      ? 'GENERATE_STORY_DIRECTIONS'
      : null
  return (
    <div className="page page--brief">
      <PageHeader
        eyebrow="项目概览"
        title={form.name || project.name}
        description={`完善项目设定，生成并选择故事方向，再进入后续创作。当前项目第 ${project.lockVersion} 版，项目简报第 ${briefVersion ?? '—'} 版。`}
        actions={<><Link className="button button--secondary button--md" to="/projects"><ArrowLeft size={16} />项目列表</Link>{isActiveWorkspace ? <Link className="button button--primary button--md" to={`/projects/${activeProject.id}/episodes/${activeProject.episodeId}`}><Clapperboard size={16} />进入工作台</Link> : null}</>}
      />

      <div className="brief-editor-layout">
        <section className="brief-editor-card">
          <div className="section-heading">
            <div><h2>项目设定</h2></div>
            <div className="brief-version-badges">
              {!editable ? <StatusBadge
                description="项目简报已锁定。项目已进入后续流程，当前简报仅供查看。"
                status="BRIEF_LOCKED"
              /> : null}
              {runningJobType ? (
                <Link
                  aria-label="查看当前生成任务"
                  className="brief-task-entry"
                  to={`/tasks?project=${project.id}&jobType=${runningJobType}`}
                >
                  <StatusBadge status={project.status} />
                  <span><ListChecks size={13} />查看任务<ArrowRight size={13} /></span>
                </Link>
              ) : ['RELATIONSHIP_READY', 'CHARACTER_VISUAL_READY'].includes(project.status) ? (
                <>
                  <StatusBadge status={project.status} />
                  <Link
                    className="button button--secondary button--sm"
                    to={project.status === 'CHARACTER_VISUAL_READY' ? `/projects/${project.id}/characters` : `/projects/${project.id}/story#relationship-review`}
                  >
                    {project.status === 'CHARACTER_VISUAL_READY' ? '锁定角色形象' : '审核关系'}<ArrowRight size={13} />
                  </Link>
                </>
              ) : <StatusBadge status={project.status} />}
            </div>
          </div>
          <div className="brief-progressive-guide">
            <span><ListChecks size={17} /></span>
            <div>
              <strong>{targetingMissing.length
                ? `下一步：确认${targetingMissing.join('、')}`
                : splitLines(form.blockingQuestions).length
                  ? '下一步：处理生成前问题'
                  : '核心设定已经完整'}</strong>
              <p>先确认故事与核心规格；受众细节、发行设置和生成边界按当前任务展开。</p>
            </div>
          </div>
          <div className="brief-form-grid">
            <div className="brief-field brief-field--wide brief-name-field">
              <div className="brief-field__heading">
                <label htmlFor="brief-project-name">短剧名称</label>
                <span className="brief-field__actions">
                  <button
                    disabled={!editable || apiStatus !== 'connected' || suggestingName || form.idea.trim().length < 10}
                    onClick={() => void intelligentlyRenameProject()}
                    type="button"
                  >
                    {suggestingName ? <LoaderCircle className="spin" size={12} /> : <FilePenLine size={12} />}
                    {suggestingName ? '正在生成新名称' : '生成新名称'}
                  </button>
                  {nameBeforeSuggestion !== null ? (
                    <button className="brief-field__action--undo" onClick={undoProjectNameSuggestion} type="button">
                      <RotateCcw size={12} />撤销
                    </button>
                  ) : null}
                </span>
              </div>
              <input
                disabled={!editable || suggestingName}
                id="brief-project-name"
                maxLength={120}
                onChange={(event) => {
                  updateField('name', event.target.value)
                  setNameBeforeSuggestion(null)
                  setNameSuggestionNote(null)
                }}
                value={form.name}
              />
              {nameSuggestionNote ? <small aria-live="polite" className="brief-field__note">{nameSuggestionNote}</small> : null}
            </div>
            <div className="brief-field brief-field--wide">
              <div className="brief-field__heading">
                <label htmlFor="brief-story-idea">故事想法</label>
                <span className="brief-field__actions">
                  <button
                    disabled={!editable || apiStatus !== 'connected' || rewritingIdea || form.idea.trim().length < 10}
                    onClick={() => void intelligentlyRewriteIdea()}
                    type="button"
                  >
                    {rewritingIdea
                      ? <LoaderCircle className="spin" size={12} />
                      : <FilePenLine size={12} />}
                    {rewritingIdea
                      ? ideaGenerationPhase === 'writing' ? '正在写入' : '正在构思'
                      : '重写故事'}
                  </button>
                  {ideaBeforeRewrite !== null ? (
                    <button className="brief-field__action--undo" onClick={undoIdeaRewrite} type="button">
                      <RotateCcw size={12} />撤销
                    </button>
                  ) : null}
                </span>
              </div>
              <div
                className={`brief-generative-field${rewritingIdea ? ' is-generating' : ''}`}
                data-phase={ideaGenerationPhase}
              >
                <textarea
                  aria-busy={rewritingIdea}
                  disabled={!editable || rewritingIdea}
                  id="brief-story-idea"
                  maxLength={4000}
                  onChange={(event) => {
                    fitStoryIdeaTextarea(event.currentTarget)
                    updateField('idea', event.target.value)
                    setIdeaBeforeRewrite(null)
                    setIdeaRewriteNote(null)
                  }}
                  ref={storyIdeaRef}
                  value={form.idea}
                />
                {rewritingIdea ? (
                  <div aria-live="polite" className="brief-generation-status" role="status">
                    <span className="brief-generation-status__icon" aria-hidden="true">
                      <LoaderCircle className="spin" size={15} />
                    </span>
                    <span className="brief-generation-status__copy">
                      <strong>{ideaGenerationPhase === 'writing'
                        ? '内容已经就绪，正在逐段写入'
                        : IDEA_GENERATION_STAGES[ideaGenerationStage].title}</strong>
                      <small>{ideaGenerationPhase === 'writing'
                        ? '完成后可以继续编辑，也可以一键撤销。'
                        : IDEA_GENERATION_STAGES[ideaGenerationStage].detail}</small>
                    </span>
                    {ideaGenerationPhase === 'writing' ? (
                      <span className="brief-generation-status__progress">{ideaGenerationProgress}%</span>
                    ) : (
                      <span className="brief-generation-status__dots" aria-hidden="true">
                        <i /><i /><i />
                      </span>
                    )}
                  </div>
                ) : null}
              </div>
              {ideaRewriteNote ? <small aria-live="polite" className="brief-field__note">{ideaRewriteNote}</small> : null}
            </div>
            <div className="brief-field">
              <div className="brief-field__heading brief-field__heading--recommendation">
                <label htmlFor="brief-genre">题材</label>
                <span className="brief-field__recommendation" title="根据当前故事想法推荐">
                  <span>建议</span>{recommendedGenreLabel}
                </span>
              </div>
              <SelectControl aria-label="题材" disabled={!editable} id="brief-genre" onChange={(event) => updateGenre(event.target.value)} value={form.genre}>
                {!hasKnownGenre ? <option value={form.genre}>{form.genre}（旧数据）</option> : null}
                {GENRE_OPTION_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </optgroup>
                ))}
              </SelectControl>
            </div>
            <div className="brief-field">
              <div className="brief-field__heading brief-field__heading--recommendation">
                <label htmlFor="brief-visual-style">视觉风格</label>
                <span className="brief-field__recommendation" title="根据当前故事想法与题材推荐">
                  <span>建议</span>{recommendedVisualStyleLabel}
                </span>
              </div>
              <SelectControl aria-label="视觉风格" disabled={!editable} id="brief-visual-style" onChange={(event) => updateField('style', event.target.value)} value={form.style}>
                {!hasKnownVisualStyle ? <option value={form.style}>{form.style}（旧数据）</option> : null}
                {VISUAL_STYLE_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </SelectControl>
            </div>
            <label className="brief-field"><span>目标时长</span><SelectControl aria-label="目标时长" disabled={!editable} onChange={(event) => updateField('targetDurationSec', Number(event.target.value))} value={form.targetDurationSec}><option value={45}>45 秒</option><option value={60}>60 秒</option><option value={90}>90 秒</option></SelectControl></label>
            <label className="brief-field"><span>画幅</span><SelectControl aria-label="画幅" disabled={!editable} onChange={(event) => updateField('aspectRatio', event.target.value as BriefForm['aspectRatio'])} value={form.aspectRatio}><option value="9:16">9:16 竖屏</option><option value="16:9">16:9 横屏</option></SelectControl></label>
            <label className="brief-field"><span>内容形态</span><SelectControl aria-label="内容形态" disabled={!editable} onChange={(event) => updateField('productionFormat', event.target.value as ProductionFormat)} value={form.productionFormat}>{PRODUCTION_FORMAT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>
            <label className="brief-field"><span>叙事主角</span><SelectControl aria-label="叙事主角" disabled={!editable} onChange={(event) => updateField('narrativeProtagonist', event.target.value as NarrativeProtagonist)} value={form.narrativeProtagonist}>{NARRATIVE_PROTAGONIST_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>
            <label className="brief-field"><span>目标受众</span><SelectControl aria-label="目标受众" disabled={!editable} onChange={(event) => updateField('targetAudience', event.target.value as TargetAudience)} value={form.targetAudience}>{TARGET_AUDIENCE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>
            <label className="brief-field"><span>主市场</span><SelectControl aria-label="主市场" disabled={!editable} onChange={(event) => setForm((current) => current ? { ...current, primaryMarket: event.target.value, secondaryMarkets: current.secondaryMarkets.filter((item) => item !== event.target.value) } : current)} value={form.primaryMarket}>{MARKET_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>

            <BriefDisclosure
              description="补充项目画像和希望观众获得的情绪结果。"
              meta={targetingMissing.length ? `待确认 ${targetingMissing.length} 项` : `${form.emotionalRewards.length} 项情绪回报`}
              onToggle={() => toggleBriefSection('audience')}
              open={openBriefSection === 'audience'}
              title="受众与情绪"
            >
              <label className="brief-field brief-field--wide"><span>补充受众画像（可选）</span><input disabled={!editable} maxLength={240} onChange={(event) => updateField('audienceProfile', event.target.value)} placeholder="例如：25—40岁女性；仅用于本项目表达校准" value={form.audienceProfile} /><small>年龄、性别、兴趣或媒介习惯只作为项目画像，不决定主角、题材或情绪回报。</small></label>
              <fieldset className="brief-choice-field brief-field--wide" disabled={!editable}><legend>情绪回报（可多选，至少一项）</legend><div className="brief-choice-grid">{EMOTIONAL_REWARD_OPTIONS.map(([value, label]) => <label key={value}><input checked={form.emotionalRewards.includes(value)} onChange={() => toggleEmotionalReward(value)} type="checkbox" /><span>{label}</span></label>)}</div></fieldset>
              <div className="brief-field brief-field--wide brief-field--compact"><span>首批选题池配比</span><small>当前内容形态：女频 {slateMix.female_frequency}% · 泛人群 {slateMix.general}% · 男频 {slateMix.male_frequency}%。该配比只用于多项目选题组合，不会改写本项目设置。</small></div>
            </BriefDisclosure>

            <BriefDisclosure
              description="需要跨市场、跨语言或多平台发行时再调整。"
              meta={`${1 + form.secondaryMarkets.length} 个市场 · ${1 + form.secondaryPlatforms.length} 个平台`}
              onToggle={() => toggleBriefSection('delivery')}
              open={openBriefSection === 'delivery'}
              title="发行与本地化"
            >
              <fieldset className="brief-choice-field brief-field--wide" disabled={!editable}><legend>次要市场（可多选）</legend><div className="brief-choice-grid">{MARKET_OPTIONS.filter(([value]) => value !== form.primaryMarket).map(([value, label]) => <label key={value}><input checked={form.secondaryMarkets.includes(value)} onChange={() => toggleSelection('secondaryMarkets', value)} type="checkbox" /><span>{label}</span></label>)}</div></fieldset>
              <label className="brief-field"><span>规范语言</span><SelectControl aria-label="规范语言" disabled={!editable} onChange={(event) => setForm((current) => current ? { ...current, canonicalLanguage: event.target.value, localizationTargets: current.localizationTargets.filter((item) => item !== event.target.value) } : current)} value={form.canonicalLanguage}>{LANGUAGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>
              <fieldset className="brief-choice-field" disabled={!editable}><legend>本地化语言（可多选）</legend><div className="brief-choice-grid">{LANGUAGE_OPTIONS.filter(([value]) => value !== form.canonicalLanguage).map(([value, label]) => <label key={value}><input checked={form.localizationTargets.includes(value)} onChange={() => toggleSelection('localizationTargets', value)} type="checkbox" /><span>{label}</span></label>)}</div></fieldset>
              <label className="brief-field"><span>主平台</span><SelectControl aria-label="主平台" disabled={!editable} onChange={(event) => setForm((current) => current ? { ...current, targetPlatform: event.target.value, secondaryPlatforms: current.secondaryPlatforms.filter((item) => item !== event.target.value) } : current)} value={form.targetPlatform}>{PLATFORM_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</SelectControl></label>
              <fieldset className="brief-choice-field" disabled={!editable}><legend>同步平台（可多选）</legend><div className="brief-choice-grid">{PLATFORM_OPTIONS.filter(([value]) => value !== form.targetPlatform).map(([value, label]) => <label key={value}><input checked={form.secondaryPlatforms.includes(value)} onChange={() => toggleSelection('secondaryPlatforms', value)} type="checkbox" /><span>{label}</span></label>)}</div></fieldset>
            </BriefDisclosure>

            <BriefDisclosure
              description="把必须满足、必须避免和阻断问题集中在一起。"
              meta={`${splitLines(form.contentRequirements).length} 条要求 · ${splitLines(form.contentAvoidances).length} 条规避`}
              onToggle={() => toggleBriefSection('guardrails')}
              open={openBriefSection === 'guardrails'}
              title="生成边界"
            >
              <div className="brief-field brief-field--wide brief-field--compact">
                <div className="brief-field__heading">
                  <label htmlFor="brief-content-requirements">必须满足（每行一条）</label>
                  <span className="brief-field__actions">
                    <button
                      disabled={!editable || apiStatus !== 'connected' || draftingRequirements || form.idea.trim().length < 10}
                      onClick={() => void intelligentlyDraftRequirements()}
                      type="button"
                    >
                      {draftingRequirements ? <LoaderCircle className="spin" size={12} /> : <ListPlus size={12} />}
                      {draftingRequirements ? '正在补充' : '补充要求'}
                    </button>
                    {requirementsBeforeDraft !== null ? (
                      <button className="brief-field__action--undo" onClick={undoRequirementsDraft} type="button">
                        <RotateCcw size={12} />撤销
                      </button>
                    ) : null}
                  </span>
                </div>
                <textarea
                  disabled={!editable || draftingRequirements}
                  id="brief-content-requirements"
                  maxLength={3000}
                  onChange={(event) => {
                    updateField('contentRequirements', event.target.value)
                    setRequirementsBeforeDraft(null)
                    setRequirementsDraftNote(null)
                  }}
                  placeholder="例如：前三秒出现危机"
                  value={form.contentRequirements}
                />
                {requirementsDraftNote ? <small aria-live="polite" className="brief-field__note">{requirementsDraftNote}</small> : null}
              </div>
              <div className="brief-field brief-field--wide brief-field--compact">
                <div className="brief-field__heading">
                  <label htmlFor="brief-content-avoidances">必须避免（每行一条）</label>
                  <span className="brief-field__actions">
                    <button
                      disabled={!editable || apiStatus !== 'connected' || draftingAvoidances || form.idea.trim().length < 10}
                      onClick={() => void intelligentlyDraftAvoidances()}
                      type="button"
                    >
                      {draftingAvoidances ? <LoaderCircle className="spin" size={12} /> : <ShieldCheck size={12} />}
                      {draftingAvoidances ? '正在检查' : '检查规避项'}
                    </button>
                    {avoidancesBeforeDraft !== null ? (
                      <button className="brief-field__action--undo" onClick={undoAvoidancesDraft} type="button">
                        <RotateCcw size={12} />撤销
                      </button>
                    ) : null}
                  </span>
                </div>
                <textarea
                  disabled={!editable || draftingAvoidances}
                  id="brief-content-avoidances"
                  maxLength={3000}
                  onChange={(event) => {
                    updateField('contentAvoidances', event.target.value)
                    setAvoidancesBeforeDraft(null)
                    setAvoidancesDraftNote(null)
                  }}
                  placeholder="例如：未授权品牌露出"
                  value={form.contentAvoidances}
                />
                {avoidancesDraftNote ? <small aria-live="polite" className="brief-field__note">{avoidancesDraftNote}</small> : null}
              </div>
              <label className="brief-field brief-field--wide brief-field--compact"><span>生成前必须回答的问题（每行一条）</span><textarea disabled={!editable} maxLength={2000} onChange={(event) => updateField('blockingQuestions', event.target.value)} placeholder="留空即可生成；有内容时系统会阻止方案生成" value={form.blockingQuestions} /></label>
            </BriefDisclosure>
          </div>
          {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}<Button onClick={() => window.location.reload()} size="sm" variant="ghost"><RefreshCw size={14} />重新载入</Button></div> : null}
          {notice ? <div className="brief-save-message brief-save-message--success">{notice}</div> : null}
          <div className="brief-editor-actions">
            <span>{dirty
              ? '有尚未保存的修改；生成前请先保存'
              : splitLines(form.blockingQuestions).length
                ? '仍有阻断问题，保存后也不能开始生成'
                : targetingMissing.length
                  ? `生成前还需确认：${targetingMissing.join('、')}`
                : project.status === 'SCRIPT_READY'
                  ? '故事资料包已生成，等待审核'
                  : '当前内容已同步'}</span>
            <div>
              <Button disabled={!editable || !dirty || saving || rewritingIdea || draftingRequirements || draftingAvoidances || form.idea.trim().length < 10 || !form.name.trim()} onClick={save}>
                {saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
                {saving ? '保存中' : '保存新版本'}
              </Button>
              {project.status === 'PROPOSAL_READY' ? (
                <Link className="button button--secondary button--md" to={`/projects/${project.id}/story`}>
                  <Play size={16} />比较故事方向
                </Link>
              ) : project.status === 'RELATIONSHIP_READY' ? (
                <Link className="button button--primary button--md" to={`/projects/${project.id}/story#relationship-review`}>
                  <ListChecks size={16} />审核角色关系
                </Link>
              ) : project.status === 'CHARACTER_VISUAL_READY' ? (
                <Link className="button button--primary button--md" to={`/projects/${project.id}/characters`}>
                  <ListChecks size={16} />生成并锁定角色形象
                </Link>
              ) : project.status === 'SCRIPT_READY' ? (
                <Link className="button button--primary button--md" to={`/projects/${project.id}/story`}>
                  <Clapperboard size={16} />审核故事与剧本
                </Link>
              ) : (
                <Button disabled={project.status !== 'DRAFT' || dirty || saving || generating || splitLines(form.blockingQuestions).length > 0 || targetingMissing.length > 0} onClick={generateProposal} variant="secondary">
                  {generating || project.status === 'PROPOSAL_RUNNING' ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
                  {project.status === 'PROPOSAL_RUNNING' ? '方向生成中' : generating ? '正在入队' : '生成 3 个故事方向'}
                </Button>
              )}
            </div>
          </div>
        </section>

        <aside className="brief-fact-card">
          <div className="section-heading"><div><h2>版本信息</h2></div><Database size={19} /></div>
          <dl><div><dt>项目编号</dt><dd>{project.id}</dd></div><div><dt>简报结构版本</dt><dd>brief-v3</dd></div><div><dt>简报版本</dt><dd>{briefVersion ?? '—'}</dd></div><div><dt>锁定版本</dt><dd>{project.lockVersion}</dd></div><div><dt>创建时间</dt><dd>{new Date(project.createdAt).toLocaleString('zh-CN')}</dd></div><div><dt>最后更新</dt><dd>{new Date(project.updatedAt).toLocaleString('zh-CN')}</dd></div></dl>
        </aside>
      </div>

      {proposal?.directionKey === 'legacy' ? <section className="brief-proposal-card" id="director-proposal">
        <div className="proposal-ready">
          <header><div><p className="eyebrow">导演方案 · 第 {proposal.version} 版</p><h2>{proposal.title}</h2></div><StatusBadge status={proposal.status} /></header>
          <blockquote>{proposal.logline}</blockquote>
          <p className="proposal-synopsis">{proposal.directorStatement}</p>
          <div className="proposal-specs"><span><small>场景</small><strong>{proposal.scenes.length}</strong></span><span><small>镜头</small><strong>{shotCount}</strong></span><span><small>时长</small><strong>{proposal.totalDurationSec} 秒</strong></span><span><small>生成服务</small><strong>{proposal.provider}</strong></span></div>
          <section className="proposal-scenes"><div className="section-heading"><div><p className="eyebrow">故事节拍</p><h2>三段式场景</h2></div></div>{proposal.scenes.map((scene) => <div key={scene.code}><span>{scene.code}</span><div><strong>{scene.title}</strong><p>{scene.purpose}</p></div><small>{scene.durationSec} 秒<br />{scene.shots.length} 个镜头</small></div>)}</section>
          <div className="assumption-box"><div><ListChecks size={15} /><strong>待确认假设</strong></div><ul>{proposal.assumptions.map((item) => <li key={item}>{item}</li>)}</ul>{project.status === 'PROPOSAL_READY' ? <label><input checked={assumptionsConfirmed} onChange={(event) => setAssumptionsConfirmed(event.target.checked)} type="checkbox" /><span><Check size={12} /></span>我已查看并确认以上假设</label> : null}</div>
          <div className="proposal-actions">
            {project.status === 'PROPOSAL_READY' ? <Button disabled={!assumptionsConfirmed || approving} onClick={approveProposal}>{approving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{approving ? '正在批准' : '批准故事并生成角色'}</Button> : null}
            {project.status === 'CHARACTER_VISUAL_READY' ? <Link className="button button--primary button--md" to={`/projects/${project.id}/characters`}>生成并锁定角色形象</Link> : null}
            {['CHARACTER_LOCKED', 'PRODUCING'].includes(project.status) ? <Link className="button button--primary button--md" to={`/tasks?project=${project.id}`}>查看制作任务</Link> : null}
            {project.status === 'PREVIEW_READY' ? <Button onClick={() => enterWorkspace('preview')}><Clapperboard size={16} />打开真实小样</Button> : null}
            {['APPROVED', 'EXPORTING', 'EXPORTED'].includes(project.status) ? <Button onClick={() => enterWorkspace('episode')}><Clapperboard size={16} />进入制作工作台</Button> : null}
          </div>
          <p className="proposal-persistence-note">方案、批准状态与后续任务均已持久化；刷新页面不会丢失。</p>
        </div>
      </section> : null}
    </div>
  )
}
