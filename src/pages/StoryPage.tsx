import { type MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  BookOpenCheck,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Coins,
  FileStack,
  Flame,
  GitMerge,
  Globe2,
  History,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  MessageCircle,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Scissors,
  Sparkles,
  UsersRound,
  WandSparkles,
  Workflow,
  X,
} from 'lucide-react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams } from 'react-router'
import {
  ApiError,
  applyScriptExcerptRewrite,
  approveScriptVersion,
  confirmCharacterRevision,
  createScriptExcerptRewrite,
  fetchBriefVersions,
  fetchProject,
  fetchScriptExcerptRewrites,
  fetchStoryPackageEstimate,
  fetchStoryWorkspace,
  generateStoryDirections,
  generateStoryPackage,
  mergeStoryDirections,
  reviewCharacterRevision,
  type CharacterRevisionChanges,
  type CharacterRevisionReview,
  type StoryWorkspace,
  type StoryPackageEstimate,
  type RelationshipGraphVersionRecord,
  type ScriptExcerptRewrite,
  type ScriptExcerptRewriteAction,
} from '../api/client'
import { RelationshipGraphSection, type RelationshipCharacter } from '../components/relationship-graph/RelationshipGraphSection'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { Button, Modal, PageHeader, SelectControl, StatusBadge, getStatusLabel } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { BriefVersionRecord, DirectorProposal, NarrativeTargeting, ProjectRecord } from '../types'
import { directionKeyLabel, directionKeyTurns, isQuestionStyleHook } from '../utils/storyDirection'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import { diffText } from '../utils/textDiff'
import { syncVisualNotesWithEthnicity } from '../utils/characterIdentityVisuals'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function recordList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function characterAge(character: Record<string, unknown>): string {
  const explicit = stringValue(character.age, '')
  if (explicit) return explicit
  const match = stringValue(character.visual_notes, '').match(/(\d{1,3}\s*岁(?:左右)?|[二三四五六七八九十百]{1,4}岁(?:左右)?)/)
  return match?.[1] ?? '待补充'
}

function characterGender(character: Record<string, unknown>): CharacterEditDraft['gender'] {
  const gender = stringValue(character.gender, 'unspecified')
  return gender === 'male' || gender === 'female' || gender === 'nonbinary'
    ? gender
    : 'unspecified'
}

function worldEthnicitySuggestion(world: string): string {
  const rules: Array<[RegExp, string]> = [
    [/(华人|华裔|汉族|东亚移民)/i, '华人背景'],
    [/(日裔|日本裔)/i, '日本背景'],
    [/(韩裔|韩国裔)/i, '韩国背景'],
    [/(南亚裔|印度裔|巴基斯坦裔|孟加拉裔)/i, '南亚背景'],
    [/(非洲裔|非洲侨民|黑人社群)/i, '非洲或非洲侨民背景'],
    [/(拉丁裔|拉丁美洲移民|拉美社群)/i, '拉丁裔或拉丁美洲背景'],
    [/(中东裔|北非裔|阿拉伯裔)/i, '中东或北非背景'],
    [/(原住民|印第安人|第一民族)/i, '原住民背景'],
    [/(多族裔|混血|多元族裔)/i, '多族裔背景'],
  ]
  return rules.find(([pattern]) => pattern.test(world))?.[1] ?? ''
}

function characterEthnicity(character: Record<string, unknown>, world: string): string {
  const ethnicity = stringValue(character.ethnicity, '')
  if (!/^(unspecified|not specified|未指定)$/i.test(ethnicity)) {
    return normalizedEthnicityValue(ethnicity)
  }
  return worldEthnicitySuggestion(world)
}

function characterGenderLabel(character: Record<string, unknown>): string {
  return ({
    female: '女性',
    male: '男性',
    nonbinary: '非二元',
    unspecified: '未指定',
  } as const)[characterGender(character)]
}

function characterInitials(character: Record<string, unknown>): string {
  const name = stringValue(character.name, '').trim()
  const words = name.split(/\s+/).filter(Boolean)
  if (words.length > 1) {
    const first = Array.from(words[0])[0] ?? ''
    const last = Array.from(words.at(-1) ?? '')[0] ?? ''
    return `${first}${last}`.toLocaleUpperCase('en-US')
  }
  return Array.from(name.replace(/\s+/g, '')).slice(0, 2).join('').toLocaleUpperCase('en-US') || '角色'
}

function characterOccupation(character: Record<string, unknown>): string {
  const explicit = stringValue(character.occupation, '')
  if (explicit) return explicit
  const role = stringValue(character.role, '')
  const roleOccupation = role.split(/[，,]/).map((item) => item.trim()).find((item) => /(董事长|主管|工程师|医生|律师|教师|警察|记者|经理|总监|设计师|演员|导演|编剧|厨师|店主|职员)/.test(item))
  if (roleOccupation) return roleOccupation.replace(/^(核心反派|职场小反派|中立配角)[，,]?/, '')
  const evidence = `${stringValue(character.desire, '')} ${stringValue(character.visual_notes, '')}`
  if (evidence.includes('广告公司')) return '广告公司职员'
  if (evidence.includes('食堂')) return '社区食堂工作人员'
  return '待补充'
}

function characterHeight(character: Record<string, unknown>): string {
  const explicit = character.height
  if (typeof explicit === 'number' && Number.isFinite(explicit)) return `${explicit} cm`
  return stringValue(explicit, '未指定')
}

function characterPersonality(character: Record<string, unknown>): string {
  const explicitList = stringList(character.personality)
  if (explicitList.length) return explicitList.join('、')
  const explicit = stringValue(character.personality, '')
  if (explicit) return explicit
  const evidence = `${stringValue(character.visual_notes, '')} ${stringValue(character.dramatic_function, '')}`
  const clues: Array<[string, string]> = [
    ['利落', '利落'], ['克制', '克制'], ['警觉', '警觉'], ['随意', '随性'],
    ['和蔼', '外表和蔼'], ['锐利', '观察敏锐'], ['颐指气使', '强势'],
    ['看不起', '势利'], ['小心翼翼', '谨慎'], ['怕惹事', '怕事'], ['冷静', '冷静'],
  ]
  const values = clues.filter(([source]) => evidence.includes(source)).map(([, label]) => label)
  return [...new Set(values)].slice(0, 3).join('、') || '待补充'
}

type CharacterFilter = 'all' | 'core' | 'opposition' | 'supporting'

interface CharacterEditDraft {
  name: string
  role: string
  gender: 'male' | 'female' | 'nonbinary' | 'unspecified'
  ethnicity: string
  age: string
  height: string
  occupation: string
  personality: string
  dramaticFunction: string
  desire: string
  fear: string
  secret: string
  visualNotes: string
}

interface ScriptTextSelection {
  scriptId: string
  lineId: string
  lineText: string
  selectionStart: number
  selectionEnd: number
  selectedText: string
  left: number
  top: number
}

type ScriptRewriteMenuMode = 'ACTIONS' | 'TONE' | 'CUSTOM'

const SCRIPT_REWRITE_ACTION_LABELS: Record<ScriptExcerptRewriteAction, string> = {
  REWRITE: '改写',
  SHORTEN: '缩短',
  INTENSIFY_CONFLICT: '增强冲突',
  ADJUST_TONE: '调整语气',
  CUSTOM: '自定义',
}

function characterCategory(character: Record<string, unknown>): Exclude<CharacterFilter, 'all'> {
  const role = stringValue(character.role, '').toLowerCase()
  if (/(叙事主角|共同主角|主人公|protagonist|co-protagonist)/.test(role)) return 'core'
  if (/(反派|对立|对手|敌手|阻碍|antagonist|rival)/.test(role)) return 'opposition'
  return 'supporting'
}

const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音',
  kuaishou: '快手',
  reels: 'Instagram Reels',
  youtube_shorts: 'YouTube Shorts',
}

const MARKET_LABELS: Record<string, string> = {
  CN: '中国大陆',
  SG: '新加坡',
  MY: '马来西亚',
  US: '美国',
  GB: '英国',
}

const MARKET_FLAGS: Record<string, string> = {
  CN: '🇨🇳',
  SG: '🇸🇬',
  MY: '🇲🇾',
  US: '🇺🇸',
  GB: '🇬🇧',
}

function MarketValues({ markets }: { markets: string[] }) {
  if (!markets.length) return <>未设置</>
  return (
    <span className="story-market-values">
      {markets.map((market) => (
        <span className="story-market-value" key={market}>
          {MARKET_FLAGS[market]
            ? <span aria-hidden="true" className="story-market-flag">{MARKET_FLAGS[market]}</span>
            : <Globe2 aria-hidden="true" size={15} />}
          <span>{MARKET_LABELS[market] ?? market}</span>
        </span>
      ))}
    </span>
  )
}

const AUDIENCE_LABELS: Record<string, string> = {
  general: '泛人群',
  male_frequency: '男频',
  female_frequency: '女频',
  urban_women_25_34: '都市女性 25–34',
  young_adults: '年轻成人',
  suspense_fans: '悬疑爱好者',
  mobile_heavy_users: '移动端重度用户',
}

const PROTAGONIST_LABELS: Record<string, string> = {
  unspecified: '待确认',
  male: '男性',
  female: '女性',
  dual: '双主角',
  ensemble: '群像',
}

const EMOTIONAL_REWARD_LABELS: Record<string, string> = {
  romance: '爱情',
  identity: '身份',
  career: '事业',
  revenge: '复仇',
  family: '亲情',
  power: '权力',
  public_mission: '公共使命',
}

function labelValues(values: string[], labels: Record<string, string>): string {
  return values.map((value) => labels[value] ?? value).join('、') || '未设置'
}

function directionCompliance(
  direction: DirectorProposal,
  brief: BriefVersionRecord | null,
): NonNullable<DirectorProposal['briefCompliance']> {
  if (direction.briefCompliance) return direction.briefCompliance
  if (!brief) return { status: 'PARTIAL', items: [] }
  const staleBrief = direction.briefVersion !== brief.version
  const items: NonNullable<DirectorProposal['briefCompliance']>['items'] = [
    ...brief.contentRequirements.map((item) => ({
      category: 'REQUIREMENT' as const,
      item,
      status: 'PARTIAL' as const,
      evidence: staleBrief
        ? `该方向基于简报第 ${direction.briefVersion} 版，当前第 ${brief.version} 版要求尚未重新生成验证。`
        : '方向摘要无法完整证明该条要求，需在完整剧本和分镜中核验。',
    })),
    ...brief.contentAvoidances.map((item) => ({
      category: 'AVOIDANCE' as const,
      item,
      status: staleBrief ? 'PARTIAL' as const : 'MET' as const,
      evidence: staleBrief
        ? `该方向基于简报第 ${direction.briefVersion} 版，当前禁止项尚未重新核验。`
        : '当前方向未出现明确冲突，生成完整剧本后继续复核。',
    })),
  ]
  return { status: items.some((item) => item.status === 'PARTIAL') ? 'PARTIAL' : 'ALL_MET', items }
}

function complianceLabel(status: 'ALL_MET' | 'PARTIAL' | 'CONFLICT'): string {
  return status === 'ALL_MET' ? '全部满足' : status === 'CONFLICT' ? '存在冲突' : '部分满足'
}

function directionProductionComplexity(direction: DirectorProposal): NonNullable<DirectorProposal['productionComplexity']> {
  if (direction.productionComplexity) return direction.productionComplexity
  const shotCount = direction.scenes.reduce((total, scene) => total + scene.shots.length, 0)
  const exteriorScenes = direction.scenes.filter((scene) => /外|街|城|山|边关|旷野|庭院/.test(`${scene.title}${scene.purpose}`))
  return {
    characterCount: Math.max(2, Math.min(8, Math.ceil(direction.scenes.length * 1.5))),
    sceneCount: direction.scenes.length,
    exteriorSceneCount: exteriorScenes.length,
    exteriorRequirements: exteriorScenes.length ? exteriorScenes.map((scene) => scene.title) : ['当前方向未明确外景'],
    vfxRequirements: direction.visualSignature ? [direction.visualSignature] : ['以基础环境增强为主'],
    estimatedGeneration: {
      keyframeImages: shotCount,
      videoClips: shotCount,
      voiceSegments: Math.max(4, direction.scenes.length * 2),
    },
  }
}

function directionFirstEpisodeRhythm(direction: DirectorProposal): NonNullable<DirectorProposal['firstEpisodeRhythm']> {
  if (direction.firstEpisodeRhythm) return direction.firstEpisodeRhythm
  const turns = directionKeyTurns(direction)
  return {
    opening3sHook: turns[0] ?? direction.scenes[0]?.purpose ?? direction.logline,
    firstPayoff: direction.storyDna?.payoff ?? turns[1] ?? direction.differentiator ?? direction.directorStatement,
    endingAction: direction.sequelSetup?.finalRevealOrAction ?? direction.storyDna?.ending_hook ?? turns.at(-1) ?? '待在完整剧本中明确',
  }
}

function recommendationMatches(direction: DirectorProposal, brief: BriefVersionRecord | null): string[] {
  if (direction.aiRecommendation?.briefMatches.length) return direction.aiRecommendation.briefMatches
  if (!brief) return ['与当前故事核心冲突一致']
  const verified = directionCompliance(direction, brief).items
    .filter((item) => item.status === 'MET')
    .slice(0, 2)
    .map((item) => item.item)
  return [
    ...verified,
    `${PLATFORM_LABELS[brief.targetPlatform] ?? brief.targetPlatform}平台`,
    `${AUDIENCE_LABELS[brief.targetAudience] ?? brief.targetAudience}目标受众`,
    `${brief.targetDurationSec} 秒目标时长`,
  ].slice(0, 3)
}

function directionTargeting(
  direction: DirectorProposal,
  brief: BriefVersionRecord | null,
): NarrativeTargeting | null {
  if (direction.narrativeTargeting) return direction.narrativeTargeting
  if (!brief) return null
  return {
    narrativeProtagonist: brief.narrativeProtagonist,
    targetAudience: brief.targetAudience,
    emotionalRewards: brief.emotionalRewards,
    audienceProfile: brief.audienceProfile,
    productionFormat: brief.productionFormat,
  }
}

function durationEstimateLabel(seconds: number): string {
  return seconds < 60 ? `约 ${seconds} 秒` : `约 ${Math.ceil(seconds / 60)} 分钟`
}

const DEFAULT_PACKAGE_ESTIMATE: StoryPackageEstimate = {
  assets: ['故事设定', '角色文字设定', '分集大纲', '结构化首集剧本'],
  estimatedSeconds: 240,
  estimatedPoints: 0,
  directionLock: 'ON_SUCCESS',
  versionStrategy: 'CREATE_NEW_VERSION',
}

const ETHNICITY_SUGGESTIONS_BY_MARKET: Record<string, string[]> = {
  US: [
    '白人（未细分）',
    '西北欧背景',
    '中欧背景',
    '南欧背景',
    '东欧背景',
    '黑人或非裔美国人',
    '亚裔美国人（未细分）',
    '华裔美国人',
    '台湾裔美国人',
    '香港裔美国人',
    '日裔美国人',
    '韩裔美国人',
    '蒙古裔美国人',
    '越南裔美国人',
    '泰裔美国人',
    '菲律宾裔美国人',
    '柬埔寨裔美国人',
    '老挝裔美国人',
    '苗族裔美国人',
    '缅甸裔美国人',
    '印度尼西亚裔美国人',
    '马来西亚裔美国人',
    '新加坡裔美国人',
    '印度裔美国人',
    '巴基斯坦裔美国人',
    '孟加拉裔美国人',
    '斯里兰卡裔美国人',
    '尼泊尔裔美国人',
    '不丹裔美国人',
    '西裔或拉丁裔美国人',
    '美洲原住民或阿拉斯加原住民',
    '中东或北非裔美国人',
    '夏威夷原住民或太平洋岛民',
    '多族裔',
  ],
}

const CUSTOM_ETHNICITY_VALUE = '__custom_ethnicity__'

function ethnicitySuggestionsForMarket(primaryMarket: string | undefined): string[] {
  return ETHNICITY_SUGGESTIONS_BY_MARKET[primaryMarket ?? ''] ?? []
}

function normalizedEthnicityValue(value: string): string {
  if (value === '白人') return '白人（未细分）'
  if (value === '亚裔美国人') return '亚裔美国人（未细分）'
  return value
}

export function StoryPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const { refreshProjects } = useStudio()
  const { notify } = useToast()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<StoryWorkspace | null>(null)
  const [brief, setBrief] = useState<BriefVersionRecord | null>(null)
  const [packageEstimate, setPackageEstimate] = useState<StoryPackageEstimate | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [mergeMode, setMergeMode] = useState(false)
  const [expandedDirectionId, setExpandedDirectionId] = useState<string | null>(null)
  const [characterFilter, setCharacterFilter] = useState<CharacterFilter>('all')
  const [editingCharacter, setEditingCharacter] = useState<Record<string, unknown> | null>(null)
  const [characterEditDraft, setCharacterEditDraft] = useState<CharacterEditDraft | null>(null)
  const [customEthnicityActive, setCustomEthnicityActive] = useState(false)
  const [characterRevisionReview, setCharacterRevisionReview] = useState<CharacterRevisionReview | null>(null)
  const [characterRevisionBusy, setCharacterRevisionBusy] = useState(false)
  const [characterRevisionError, setCharacterRevisionError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)
  const [approveScriptOpen, setApproveScriptOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [scriptSelection, setScriptSelection] = useState<ScriptTextSelection | null>(null)
  const [scriptRewriteMenuOpen, setScriptRewriteMenuOpen] = useState(false)
  const [scriptRewriteMenuMode, setScriptRewriteMenuMode] = useState<ScriptRewriteMenuMode>('ACTIONS')
  const [scriptRewriteCustomInstruction, setScriptRewriteCustomInstruction] = useState('')
  const [scriptRewriteBusy, setScriptRewriteBusy] = useState(false)
  const [scriptRewriteError, setScriptRewriteError] = useState<string | null>(null)
  const [activeScriptRewrite, setActiveScriptRewrite] = useState<ScriptExcerptRewrite | null>(null)
  const [scriptRewriteVersions, setScriptRewriteVersions] = useState<ScriptExcerptRewrite[]>([])
  const [scriptRewriteVersionsOpen, setScriptRewriteVersionsOpen] = useState(false)
  const [relationshipFocus, setRelationshipFocus] = useState<{
    graphId: string
    relationshipKey: string
    beatOrdinal: number
    requestId: number
  } | null>(null)

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!projectId) return
    const [nextProject, nextWorkspace, briefVersions, nextPackageEstimate] = await Promise.all([
      fetchProject(projectId, signal),
      fetchStoryWorkspace(projectId, signal),
      fetchBriefVersions(projectId, signal),
      fetchStoryPackageEstimate(projectId, signal).catch(() => DEFAULT_PACKAGE_ESTIMATE),
    ])
    setProject(nextProject)
    setWorkspace(nextWorkspace)
    setBrief(briefVersions[0] ?? null)
    setPackageEstimate(nextPackageEstimate)
  }, [projectId])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    void load(controller.signal)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '故事工作区读取失败')
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (loading || !workspace?.relationshipGraphVersions.length || window.location.hash !== '#relationship-review') return
    window.requestAnimationFrame(() => {
      document.getElementById('relationship-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [loading, workspace?.relationshipGraphVersions.length])

  const directions = workspace?.directions ?? []
  const latestScript = workspace?.scriptVersions[0]
  const latestBible = workspace?.storyBibleVersions[0]
  const latestOutline = workspace?.episodeOutlineVersions[0]
  const activeDirection = useMemo(
    () => directions.find((direction) => selected.includes(direction.id)) ?? null,
    [directions, selected],
  )
  const expandedDirection = useMemo(
    () => directions.find((direction) => direction.id === expandedDirectionId) ?? null,
    [directions, expandedDirectionId],
  )
  const recommendedDirection = useMemo(() => {
    const currentBriefDirections = brief
      ? directions.filter((direction) => direction.briefVersion === brief.version)
      : directions
    const comparable = (currentBriefDirections.length ? currentBriefDirections : directions)
      .filter((direction) => direction.directionKey !== 'merged')
    return comparable.find((direction) => direction.aiRecommendation?.recommended)
      ?? comparable.find((direction) => direction.directionKey === 'market')
      ?? comparable.find((direction) => directionCompliance(direction, brief).status !== 'CONFLICT')
      ?? comparable[0]
      ?? null
  }, [brief, directions])
  const directionSelectionOpen = project?.status === 'PROPOSAL_READY'
  const approvedDirection = directions.find((direction) => direction.status === 'APPROVED')
    ?? directions.find((direction) => direction.title === workspace?.storyDnaVersions[0]?.title)
    ?? null
  const visibleDirections = directionSelectionOpen
    ? directions
    : approvedDirection ? [approvedDirection] : []

  function chooseDirection(id: string) {
    setSelected((current) => {
      if (!mergeMode) return current.includes(id) ? [] : [id]
      return current.includes(id)
        ? current.filter((item) => item !== id)
        : current.length >= 3 ? current : [...current, id]
    })
    setError(null)
    setNotice(null)
  }

  function toggleMergeMode() {
    setMergeMode((current) => {
      const next = !current
      if (!next) setSelected((values) => values.slice(0, 1))
      return next
    })
    setError(null)
    setNotice(null)
  }

  async function runAction(action: () => Promise<void>) {
    if (acting) return
    setActing(true)
    setError(null)
    setNotice(null)
    try {
      await action()
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
        setError('项目版本已变化，请刷新后重新选择。')
      } else {
        setError(reason instanceof Error ? reason.message : '操作失败')
      }
    } finally {
      setActing(false)
    }
  }

  async function confirmApproveScript() {
    if (!latestScript || !project) return
    await runAction(async () => {
      const job = await approveScriptVersion(latestScript.id, project.lockVersion)
      await refreshProjects()
      setApproveScriptOpen(false)
      notify('首集剧本已批准，角色任务已入队。')
      setNotice(`剧本已批准，角色任务已入队：${job.stage}`)
      navigate(`/tasks?project=${project.id}`)
    })
  }

  function captureScriptSelection(
    event: ReactMouseEvent<HTMLParagraphElement>,
    line: { id: string; text: string },
  ) {
    if (!latestScript || !project || latestScript.status !== 'READY_FOR_REVIEW' || project.status !== 'SCRIPT_READY') return
    const selection = window.getSelection()
    const paragraph = event.currentTarget
    if (
      !selection
      || selection.isCollapsed
      || !selection.rangeCount
      || !selection.anchorNode
      || !selection.focusNode
      || !paragraph.contains(selection.anchorNode)
      || !paragraph.contains(selection.focusNode)
    ) return
    const range = selection.getRangeAt(0)
    const before = document.createRange()
    before.selectNodeContents(paragraph)
    before.setEnd(range.startContainer, range.startOffset)
    const selectedText = range.toString()
    if (!selectedText.trim()) return
    const selectionStart = Array.from(before.toString()).length
    const selectionEnd = selectionStart + Array.from(selectedText).length
    const rect = range.getBoundingClientRect()
    const menuHeight = 286
    const menuTop = rect.bottom + 8 + menuHeight > window.innerHeight
      ? Math.max(12, rect.top - menuHeight - 8)
      : rect.bottom + 8
    setScriptSelection({
      scriptId: latestScript.id,
      lineId: line.id,
      lineText: line.text,
      selectionStart,
      selectionEnd,
      selectedText,
      left: Math.max(12, Math.min(window.innerWidth - 238, rect.left + rect.width / 2 - 108)),
      top: menuTop,
    })
    setScriptRewriteMenuMode('ACTIONS')
    setScriptRewriteCustomInstruction('')
    setScriptRewriteError(null)
    setScriptRewriteMenuOpen(true)
    setActiveScriptRewrite(null)
    setScriptRewriteVersionsOpen(false)
  }

  async function generateScriptRewrite(
    action: ScriptExcerptRewriteAction,
    options: {
      tone?: string
      customInstruction?: string
      parentRevisionId?: string
    } = {},
  ) {
    if (!scriptSelection || !project) return
    setScriptRewriteBusy(true)
    setScriptRewriteError(null)
    try {
      const revision = await createScriptExcerptRewrite(
        scriptSelection.scriptId,
        scriptSelection.lineId,
        {
          expectedVersion: project.lockVersion,
          selectionStart: scriptSelection.selectionStart,
          selectionEnd: scriptSelection.selectionEnd,
          action,
          tone: options.tone,
          customInstruction: options.customInstruction,
          parentRevisionId: options.parentRevisionId,
        },
      )
      setActiveScriptRewrite(revision)
      setScriptRewriteVersions((current) => [
        revision,
        ...current.filter((item) => item.id !== revision.id),
      ])
      setScriptRewriteMenuOpen(false)
      setScriptRewriteMenuMode('ACTIONS')
      setScriptRewriteVersionsOpen(false)
      window.getSelection()?.removeAllRanges()
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
        setScriptRewriteError('剧本版本已经变化，请刷新后重新选择。')
      } else {
        setScriptRewriteError(reason instanceof Error ? reason.message : '改写生成失败')
      }
    } finally {
      setScriptRewriteBusy(false)
    }
  }

  function keepOriginalScriptText() {
    setActiveScriptRewrite(null)
    setScriptSelection(null)
    setScriptRewriteError(null)
    setScriptRewriteVersionsOpen(false)
  }

  async function useActiveScriptRewrite() {
    if (!activeScriptRewrite || !scriptSelection || !project || !latestScript) return
    setScriptRewriteBusy(true)
    setScriptRewriteError(null)
    try {
      const result = await applyScriptExcerptRewrite(activeScriptRewrite.id, {
        expectedVersion: project.lockVersion,
        scriptId: latestScript.id,
        lineId: scriptSelection.lineId,
      })
      await load()
      await refreshProjects()
      setActiveScriptRewrite(null)
      setScriptSelection(null)
      setScriptRewriteVersions([])
      setScriptRewriteVersionsOpen(false)
      setNotice(`已创建第 ${result.scriptVersion} 版剧本，原版本仍可随时查看。`)
    } catch (reason) {
      if (reason instanceof ApiError && ['VERSION_CONFLICT', 'SCRIPT_REWRITE_SOURCE_CHANGED'].includes(reason.code)) {
        setScriptRewriteError('原文或剧本版本已经变化，请刷新后重新选择。')
      } else {
        setScriptRewriteError(reason instanceof Error ? reason.message : '未能使用这个改写版本')
      }
    } finally {
      setScriptRewriteBusy(false)
    }
  }

  async function toggleScriptRewriteVersions() {
    if (!scriptSelection) return
    if (scriptRewriteVersionsOpen) {
      setScriptRewriteVersionsOpen(false)
      return
    }
    setScriptRewriteBusy(true)
    setScriptRewriteError(null)
    try {
      const versions = await fetchScriptExcerptRewrites(
        scriptSelection.scriptId,
        scriptSelection.lineId,
      )
      setScriptRewriteVersions(versions)
      setScriptRewriteVersionsOpen(true)
    } catch (reason) {
      setScriptRewriteError(reason instanceof Error ? reason.message : '改写版本读取失败')
    } finally {
      setScriptRewriteBusy(false)
    }
  }

  function applyRelationshipGraph(nextGraph: RelationshipGraphVersionRecord) {
    setWorkspace((current) => {
      if (!current) return current
      const versions = current.relationshipGraphVersions.some((item) => item.id === nextGraph.id)
        ? current.relationshipGraphVersions.map((item) => item.id === nextGraph.id ? nextGraph : item)
        : [nextGraph, ...current.relationshipGraphVersions]
      return { ...current, relationshipGraphVersions: versions.sort((a, b) => b.version - a.version) }
    })
    setProject((current) => current ? { ...current, lockVersion: nextGraph.projectLockVersion } : current)
  }

  function openCharacterEditor(character: Record<string, unknown>) {
    const ethnicity = normalizedEthnicityValue(
      characterEthnicity(character, stringValue(latestBible?.payload.world, '')),
    )
    const suggestedEthnicities = ethnicitySuggestionsForMarket(brief?.primaryMarket)
    setEditingCharacter(character)
    setCharacterEditDraft({
      name: stringValue(character.name, ''), role: stringValue(character.role, ''),
      gender: characterGender(character),
      ethnicity,
      age: characterAge(character), height: characterHeight(character),
      occupation: characterOccupation(character),
      personality: characterPersonality(character),
      dramaticFunction: stringValue(character.dramatic_function, ''),
      desire: stringValue(character.desire, ''), fear: stringValue(character.fear, ''),
      secret: stringValue(character.secret, ''), visualNotes: stringValue(character.visual_notes, ''),
    })
    setCustomEthnicityActive(Boolean(ethnicity) && !suggestedEthnicities.includes(ethnicity))
    setCharacterRevisionReview(null)
    setCharacterRevisionError(null)
  }

  function characterChanges(): CharacterRevisionChanges {
    if (!characterEditDraft) return {}
    return {
      name: characterEditDraft.name, role: characterEditDraft.role,
      age: characterEditDraft.age, height: characterEditDraft.height,
      gender: characterEditDraft.gender,
      ethnicity: characterEditDraft.ethnicity.trim() || 'unspecified',
      occupation: characterEditDraft.occupation,
      personality: characterEditDraft.personality.split(/[、,，]/).map((item) => item.trim()).filter(Boolean).slice(0, 5),
      dramatic_function: characterEditDraft.dramaticFunction, desire: characterEditDraft.desire,
      fear: characterEditDraft.fear, secret: characterEditDraft.secret,
      visual_notes: characterEditDraft.visualNotes,
    }
  }

  async function runCharacterReview() {
    if (!editingCharacter || !latestBible || !project) return
    const graph = workspace?.relationshipGraphVersions.find((item) => item.storyBibleVersionId === latestBible.id) ?? workspace?.relationshipGraphVersions[0]
    if (!graph) return
    setCharacterRevisionBusy(true)
    setError(null)
    setCharacterRevisionError(null)
    try {
      const review = await reviewCharacterRevision(project.id, {
        baseStoryBibleId: latestBible.id, baseRelationshipGraphId: graph.id,
        characterKey: stringValue(editingCharacter.key, ''), changes: characterChanges(),
        expectedVersion: project.lockVersion,
      })
      setCharacterRevisionReview(review)
      window.requestAnimationFrame(() => {
        document.querySelector('.character-revision-review')?.scrollIntoView({
          behavior: 'smooth',
          block: 'nearest',
        })
      })
    } catch (reason) {
      setCharacterRevisionError(reason instanceof Error ? reason.message : '角色逻辑审核失败')
    } finally {
      setCharacterRevisionBusy(false)
    }
  }

  async function confirmCharacterEdit() {
    if (!editingCharacter || !latestBible || !project || !characterRevisionReview) return
    setCharacterRevisionBusy(true)
    setError(null)
    setCharacterRevisionError(null)
    try {
      await confirmCharacterRevision(project.id, {
        baseStoryBibleId: characterRevisionReview.baseStoryBibleId,
        baseRelationshipGraphId: characterRevisionReview.baseRelationshipGraphId,
        characterKey: stringValue(editingCharacter.key, ''), changes: characterChanges(),
        expectedVersion: project.lockVersion, impactHash: characterRevisionReview.impactHash,
      })
      setEditingCharacter(null)
      setCharacterEditDraft(null)
      setCustomEthnicityActive(false)
      setCharacterRevisionReview(null)
      await load()
      await refreshProjects()
      setNotice('角色修改版已创建；请重新核对相关人物关系，确认后生成新故事线。')
    } catch (reason) {
      setCharacterRevisionError(reason instanceof Error ? reason.message : '角色修改版创建失败')
    } finally {
      setCharacterRevisionBusy(false)
    }
  }

  if (loading) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取故事资产…</strong></div>
  }
  if (!project || !workspace || !projectId) {
    return <ServiceRequiredState feature="故事与剧本" projectId={projectId} />
  }

  const biblePayload = latestBible?.payload ?? {}
  const bibleRules = stringList(biblePayload.rules)
  const ethnicitySuggestions = ethnicitySuggestionsForMarket(brief?.primaryMarket)
  const ethnicityOptions = Array.from(new Set([
    ...(characterEditDraft?.ethnicity.trim() ? [characterEditDraft.ethnicity.trim()] : []),
    ...ethnicitySuggestions,
  ]))
  const outlinePayload = latestOutline?.payload ?? {}
  const characters = recordList(biblePayload.characters)
  const characterTabs: Array<{ id: CharacterFilter; label: string; count: number }> = [
    { id: 'all', label: '全部', count: characters.length },
    { id: 'core', label: '核心角色', count: characters.filter((character) => characterCategory(character) === 'core').length },
    { id: 'opposition', label: '对立角色', count: characters.filter((character) => characterCategory(character) === 'opposition').length },
    { id: 'supporting', label: '支撑角色', count: characters.filter((character) => characterCategory(character) === 'supporting').length },
  ]
  const visibleCharacters = characterFilter === 'all'
    ? characters
    : characters.filter((character) => characterCategory(character) === characterFilter)
  const relationshipCharacters: RelationshipCharacter[] = characters.map((character) => ({
    key: stringValue(character.key, ''),
    name: stringValue(character.name, stringValue(character.key)),
    role: stringValue(character.role, '角色'),
    desire: stringValue(character.desire, ''),
    fear: stringValue(character.fear, ''),
    secret: stringValue(character.secret, ''),
    dramaticFunction: stringValue(character.dramatic_function, ''),
    age: characterAge(character),
    occupation: characterOccupation(character),
    personality: characterPersonality(character),
  }))
  const criticStatus = stringValue(latestScript?.critic.status, '待检查')
  const enginePayload = isRecord(latestScript?.payload.short_drama_engine)
    ? latestScript.payload.short_drama_engine
    : {}
  const reversalChain = stringList(enginePayload.reversal_chain)
  const engineBeats = recordList(enginePayload.beats)
  const breakoutPayload = isRecord(latestScript?.payload.breakout_engine)
    ? latestScript.payload.breakout_engine
    : {}
  const misjudgmentChain = recordList(breakoutPayload.misjudgment_chain)
  const authenticationLadder = recordList(breakoutPayload.authentication_ladder)
  const relationshipReorders = recordList(breakoutPayload.relationship_reorders)
  const emotionalOrder = isRecord(breakoutPayload.emotional_order_rebuild)
    ? breakoutPayload.emotional_order_rebuild
    : {}
  const sequelUnit = isRecord(breakoutPayload.sequel_unit) ? breakoutPayload.sequel_unit : {}
  const scriptRelationshipGraph = workspace.relationshipGraphVersions.find(
    (graph) => graph.id === latestScript?.relationshipGraphVersionId,
  )
  function relationshipBeatForScene(sceneOrdinal: number) {
    return scriptRelationshipGraph?.graph.beats.find(
      (beat) => beat.episodeOrdinal === latestScript?.episodeOrdinal
        && beat.sceneOrdinal === sceneOrdinal,
    )
  }

  function renderScriptRewriteDiff(sceneOrdinal: number, lineOrdinal: number) {
    if (
      !activeScriptRewrite
      || activeScriptRewrite.sceneOrdinal !== sceneOrdinal
      || activeScriptRewrite.lineOrdinal !== lineOrdinal
    ) return null
    const canUseVersion = activeScriptRewrite.status === 'GENERATED'
      && activeScriptRewrite.baseScriptVersionId === latestScript?.id
      && activeScriptRewrite.baseLineId === scriptSelection?.lineId
    const parts = diffText(
      activeScriptRewrite.originalText,
      activeScriptRewrite.proposedText,
    )
    return (
      <section className="script-rewrite-diff" aria-live="polite">
        <header>
          <div>
            <span>改写版本 {activeScriptRewrite.version}</span>
            <strong>{SCRIPT_REWRITE_ACTION_LABELS[activeScriptRewrite.action]}</strong>
          </div>
          <small>{activeScriptRewrite.provider}/{activeScriptRewrite.model}</small>
        </header>
        <div className="script-rewrite-diff__text">
          {parts.map((part, index) => part.type === 'delete'
            ? <del key={`${part.type}-${index}`}>{part.text}</del>
            : part.type === 'insert'
              ? <ins key={`${part.type}-${index}`}>{part.text}</ins>
              : <span key={`${part.type}-${index}`}>{part.text}</span>)}
        </div>
        <p>{activeScriptRewrite.rationale}</p>
        {scriptRewriteError ? <div className="script-rewrite-error" role="alert"><AlertTriangle size={14} />{scriptRewriteError}</div> : null}
        {scriptRewriteVersionsOpen ? (
          <div className="script-rewrite-versions">
            <span>其他版本</span>
            <div>
              {scriptRewriteVersions.map((revision) => (
                <button
                  className={revision.id === activeScriptRewrite.id ? 'is-active' : ''}
                  key={revision.id}
                  onClick={() => setActiveScriptRewrite(revision)}
                  type="button"
                >
                  <strong>版本 {revision.version}</strong>
                  <span>{SCRIPT_REWRITE_ACTION_LABELS[revision.action]}</span>
                  {revision.status === 'APPLIED' ? <small>已使用</small> : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <footer>
          <Button disabled={scriptRewriteBusy} onClick={keepOriginalScriptText} size="sm" variant="ghost">
            保留原文
          </Button>
          <Button disabled={scriptRewriteBusy || !canUseVersion} onClick={() => void useActiveScriptRewrite()} size="sm">
            <Check size={14} />使用新版本
          </Button>
          <Button
            disabled={scriptRewriteBusy}
            onClick={() => void generateScriptRewrite(activeScriptRewrite.action, {
              tone: activeScriptRewrite.tone ?? undefined,
              customInstruction: activeScriptRewrite.customInstruction ?? undefined,
              parentRevisionId: activeScriptRewrite.id,
            })}
            size="sm"
            variant="secondary"
          >
            {scriptRewriteBusy ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}
            再试一次
          </Button>
          <Button disabled={scriptRewriteBusy} onClick={() => void toggleScriptRewriteVersions()} size="sm" variant="ghost">
            <History size={14} />查看其他版本
          </Button>
        </footer>
      </section>
    )
  }

  return (
    <div className="page page--story">
      <PageHeader
        eyebrow="第 2 阶段 · 故事与剧本"
        title="故事方向与剧本"
        description="比较故事方向，先审核故事设定与角色关系，再生成分集大纲和结构化首集剧本。"
        actions={<><Link className="button button--secondary button--md" to={`/projects/${project.id}`}><ArrowLeft size={16} />返回项目简报</Link><Button onClick={() => void runAction(async () => { await load(); setNotice('已载入最新版本。') })} variant="secondary"><RefreshCw size={16} />刷新</Button></>}
      />

      {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
      {notice ? <div className="brief-save-message brief-save-message--success">{notice}</div> : null}

      {brief ? <section className="story-brief-baseline" aria-labelledby="story-brief-baseline-title">
        <header><div><p className="eyebrow">方向评审依据</p><h2 id="story-brief-baseline-title">本次创作基准</h2></div><span>所有方向均应符合以下条件</span></header>
        <dl className="story-brief-baseline__facts">
          <div><dt>平台</dt><dd>{labelValues(brief.platformTargets.map((item) => item.platform), PLATFORM_LABELS)}</dd></div>
          <div><dt>市场</dt><dd><MarketValues markets={[brief.primaryMarket, ...brief.secondaryMarkets]} /></dd></div>
          <div><dt>核心观众</dt><dd>{labelValues([brief.primaryAudience, ...brief.secondaryAudiences], AUDIENCE_LABELS)}</dd></div>
          <div><dt>目标时长</dt><dd>{brief.targetDurationSec} 秒 · {brief.aspectRatio} {brief.aspectRatio === '9:16' ? '竖屏' : '横屏'}</dd></div>
        </dl>
        <div className="story-brief-baseline__constraints">
          <article><h3><Check size={15} />必须满足 <span>{brief.contentRequirements.length}</span></h3>{brief.contentRequirements.length ? <ul>{brief.contentRequirements.map((item) => <li key={item}>{item}</li>)}</ul> : <p>未设置额外必须满足项。</p>}</article>
          <article><h3><AlertTriangle size={15} />必须避免 <span>{brief.contentAvoidances.length}</span></h3>{brief.contentAvoidances.length ? <ul>{brief.contentAvoidances.map((item) => <li key={item}>{item}</li>)}</ul> : <p>未设置额外必须避免项。</p>}</article>
        </div>
      </section> : null}

      <section className="story-section">
        <div className="section-heading"><div><p className="eyebrow">{directionSelectionOpen ? '方向审核' : '方向基线'}</p><h2>{directionSelectionOpen ? mergeMode ? '选择要合并的故事方向' : '选择一个故事方向' : '已确认的故事方向'}</h2><p>{directionSelectionOpen ? mergeMode ? '至少选择两个方向，系统会保留各自优势并生成一个新的融合版本。' : '先比较最影响决策的差异，需要时再展开完整方案。确认后先生成可审核的故事结构与角色关系。' : '该方向已作为故事设定与角色关系的创作基线，当前阶段不再重复展示其他候选方向。'}</p></div>{directions.length === 0 ? <Button disabled={acting || project.status !== 'DRAFT'} onClick={() => void runAction(async () => { const job = await generateStoryDirections(project.id, project.lockVersion, crypto.randomUUID()); setNotice(`任务已入队：${job.stage}`); navigate(`/tasks?project=${project.id}`) })}><BookOpenCheck size={16} />生成 3 个方向</Button> : null}</div>
        {directions.length === 0 ? <div className="story-direction-empty" role="status">
          <p>还没有故事方向。完成以下步骤后即可开始生成：</p>
          <ul className="story-prerequisite-list">
            <li className={brief ? 'is-done' : 'is-pending'}>{brief ? <Check size={15} /> : <AlertTriangle size={15} />}<span>项目简报已保存并作为评审依据</span></li>
            <li className={project.status === 'DRAFT' ? 'is-done' : 'is-pending'}>{project.status === 'DRAFT' ? <Check size={15} /> : <AlertTriangle size={15} />}<span>项目处于草稿状态（当前：{getStatusLabel(project.status)}）</span></li>
            <li className={project.status !== 'PROPOSAL_RUNNING' ? 'is-done' : 'is-pending'}>{project.status !== 'PROPOSAL_RUNNING' ? <Check size={15} /> : <LoaderCircle className="spin" size={15} />}<span>{project.status === 'PROPOSAL_RUNNING' ? '故事方向正在生成中' : '当前没有进行中的方向任务'}</span></li>
          </ul>
          {project.status === 'PROPOSAL_RUNNING' ? <Link className="button button--secondary button--md" to={`/tasks?project=${project.id}`}>查看生成任务</Link> : project.status !== 'DRAFT' ? <Link className="button button--secondary button--md" to={`/projects/${project.id}`}>返回项目简报</Link> : !brief ? <Link className="button button--secondary button--md" to={`/projects/${project.id}`}>先完善项目简报</Link> : null}
        </div> : null}
        {directionSelectionOpen && mergeMode ? <section className="story-merge-explanation" aria-label="合并方向规则">
          <article><Check size={16} /><div><strong>保留什么</strong><p>保留选中方向的核心冲突、情绪承诺、有效转折和续作动作，不是简单拼接文案。</p></div></article>
          <article><AlertTriangle size={16} /><div><strong>如何处理冲突</strong><p>以 Brief 必须满足与必须避免为最高优先级，再统一人物动机、时间线和因果链；无法兼容的内容会明确取舍。</p></div></article>
          <article><GitMerge size={16} /><div><strong>合并后的结果</strong><p>生成一个新的独立方向，保留来源关系；原方向不会被覆盖，新方向需重新审核后才能生成剧本。</p></div></article>
        </section> : null}
        {directionSelectionOpen && !mergeMode && recommendedDirection ? <section className="story-ai-recommendation" aria-label="建议方向"><BookOpenCheck size={18} /><div><span>建议优先比较</span><strong>{recommendedDirection.title}</strong><p>{recommendedDirection.aiRecommendation?.reason ?? '该方向在不增加无根据设定的前提下，更直接对齐平台钩子、核心观众和目标时长。'}</p><ul>{recommendationMatches(recommendedDirection, brief).map((item) => <li key={item}>{item}</li>)}</ul></div></section> : null}
        <div aria-label={directionSelectionOpen ? mergeMode ? '选择要合并的故事方向' : '选择一个故事方向' : '已确认的故事方向'} className={`story-direction-comparison ${directionSelectionOpen ? '' : 'story-direction-comparison--confirmed'}`} role={directionSelectionOpen ? mergeMode ? 'group' : 'radiogroup' : undefined}>
          {visibleDirections.map((direction) => {
            const checked = directionSelectionOpen ? selected.includes(direction.id) : direction.id === approvedDirection?.id
            const expanded = expandedDirectionId === direction.id
            const compliance = directionCompliance(direction, brief)
            const exceptions = compliance.items.filter((item) => item.status !== 'MET')
            const production = directionProductionComplexity(direction)
            const recommended = recommendedDirection?.id === direction.id
            return <article
              aria-checked={directionSelectionOpen ? checked : undefined}
              aria-label={directionSelectionOpen ? `${checked ? '取消选择' : mergeMode ? '加入合并' : '选择'}：${direction.title}` : undefined}
              className={`story-direction-summary ${checked ? 'story-direction-summary--selected' : ''} ${directionSelectionOpen ? '' : 'story-direction-summary--locked'}`}
              key={direction.id}
              onClick={directionSelectionOpen ? () => chooseDirection(direction.id) : undefined}
              onKeyDown={(event) => {
                if (!directionSelectionOpen) return
                if (event.target !== event.currentTarget) return
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  chooseDirection(direction.id)
                }
              }}
              role={directionSelectionOpen ? mergeMode ? 'checkbox' : 'radio' : undefined}
              tabIndex={directionSelectionOpen ? 0 : undefined}
            >
              <header><span>{directionKeyLabel(direction.directionKey)}</span><div>{directionSelectionOpen && recommended ? <span className="story-direction-recommendation-badge">建议优先</span> : null}<StatusBadge label={directionSelectionOpen ? checked ? '已选择' : mergeMode ? '待合并' : '待选择' : '已确认'} status={directionSelectionOpen ? checked ? 'SELECTED' : direction.status : 'APPROVED'} /></div></header>
              <h3>{direction.title}</h3>
              <p className="story-direction-summary__logline">{direction.logline}</p>
              <dl className="story-direction-summary__key-facts">
                <div><dt>核心冲突</dt><dd>{direction.storyDna?.central_conflict ?? direction.differentiator ?? '—'}</dd></div>
                <div><dt>情绪承诺</dt><dd>{direction.storyDna?.emotional_promise ?? direction.directorStatement}</dd></div>
                <div><dt>制作规模</dt><dd>{production.characterCount} 个角色 · {production.sceneCount} 个场景 · {production.exteriorSceneCount} 个外景</dd></div>
              </dl>
              <div
                aria-label={`Brief 合规：${complianceLabel(compliance.status)}${exceptions.length ? `，${exceptions.length} 条需关注` : ''}`}
                className={`story-direction-summary__compliance story-direction-summary__compliance--${compliance.status.toLowerCase()}`}
              >
                <span aria-hidden="true" className="story-direction-summary__compliance-icon">
                  {compliance.status === 'ALL_MET' ? <Check size={14} /> : <AlertTriangle size={14} />}
                </span>
                <span className="story-direction-summary__compliance-copy">
                  <small>Brief 合规</small>
                  <strong>{complianceLabel(compliance.status)}</strong>
                </span>
                {exceptions.length ? <em>{exceptions.length} 条需关注</em> : <em>无待处理项</em>}
              </div>
              <footer>
                <button aria-expanded={expanded} className="story-direction-detail-trigger" onClick={(event) => { event.stopPropagation(); setExpandedDirectionId((current) => current === direction.id ? null : direction.id) }} type="button">
                  {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}{expanded ? '收起完整方案' : '查看完整方案'}
                </button>
              </footer>
            </article>
          })}
        </div>
        {expandedDirection ? (() => {
          const direction = expandedDirection
          const checked = selected.includes(direction.id)
          const turns = directionKeyTurns(direction)
          const legacyHook = direction.storyDna?.ending_hook ?? ''
          const invalidLegacyHook = !direction.sequelSetup && isQuestionStyleHook(legacyHook)
          const compliance = directionCompliance(direction, brief)
          const targeting = directionTargeting(direction, brief)
          const production = directionProductionComplexity(direction)
          const rhythm = directionFirstEpisodeRhythm(direction)
          return <Modal
            className="modal--story-direction"
            description={`${directionKeyLabel(direction.directionKey)} · 方向版本 ${direction.version}`}
            footer={<Button onClick={() => setExpandedDirectionId(null)}>关闭完整方案</Button>}
            onClose={() => setExpandedDirectionId(null)}
            open
            title={direction.title}
          ><article className={`story-direction-card story-direction-card--detail ${checked ? 'story-direction-card--selected' : ''}`}>
            <div className="story-direction-card__lead">
              <div><span className="eyebrow">一句话梗概</span><p>{direction.logline}</p></div>
              <blockquote><strong>为什么选这个方向</strong>{direction.differentiator ?? direction.directorStatement}</blockquote>
            </div>
            <div className="story-direction-card__operations">
              <section className="story-direction-production"><header><strong>制作复杂度</strong><span>预计 {production.characterCount} 角色 · {production.sceneCount} 场景 · {production.exteriorSceneCount} 外景</span></header><p>生成量级：{production.estimatedGeneration.keyframeImages} 张关键帧 · {production.estimatedGeneration.videoClips} 段视频 · {production.estimatedGeneration.voiceSegments} 段语音</p><small>外景：{production.exteriorRequirements.join('、')}；特效：{production.vfxRequirements.join('、')}</small></section>
              <section className="story-direction-rhythm"><strong>首集节奏</strong><dl><div><dt>3 秒钩子</dt><dd>{rhythm.opening3sHook}</dd></div><div><dt>首个兑现</dt><dd>{rhythm.firstPayoff}</dd></div><div><dt>结尾动作</dt><dd>{rhythm.endingAction}</dd></div></dl></section>
            </div>
            <div className="story-direction-card__decision-grid">
              {direction.storyDna ? <dl>
                {targeting ? <>
                  <div><dt>叙事主角</dt><dd>{PROTAGONIST_LABELS[targeting.narrativeProtagonist] ?? targeting.narrativeProtagonist}</dd></div>
                  <div><dt>目标受众</dt><dd>{AUDIENCE_LABELS[targeting.targetAudience] ?? targeting.targetAudience}</dd></div>
                  <div><dt>情绪回报</dt><dd>{labelValues(targeting.emotionalRewards, EMOTIONAL_REWARD_LABELS)}</dd></div>
                  {targeting.audienceProfile ? <div><dt>补充受众画像</dt><dd>{targeting.audienceProfile}</dd></div> : null}
                </> : null}
                <div><dt>核心前提</dt><dd>{direction.storyDna.core_premise}</dd></div>
                <div><dt>主角外在目标</dt><dd>{direction.storyDna.protagonist_want}</dd></div>
                <div><dt>主角内在需要</dt><dd>{direction.storyDna.protagonist_need}</dd></div>
                <div><dt>核心冲突</dt><dd>{direction.storyDna.central_conflict}</dd></div>
                {direction.storyDna.stakes ? <div><dt>失败代价</dt><dd>{direction.storyDna.stakes}</dd></div> : null}
                <div><dt>情绪承诺</dt><dd>{direction.storyDna.emotional_promise}</dd></div>
                {direction.storyDna.payoff ? <div><dt>本部兑现</dt><dd>{direction.storyDna.payoff}</dd></div> : null}
                {direction.audienceFit ? <div><dt>最适合的观众</dt><dd>{direction.audienceFit}</dd></div> : null}
                {direction.visualSignature ? <div><dt>视觉抓手</dt><dd>{direction.visualSignature}</dd></div> : null}
                {direction.selectionTradeoff ? <div><dt>选择代价</dt><dd>{direction.selectionTradeoff}</dd></div> : null}
              </dl> : null}
              <section className="story-direction-card__turns"><h4>关键剧情转折</h4><ol>{turns.map((turn, index) => <li key={`${direction.id}-${index}`}><span>{index + 1}</span><p>{turn}</p></li>)}</ol></section>
            </div>
            {direction.sequelSetup ? <section className="story-direction-card__sequel">
              <div><span>续作剧情铺垫</span><strong>不是向观众提问，而是让剧情继续发生</strong></div>
              <dl>
                <div><dt>本部闭环</dt><dd>{direction.sequelSetup.currentArcClosure}</dd></div>
                <div><dt>结尾动作 / 揭示</dt><dd>{direction.sequelSetup.finalRevealOrAction}</dd></div>
                <div><dt>下一部冲突</dt><dd>{direction.sequelSetup.nextInstallmentConflict}</dd></div>
                <div><dt>下一部目标</dt><dd>{direction.sequelSetup.nextInstallmentObjective}</dd></div>
              </dl>
            </section> : invalidLegacyHook ? <div className="story-direction-card__legacy-warning" role="note"><AlertTriangle size={16} /><div><strong>旧版本为提问式钩子，不能作为续作剧情</strong><p>该方向仍可比较，但下次重新生成会改为具体的结尾动作、下一部冲突与主角目标。</p></div></div> : legacyHook ? <section className="story-direction-card__legacy-hook"><span>续作剧情铺垫 · 旧版本</span><p>{legacyHook}</p></section> : null}
            <section className={`story-direction-compliance-detail story-direction-compliance-detail--${compliance.status.toLowerCase()}`}>
              <header><div><span>Brief 合规结果</span><h4>{complianceLabel(compliance.status)}</h4></div><small>{compliance.items.length ? `共核验 ${compliance.items.length} 条` : '无额外内容约束'}</small></header>
              {compliance.items.length ? <ul>{compliance.items.map((item) => <li key={`${item.category}-${item.item}`}><span className={`story-direction-compliance-detail__status story-direction-compliance-detail__status--${item.status.toLowerCase()}`}>{item.status === 'MET' ? '已满足' : item.status === 'CONFLICT' ? '有冲突' : '待核验'}</span><div><strong>{item.category === 'REQUIREMENT' ? '必须满足' : '必须避免'}：{item.item}</strong><p>{item.evidence}</p></div></li>)}</ul> : <p>本次 Brief 没有额外的必须满足或必须避免条目。</p>}
            </section>
            <div className="story-direction-card__risks"><strong>创作假设与风险</strong><ul>{(direction.riskNotes?.length ? direction.riskNotes : direction.assumptions).map((item) => <li key={item}>{item}</li>)}</ul></div>
          </article></Modal>
        })() : null}
        {directions.length > 0 && project.status === 'PROPOSAL_READY' ? <div className={`story-direction-actions story-direction-actions--sticky ${activeDirection && !mergeMode && packageEstimate ? 'story-direction-actions--with-confirmation' : ''}`}>
          {activeDirection && !mergeMode && packageEstimate ? <section className="story-generation-confirmation" aria-labelledby="story-generation-confirmation-title">
            <header><div><p className="eyebrow">确认前请核对</p><h3 id="story-generation-confirmation-title">确认后将生成什么？</h3></div><span>{activeDirection.title}</span></header>
            <div className="story-generation-confirmation__grid">
              <article><FileStack size={17} /><div><strong>生成资产</strong><p>{packageEstimate.assets.join('、')}</p></div></article>
              <article><Clock3 size={17} /><div><strong>预计等待</strong><p>{durationEstimateLabel(packageEstimate.estimatedSeconds)}，任务页会实时显示进度。</p></div></article>
              <article><Coins size={17} /><div><strong>积分消耗</strong><p>{packageEstimate.estimatedPoints} 积分。{packageEstimate.estimatedPoints === 0 ? '当前文本生成阶段暂不扣除积分。' : ''}</p></div></article>
              <article><LockKeyhole size={17} /><div><strong>审核闸门</strong><p>本步只生成故事设定与角色关系；确认关系后才会生成剧本。</p></div></article>
              <article><Layers3 size={17} /><div><strong>后续修改</strong><p>关系与剧本均创建新版本，不覆盖已有创作资产。</p></div></article>
            </div>
          </section> : null}
          <div className="story-direction-actions__summary"><strong>{mergeMode ? `合并模式 · 已选择 ${selected.length} 个方向` : activeDirection ? `已选择：${activeDirection.title}` : '请选择一个故事方向'}</strong><small>{mergeMode ? '选择 2–3 个方向后生成新的融合版本。' : '确认后先生成故事设定与角色关系，审核通过后再生成剧本。'}</small></div>
          <div>
            <Button onClick={toggleMergeMode} variant="secondary"><GitMerge size={16} />{mergeMode ? '退出合并模式' : '合并方向'}</Button>
            {mergeMode ? <Button disabled={acting || selected.length < 2 || project.status !== 'PROPOSAL_READY'} onClick={() => void runAction(async () => { const merged = await mergeStoryDirections(project.id, project.lockVersion, selected); await load(); setSelected([merged.id]); setExpandedDirectionId(merged.id); setMergeMode(false); setNotice('融合方向已创建，请确认后生成故事结构。') })}><GitMerge size={16} />生成融合方向</Button> : <Button disabled={acting || selected.length !== 1 || !activeDirection || project.status !== 'PROPOSAL_READY'} onClick={() => void runAction(async () => { if (!activeDirection) return; const job = await generateStoryPackage(project.id, activeDirection.version, project.lockVersion); setNotice(`故事结构生成任务已入队：${job.stage}`); navigate(`/tasks?project=${project.id}&jobType=GENERATE_STORY_STRUCTURE`) })}><Play size={16} />确认方向并生成故事结构</Button>}
          </div>
        </div> : null}
      </section>

      {latestBible ? <section aria-labelledby="story-bible-title" className="story-section story-bible-grid">
        <header className="story-bible-grid__header">
          <div><p className="eyebrow">故事设定集 · 第 {latestBible.version} 版</p><h2 id="story-bible-title">故事世界与角色事实</h2><p>集中查看后续分集大纲和剧本必须遵循的世界观、连续性规则与角色动机。</p></div>
          <dl><div><dt>核心规则</dt><dd>{bibleRules.length}</dd></div><div><dt>主要角色</dt><dd>{characters.length}</dd></div></dl>
        </header>
        <div className="story-bible-grid__overview">
          <article className="story-bible-world"><header><Globe2 size={18} /><div><h3>世界与连续性</h3></div></header><p>{stringValue(biblePayload.world)}</p></article>
          <article className="story-bible-rules"><header><BookOpenCheck size={18} /><h3>核心规则</h3></header><ol>{bibleRules.map((item, index) => <li key={item}><span>{index + 1}</span><p>{item}</p></li>)}</ol></article>
        </div>
        <section aria-labelledby="story-characters-title" className="story-bible-characters">
          <header><div><UsersRound size={18} /><h3 id="story-characters-title">角色文字设定</h3></div><small>{characters.length} 位角色</small></header>
          <div aria-label="按叙事功能筛选角色" className="story-character-tabs" role="tablist">{characterTabs.map((tab, index) => <button aria-controls="story-character-panel" aria-selected={characterFilter === tab.id} key={tab.id} onClick={() => setCharacterFilter(tab.id)} onKeyDown={(event) => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? characterTabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + characterTabs.length) % characterTabs.length; setCharacterFilter(characterTabs[nextIndex].id); (event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex])?.focus() }} role="tab" tabIndex={characterFilter === tab.id ? 0 : -1} type="button">{tab.label}<span>{tab.count}</span></button>)}</div>
          <div aria-live="polite" id="story-character-panel" role="tabpanel">{visibleCharacters.map((character) => {
            const ethnicity = characterEthnicity(character, stringValue(biblePayload.world, '')) || '未指定'
            const category = characterCategory(character)
            return <article className="story-character" data-character-category={category} key={stringValue(character.key)}>
              <header>
                <div className="story-character__identity">
                  <span aria-hidden="true" className="story-character__avatar" data-category={category}>
                    <span className="story-character__avatar-monogram">{characterInitials(character)}</span>
                    <span className="story-character__avatar-status" />
                  </span>
                  <div><span>{stringValue(character.role)}</span><strong>{stringValue(character.name)}</strong><small>{characterGenderLabel(character)} · {ethnicity}</small></div>
                </div>
                <button aria-label={`编辑${stringValue(character.name)}的角色信息`} onClick={() => openCharacterEditor(character)} type="button"><Pencil size={14} />编辑</button>
              </header>
              <p className="story-character__function">{stringValue(character.dramatic_function)}</p>
              <dl className="story-character__profile"><div><dt>年龄</dt><dd>{characterAge(character)}</dd></div><div><dt>职业</dt><dd>{characterOccupation(character)}</dd></div><div className="story-character__fact--wide"><dt>性格</dt><dd>{characterPersonality(character)}</dd></div></dl>
              <dl className="story-character__motivation"><div><dt>欲望</dt><dd>{stringValue(character.desire)}</dd></div><div><dt>秘密</dt><dd>{stringValue(character.secret)}</dd></div></dl>
            </article>
          })}</div>
        </section>
      </section> : null}

      <Modal className="modal--character-revision" description="修改不会覆盖当前版本。系统先审核故事逻辑与人物关系，确认影响后再创建同步修改版。" footer={<><Button disabled={characterRevisionBusy} onClick={() => { setEditingCharacter(null); setCharacterEditDraft(null); setCustomEthnicityActive(false); setCharacterRevisionReview(null); setCharacterRevisionError(null) }} variant="secondary">取消</Button>{characterRevisionReview ? <><Button disabled={characterRevisionBusy} onClick={() => { setCharacterRevisionReview(null); setCharacterRevisionError(null) }} variant="secondary">返回编辑</Button><Button disabled={characterRevisionBusy} onClick={() => void confirmCharacterEdit()}>{characterRevisionBusy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}确认修改并同步</Button></> : <Button disabled={characterRevisionBusy || !characterEditDraft?.name || !characterEditDraft.role || !characterEditDraft.age || !characterEditDraft.height || !characterEditDraft.occupation || !characterEditDraft.personality} onClick={() => void runCharacterReview()}>{characterRevisionBusy ? <LoaderCircle className="spin" size={16} /> : <GitMerge size={16} />}检查修改影响</Button>}</>} onClose={() => { setEditingCharacter(null); setCharacterEditDraft(null); setCustomEthnicityActive(false); setCharacterRevisionReview(null); setCharacterRevisionError(null) }} open={Boolean(editingCharacter && characterEditDraft)} title={editingCharacter ? `编辑角色 · ${stringValue(editingCharacter.name)}` : '编辑角色'}>
        {characterRevisionError ? <div className="character-revision-error" role="alert"><AlertTriangle size={17} /><div><strong>未能完成本次操作</strong><p>{characterRevisionError}</p></div></div> : null}
        {characterEditDraft ? <div className="character-revision-form">
          <div className="character-revision-form__grid">
            <label>姓名<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, name: event.target.value } : current)} value={characterEditDraft.name} /></label>
            <label>角色定位<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, role: event.target.value } : current)} value={characterEditDraft.role} /></label>
            <label>年龄<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, age: event.target.value } : current)} value={characterEditDraft.age} /></label>
            <label>身高<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, height: event.target.value } : current)} placeholder="例如：170 cm；不确定可填写未指定" value={characterEditDraft.height} /></label>
            <label>职业<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, occupation: event.target.value } : current)} value={characterEditDraft.occupation} /></label>
            <label>性别<SelectControl aria-label="性别" disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, gender: event.target.value as CharacterEditDraft['gender'] } : current)} value={characterEditDraft.gender}><option value="unspecified">未指定</option><option value="female">女性</option><option value="male">男性</option><option value="nonbinary">非二元</option></SelectControl></label>
            <label>族裔／文化背景<SelectControl aria-label="族裔／文化背景" disabled={Boolean(characterRevisionReview)} onChange={(event) => {
              const nextValue = event.target.value
              const custom = nextValue === CUSTOM_ETHNICITY_VALUE
              setCustomEthnicityActive(custom)
              setCharacterEditDraft((current) => current ? {
                ...current,
                ethnicity: custom ? '' : nextValue,
                visualNotes: syncVisualNotesWithEthnicity(current.visualNotes, custom ? '' : nextValue),
              } : current)
            }} searchable={ethnicityOptions.length >= 8} value={customEthnicityActive ? CUSTOM_ETHNICITY_VALUE : characterEditDraft.ethnicity}><option value="">未指定</option>{ethnicityOptions.map((suggestion) => <option key={suggestion} value={suggestion}>{suggestion}</option>)}<option value={CUSTOM_ETHNICITY_VALUE}>其他／自定义背景</option></SelectControl>{customEthnicityActive ? <><input aria-label="自定义族裔或文化背景" disabled={Boolean(characterRevisionReview)} onChange={(event) => {
              const nextValue = event.target.value
              setCharacterEditDraft((current) => current ? {
                ...current,
                ethnicity: nextValue,
                visualNotes: syncVisualNotesWithEthnicity(current.visualNotes, nextValue),
              } : current)
            }} placeholder="例如：爱尔兰裔美国人、意大利裔美国人或其他具体亚洲背景" value={characterEditDraft.ethnicity} /><small className="character-revision-form__note">只用于外观与文化设定，不会推断性格、职业或剧情功能。</small></> : null}</label>
            <label className="character-revision-form__wide">性格关键词（用顿号分隔）<input disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, personality: event.target.value } : current)} value={characterEditDraft.personality} /></label>
            <label className="character-revision-form__wide">剧情功能<textarea disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, dramaticFunction: event.target.value } : current)} rows={2} value={characterEditDraft.dramaticFunction} /></label>
            <label>欲望<textarea disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, desire: event.target.value } : current)} rows={3} value={characterEditDraft.desire} /></label>
            <label>恐惧<textarea disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, fear: event.target.value } : current)} rows={3} value={characterEditDraft.fear} /></label>
            <label className="character-revision-form__wide">秘密<textarea disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, secret: event.target.value } : current)} rows={2} value={characterEditDraft.secret} /></label>
            <label className="character-revision-form__wide">视觉特征<textarea disabled={Boolean(characterRevisionReview)} onChange={(event) => setCharacterEditDraft((current) => current ? { ...current, visualNotes: event.target.value } : current)} rows={3} value={characterEditDraft.visualNotes} /><small className="character-revision-form__note">更改族裔／文化背景时会同步身份约束，同时保留年龄、发型、服装和表演状态等个体特征；你仍可继续修改。</small></label>
          </div>
          {characterRevisionReview ? <section className={`character-revision-review is-${characterRevisionReview.review.verdict.toLowerCase()}`}><header><div><span>{characterRevisionReview.review.verdict === 'CONFLICT' ? '发现逻辑冲突' : '审核通过'}</span><h3>{characterRevisionReview.review.summary}</h3></div><small>{characterRevisionReview.provider}/{characterRevisionReview.model}</small></header>{characterRevisionReview.review.issues.length ? <ul>{characterRevisionReview.review.issues.map((issue) => <li data-severity={issue.severity.toLowerCase()} key={issue.code}><strong>{issue.severity === 'BLOCKER' ? '冲突' : issue.severity === 'WARNING' ? '提醒' : '信息'}</strong><div><p>{issue.message}</p><small>{issue.suggestion}</small></div></li>)}</ul> : <p>未发现需要阻止修改的故事逻辑问题。</p>}<div className="character-revision-impact"><div><span>人物关系</span><strong>{characterRevisionReview.affected.relationshipCount} 条</strong></div><div><span>分集大纲</span><strong>{characterRevisionReview.affected.outlineCount} 版</strong></div><div><span>剧本</span><strong>{characterRevisionReview.affected.scriptCount} 版</strong></div></div><p>确认后将创建新的故事设定和关系草稿；旧版本继续保留。重新确认关系后，系统才会生成同步后的故事线与剧本。</p></section> : null}
        </div> : null}
      </Modal>

      {workspace.relationshipGraphVersions.length ? <RelationshipGraphSection
        characters={relationshipCharacters}
        focusTarget={relationshipFocus}
        onGraphChanged={applyRelationshipGraph}
        onCharacterVisualsReady={(route, characterCount) => {
          setNotice(`已为 ${characterCount} 位角色准备结构化视觉档案。`)
          navigate(route)
        }}
        versions={workspace.relationshipGraphVersions}
      /> : null}

      {workspace.relationshipGraphStale ? <div className="story-relationship-stale" role="alert"><AlertTriangle size={18} /><div><strong>当前剧本使用的是旧关系版本</strong><p>新的关系修改版尚未批准。当前剧本可以查看，但不能批准；请先完成关系修改并重新生成剧本。</p></div></div> : null}

      {latestOutline && latestScript ? <>
        <section className="story-section story-outline-section"><article><p className="eyebrow">分集大纲 · 第 {latestOutline.episodeOrdinal} 集</p><h2>{stringValue(outlinePayload.title)}</h2><dl className="story-outline"><div><dt>开场钩子</dt><dd>{stringValue(outlinePayload.hook)}</dd></div><div><dt>目标</dt><dd>{stringValue(outlinePayload.objective)}</dd></div><div><dt>冲突</dt><dd>{stringValue(outlinePayload.conflict)}</dd></div><div><dt>反转</dt><dd>{stringValue(outlinePayload.turn)}</dd></div><div><dt>悬念</dt><dd>{stringValue(outlinePayload.cliffhanger)}</dd></div></dl></article></section>

        <section className="story-section">
          <div className="section-heading"><div><p className="eyebrow">短剧创作引擎 · {stringValue(enginePayload.formula_version, '待生成')}</p><h2>短剧创作引擎</h2><p>{stringValue(enginePayload.formula, '明确的角色欲望 × 高密度推进 × 高频情绪兑现 × 递进式因果反转 × 阶段性闭环与续作悬念')}</p></div></div>
          <div className="short-drama-contract-grid">
            <article><span>角色欲望</span><p>{stringValue(enginePayload.protagonist_desire)}</p></article>
            <article><span>高密度推进</span><p>{stringValue(enginePayload.pace_strategy)}</p></article>
            <article><span>爽点兑现</span><p>{stringValue(enginePayload.payoff_strategy)}</p></article>
            <article><span>阶段闭环</span><p>{stringValue(enginePayload.stage_closure)}</p></article>
            <article><span>续作钩子</span><p>{stringValue(enginePayload.continuation_hook)}</p></article>
          </div>
          <div className="short-drama-engine-detail">
            <article><h3>递进式反转链</h3><ol>{reversalChain.map((item) => <li key={item}>{item}</li>)}</ol></article>
            <article><h3>节拍表</h3><ol>{engineBeats.map((beat) => <li key={String(beat.sequence)}><strong>{localizeDisplayText(stringValue(beat.beat_type))}</strong><span>{(Number(beat.at_ms) / 1000).toFixed(1)} 秒 · {stringValue(beat.description)}</span><small>{stringValue(beat.story_state_change)}</small></li>)}</ol></article>
          </div>
        </section>

        <section className="story-section">
          <div className="section-heading"><div><p className="eyebrow">爆款叙事引擎 · {stringValue(breakoutPayload.formula_version, '待生成')}</p><h2>从持续误判到情感秩序重建</h2><p>{stringValue(breakoutPayload.formula, '弱势外壳 × 顶级内核 × 持续误判 × 分段认证 × 关系重排 × 情感秩序重建 × 可续作单元')}</p></div></div>
          <div className="breakout-contract-grid">
            <article><span>弱势外壳</span><p>{stringValue(breakoutPayload.vulnerable_shell)}</p></article>
            <article><span>顶级内核</span><p>{stringValue(breakoutPayload.elite_core)}</p></article>
            <article><span>情感秩序重建</span><p>{stringValue(emotionalOrder.old_order)} → {stringValue(emotionalOrder.new_order)}</p><small>{stringValue(emotionalOrder.emotional_payoff)}</small></article>
            <article><span>可续作单元</span><p>{stringValue(sequelUnit.current_unit_closure)}</p><small>下一单元：{stringValue(sequelUnit.next_unit_trigger)}</small></article>
          </div>
          <div className="breakout-engine-detail">
            <article><h3>持续误判链</h3><ol>{misjudgmentChain.map((step) => <li key={String(step.sequence)}><strong>{stringValue(step.observer_key)}</strong><span>{stringValue(step.mistaken_belief)}</span><small>行动：{stringValue(step.resulting_action)} · 主角代价：{stringValue(step.cost_to_protagonist)} · 纠偏伏笔：{stringValue(step.correction_seed)}</small></li>)}</ol></article>
            <article><h3>分段认证阶梯</h3><ol>{authenticationLadder.map((stage) => <li key={String(stage.sequence)}><strong>{localizeDisplayText(stringValue(stage.proof_type))}</strong><span>{stringValue(stage.proof)}</span><small>{stringValue(stage.status_shift)} · 剩余误判：{stringValue(stage.remaining_misjudgment)}</small></li>)}</ol></article>
            <article><h3>关系重排</h3><ol>{relationshipReorders.map((relationship) => <li key={stringValue(relationship.relationship_key)}><strong>{stringValue(relationship.relationship_key)}</strong><span>{stringValue(relationship.before)} → {stringValue(relationship.after)}</span><small>{stringValue(relationship.emotional_consequence)}</small></li>)}</ol></article>
          </div>
        </section>

        <section className="story-section">
          <div className="section-heading"><div><p className="eyebrow">剧本 · 第 {latestScript.episodeOrdinal} 集 · 第 {latestScript.version} 版</p><h2>结构化首集剧本</h2><p>{Math.round(latestScript.estimatedDurationMs / 1000)} 秒 · 内容评审：{criticStatus} · {latestScript.provider}/{latestScript.model}</p></div><StatusBadge status={latestScript.status} /></div>
          <div className="script-scene-list">
            {latestScript.scenes.map((scene) => {
              const relationshipBeat = relationshipBeatForScene(scene.ordinal)
              return (
                <article key={scene.id}>
                  <header>
                    <span>{scene.ordinal.toString().padStart(2, '0')}</span>
                    <div>
                      <h3>{localizeDisplayText(scene.heading)}</h3>
                      <p>{scene.location} · {localizeDisplayText(scene.timeOfDay)} · {localizeDisplayText(scene.emotion)}</p>
                    </div>
                    <small>{(scene.durationMs / 1000).toFixed(1)} 秒</small>
                  </header>
                  <p className="script-purpose">{scene.purpose}</p>
                  <div className="script-lines">
                    {scene.lines.map((line) => (
                      <div className="script-line" key={line.id}>
                        <strong>{stringValue(characters.find((character) => stringValue(character.key) === line.speakerKey)?.name, line.speakerKey)}</strong>
                        <p
                          data-script-line-text
                          onMouseUp={(event) => captureScriptSelection(event, line)}
                        >
                          {line.text}
                        </p>
                        <small>{localizeDisplayText(line.lineType)} · {localizeDisplayText(line.emotion)} · {(line.estimatedDurationMs / 1000).toFixed(1)} 秒</small>
                        {renderScriptRewriteDiff(scene.ordinal, line.ordinal)}
                      </div>
                    ))}
                  </div>
                  <footer>
                    <span>背景音乐：{scene.bgmIntent}</span>
                    <span>音效：{scene.sfxIntents.join(' / ') || '—'}</span>
                    {relationshipBeat && scriptRelationshipGraph ? (
                      <button
                        className="script-relationship-link"
                        onClick={() => setRelationshipFocus({
                          graphId: scriptRelationshipGraph.id,
                          relationshipKey: relationshipBeat.relationshipKey,
                          beatOrdinal: relationshipBeat.ordinal,
                          requestId: Date.now(),
                        })}
                        type="button"
                      >
                        <Workflow size={13} />查看对应关系变化
                      </button>
                    ) : null}
                  </footer>
                </article>
              )
            })}
          </div>
          <div className="story-direction-actions"><span>{workspace.relationshipGraphStale ? '关系修改版尚未批准，当前剧本已过期。' : '批准后锁定第 2 阶段，并让全部剧本角色进入前期制作。'}</span><Button disabled={acting || workspace.relationshipGraphStale || latestScript.status !== 'READY_FOR_REVIEW' || project.status !== 'SCRIPT_READY'} onClick={() => setApproveScriptOpen(true)}><Check size={16} />{workspace.relationshipGraphStale ? '关系更新后才能批准' : '批准首集剧本'}</Button></div>
        </section>
      </> : null}

      <ImpactConfirmModal
        confirmLabel="批准首集剧本"
        description="批准后剧本版本将冻结，修改需创建修改版。"
        items={[
          { icon: <LockKeyhole size={16} />, title: '锁定第 2 阶段', detail: `剧本第 ${latestScript?.version ?? 1} 版将成为后续制作的文本基线。` },
          { icon: <UsersRound size={16} />, title: '角色进入前期制作', detail: `${characters.length} 位剧本角色将触发视觉档案与前期资产任务。` },
          { icon: <Sparkles size={16} />, title: '任务入队', detail: '批准后会跳转到任务页，等待角色与前期资产就绪。' },
        ]}
        loading={acting}
        onClose={() => { if (!acting) setApproveScriptOpen(false) }}
        onConfirm={() => void confirmApproveScript()}
        open={approveScriptOpen}
        subtitle="确认剧本、关系基线与角色设定无误后再继续。"
        title="批准首集剧本？"
      />

      {scriptRewriteMenuOpen && scriptSelection ? createPortal((
        <div
          aria-label="剧本改写"
          className="script-selection-menu"
          onMouseDown={(event) => event.preventDefault()}
          role="toolbar"
          style={{ left: scriptSelection.left, top: scriptSelection.top }}
        >
          <header>
            <span>{scriptRewriteMenuMode === 'ACTIONS' ? '处理选中文字' : scriptRewriteMenuMode === 'TONE' ? '调整语气' : '自定义改写'}</span>
            <button
              aria-label="关闭"
              disabled={scriptRewriteBusy}
              onClick={() => setScriptRewriteMenuOpen(false)}
              type="button"
            >
              <X size={13} />
            </button>
          </header>
          {scriptRewriteMenuMode === 'ACTIONS' ? (
            <div className="script-selection-menu__actions">
              <button disabled={scriptRewriteBusy} onClick={() => void generateScriptRewrite('REWRITE')} type="button"><WandSparkles size={14} />改写</button>
              <button disabled={scriptRewriteBusy} onClick={() => void generateScriptRewrite('SHORTEN')} type="button"><Scissors size={14} />缩短</button>
              <button disabled={scriptRewriteBusy} onClick={() => void generateScriptRewrite('INTENSIFY_CONFLICT')} type="button"><Flame size={14} />增强冲突</button>
              <button disabled={scriptRewriteBusy} onClick={() => setScriptRewriteMenuMode('TONE')} type="button"><MessageCircle size={14} />调整语气</button>
              <button disabled={scriptRewriteBusy} onClick={() => setScriptRewriteMenuMode('CUSTOM')} type="button"><Sparkles size={14} />自定义……</button>
            </div>
          ) : scriptRewriteMenuMode === 'TONE' ? (
            <div className="script-selection-menu__choices">
              {['克制', '强硬', '温柔', '讽刺'].map((tone) => (
                <button disabled={scriptRewriteBusy} key={tone} onClick={() => void generateScriptRewrite('ADJUST_TONE', { tone })} type="button">{tone}</button>
              ))}
              <button className="script-selection-menu__back" onClick={() => setScriptRewriteMenuMode('ACTIONS')} type="button">返回</button>
            </div>
          ) : (
            <div className="script-selection-menu__custom">
              <textarea
                autoFocus
                onChange={(event) => setScriptRewriteCustomInstruction(event.target.value)}
                placeholder="例如：更口语、更有停顿感"
                rows={3}
                value={scriptRewriteCustomInstruction}
              />
              <div>
                <button onClick={() => setScriptRewriteMenuMode('ACTIONS')} type="button">返回</button>
                <button
                  disabled={scriptRewriteBusy || !scriptRewriteCustomInstruction.trim()}
                  onClick={() => void generateScriptRewrite('CUSTOM', {
                    customInstruction: scriptRewriteCustomInstruction.trim(),
                  })}
                  type="button"
                >
                  {scriptRewriteBusy ? <LoaderCircle className="spin" size={13} /> : null}
                  生成
                </button>
              </div>
            </div>
          )}
          {scriptRewriteBusy && scriptRewriteMenuMode !== 'CUSTOM' ? <div className="script-selection-menu__loading"><LoaderCircle className="spin" size={14} />正在生成…</div> : null}
          {scriptRewriteError ? <p className="script-selection-menu__error">{scriptRewriteError}</p> : null}
        </div>
      ), document.body) : null}
    </div>
  )
}
