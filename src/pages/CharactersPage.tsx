import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  Dna,
  Eye,
  GitCompare,
  History,
  Images,
  LoaderCircle,
  LockKeyhole,
  Maximize2,
  Monitor,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserRound,
  UserRoundCheck,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  applyCharacterVisualChange,
  deleteCharacterVisualCandidate,
  fetchCharacterVisuals,
  generateCharacterIdentityView,
  generateCharacterVisualCandidates,
  lockCharacterVisualIdentity,
  retryPersistedJob,
  restoreCharacterVisualIdentity,
  selectCharacterVisualCandidate,
  updateCharacterVisualProfile,
  type CharacterVisualRecord,
  type CharacterVisualWorkspace,
} from '../api/client'
import { Button, getStatusLabel, Modal, PageHeader, StatusBadge } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { buildCandidateGenerationSlots } from '../utils/candidateGenerationSlots'
import { buildCharacterVisualFacts } from '../utils/characterVisualSummary'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const STATUS_LABELS: Record<string, string> = {
  NOT_GENERATED: '尚未生成',
  GENERATING: '正在生成',
  CANDIDATES_READY: '待选择形象',
  PENDING_SELECTION: '待选择形象',
  PENDING_REVIEW: '待确认形象',
  REVIEW_REQUIRED: '待确认形象',
  LOCKED: '已锁定',
  TEXT_CHANGED: '文字设定已变化',
  RE_REVIEW_REQUIRED: '需要重新审核',
  GENERATION_FAILED: '生成失败',
}

const VIEW_LABELS: Record<string, string> = {
  FRONT: '正面',
  THREE_QUARTER: '45° 侧面',
  PROFILE: '侧面',
  FULL_BODY: '全身',
  EXPRESSIONS: '基础表情组',
}

const DIGITAL_ENTITY_VIEW_LABELS: Record<string, string> = {
  FRONT: '主界面',
  THREE_QUARTER: '运行状态',
  PROFILE: '警报状态',
  FULL_BODY: '场景载体',
  EXPRESSIONS: '状态变化组',
}

function dossierViewLabel(viewType: string, entityKind = 'HUMAN'): string {
  const labels = entityKind === 'DIGITAL_ENTITY' ? DIGITAL_ENTITY_VIEW_LABELS : VIEW_LABELS
  return labels[viewType] ?? viewType
}

const VISUAL_SOURCE_FIELD_LABELS: Record<string, string> = {
  character: '角色',
  name: '姓名',
  role: '角色定位',
  age: '年龄',
  gender: '性别',
  ethnicity: '种族/族裔',
  occupation: '职业',
  dramatic_function: '剧情功能',
  visual_notes: '视觉特征',
  personality: '性格关键词',
}

const DOSSIER_VIEW_TYPES = [
  'FRONT',
  'THREE_QUARTER',
  'PROFILE',
  'FULL_BODY',
  'EXPRESSIONS',
] as const

const ACTIVE_DOSSIER_JOB_STATUSES = new Set(['PENDING', 'RETRY_WAIT', 'RUNNING'])
const FAILED_DOSSIER_JOB_STATUSES = new Set(['FAILED', 'CANCELLED'])

const FAMILY_SIMILARITY_META: Record<string, { label: string; strength: number }> = {
  LOW: { label: '低度相似', strength: 1 },
  MEDIUM: { label: '中度相似', strength: 2 },
  HIGH: { label: '高度相似', strength: 3 },
  VERY_HIGH: { label: '双胞胎级', strength: 4 },
}

const CANDIDATE_PLACEHOLDERS = [
  { label: '候选方向 1', description: '生成时动态抽取' },
  { label: '候选方向 2', description: '生成时动态抽取' },
  { label: '候选方向 3', description: '生成时动态抽取' },
] as const

const CHARACTER_WORKFLOW_STEPS = [
  '确认设定',
  '选择形象',
  '检查基准',
  '确认锁定',
] as const

function CandidateGenerationControl({
  disabled,
  generating,
  hasCandidates,
  menuId,
  onGenerate,
  visualScheme = false,
}: {
  disabled: boolean
  generating: boolean
  hasCandidates: boolean
  menuId: string
  onGenerate: (count: 1 | 3) => void
  visualScheme?: boolean
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const firstOptionRef = useRef<HTMLButtonElement>(null)
  const variant = hasCandidates ? 'secondary' : 'primary'

  useEffect(() => {
    if (!open) return
    firstOptionRef.current?.focus({ preventScroll: true })
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('pointerdown', closeOnOutsidePointer, true)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('pointerdown', closeOnOutsidePointer, true)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  const generate = (count: 1 | 3) => {
    setOpen(false)
    onGenerate(count)
  }

  return (
    <div className="candidate-generation-control" ref={rootRef}>
      <Button
        className="candidate-generation-control__main"
        disabled={disabled}
        onClick={() => generate(3)}
        variant={variant}
      >
        {generating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
        {hasCandidates ? `重新生成 3 个${visualScheme ? '视觉方案' : '方向'}` : `生成 3 个${visualScheme ? '视觉方案' : '形象方向'}`}
      </Button>
      <Button
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="选择生成数量"
        className="candidate-generation-control__toggle"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        variant={variant}
      >
        <ChevronDown aria-hidden="true" size={15} />
      </Button>
      {open ? <div className="candidate-generation-menu" id={menuId} role="menu">
        <button onClick={() => generate(1)} ref={firstOptionRef} role="menuitem" type="button"><strong><span>1</span>只生成 1 个{visualScheme ? '视觉方案' : '方向'}</strong><small>快速补充一张新候选</small></button>
        <button onClick={() => generate(3)} role="menuitem" type="button"><strong><span>3</span>生成 3 个{visualScheme ? '视觉方案' : '方向'}</strong><small>一次比较三个差异方向</small></button>
      </div> : null}
    </div>
  )
}

function familyRelationshipLabel(character?: CharacterVisualRecord): string {
  const role = character?.role ?? ''
  if (/母亲|妈妈/.test(role)) return '母亲'
  if (/父亲|爸爸/.test(role)) return '父亲'
  if (/女儿/.test(role)) return '女儿'
  if (/儿子/.test(role)) return '儿子'
  if (/姐姐|姊姊/.test(role)) return '姐姐'
  if (/妹妹/.test(role)) return '妹妹'
  if (/哥哥/.test(role)) return '哥哥'
  if (/弟弟/.test(role)) return '弟弟'
  if (/祖母|奶奶|外婆/.test(role)) return '祖母'
  if (/祖父|爷爷|外公/.test(role)) return '祖父'
  return '亲属'
}

interface ProfileDraft {
  entityKind: string
  embodiment: string
  age: string
  height: string
  genderExpression: string
  region: string
  era: string
  occupation: string
  socialClass: string
  storyIdentity: string
  faceShape: string
  facialFeatures: string
  browEyeShape: string
  noseShape: string
  mouthCorner: string
  skinTone: string
  hairstyle: string
  hairTexture: string
  bodyType: string
  identifyingFeatures: string
  lifeMarks: string
  expression: string
  gaze: string
  posture: string
  movement: string
  wardrobe: string
  materials: string
  colors: string
  shoesBags: string
  accessories: string
  forbiddenElements: string
  negativeConstraints: string
}

interface IdentityViewAction {
  characterId: string
  characterName: string
  identityVersionId: string
  viewType: string
  viewLabel: string
  assetUrl: string
  mode: 'adjust' | 'regenerate'
  createsNewVersion?: boolean
}

interface IdentityImageViewer {
  characterId: string
  characterName: string
  identityVersionId: string
  viewType: string
  viewLabel: string
  assetUrl: string
  readOnly?: boolean
  createsNewVersion?: boolean
}

interface IdentityVersionGallery {
  characterId: string
  characterName: string
  identityVersionId: string
  identityVersion: number
  entityKind: string
  assets: CharacterVisualRecord['identities'][number]['assets']
}

interface CandidateImageViewer {
  characterId: string
  candidateId: string
  characterName: string
  variantLabel: string
  assetUrl: string
  selectable: boolean
  generationPrompt: string
}

function DossierGenerationMark() {
  return <div aria-hidden="true" className="character-dossier-generation-mark">
    <UserRound size={32} strokeWidth={1.35} />
    <Sparkles size={17} strokeWidth={1.7} />
  </div>
}

function profileDraft(character: CharacterVisualRecord): ProfileDraft | null {
  const profile = character.profile
  if (!profile) return null
  const identity = profile.identityFields
  const appearance = profile.appearanceFields
  const personality = profile.personalityVisualization
  const styling = profile.stylingFields
  const listText = (value: string | string[] | undefined) => (
    Array.isArray(value) ? value.join('、') : value ?? ''
  )
  return {
    entityKind: identity.entity_kind ?? 'HUMAN',
    embodiment: identity.embodiment ?? '',
    age: identity.age ?? '',
    height: appearance.height ?? '',
    genderExpression: identity.gender_expression ?? '',
    region: identity.region ?? '',
    era: identity.era ?? '',
    occupation: identity.occupation ?? '',
    socialClass: identity.social_class ?? '',
    storyIdentity: identity.story_identity ?? '',
    faceShape: appearance.face_shape ?? '',
    facialFeatures: appearance.facial_features ?? '',
    browEyeShape: appearance.brow_eye_shape ?? '',
    noseShape: appearance.nose_shape ?? '',
    mouthCorner: appearance.mouth_corner ?? '',
    skinTone: appearance.skin_tone ?? '',
    hairstyle: appearance.hairstyle ?? '',
    hairTexture: appearance.hair_texture ?? '',
    bodyType: appearance.body_type ?? '',
    identifyingFeatures: appearance.identifying_features ?? '',
    lifeMarks: appearance.life_marks ?? '',
    expression: personality.expression ?? '',
    gaze: personality.gaze ?? '',
    posture: personality.posture ?? '',
    movement: personality.movement ?? '',
    wardrobe: listText(styling.wardrobe),
    materials: listText(styling.materials),
    colors: listText(styling.colors),
    shoesBags: listText(styling.shoes_bags),
    accessories: listText(styling.accessories),
    forbiddenElements: listText(styling.forbidden_elements),
    negativeConstraints: profile.negativeConstraints.join('、'),
  }
}

function splitItems(value: string): string[] {
  return value.split(/[、,，；;]/).map((item) => item.trim()).filter(Boolean)
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? localizeDisplayText(status)
}

function dossierViewOrder(viewType: string): number {
  const index = DOSSIER_VIEW_TYPES.findIndex((item) => item === viewType)
  return index === -1 ? DOSSIER_VIEW_TYPES.length : index
}

function compactDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`
}

function characterReferenceImage(character: CharacterVisualRecord): string | undefined {
  const activeIdentity = character.identities.find(
    (identity) => identity.id === character.lockedIdentityVersionId,
  ) ?? character.identities.at(-1)
  return activeIdentity?.sourceCandidateAssetUrl
    ?? character.candidates.find((candidate) => candidate.selected)?.assetUrl
    ?? character.candidates.at(-1)?.assetUrl
}

function characterScrollOffset(): number {
  if (!window.matchMedia('(max-width: 1180px)').matches) return 92
  const locatorHeight = document.querySelector<HTMLElement>('.character-locator')
    ?.getBoundingClientRect().height ?? 0
  const topbarHeight = window.matchMedia('(max-width: 620px)').matches ? 68 : 76
  return topbarHeight + locatorHeight + 12
}

export function CharactersPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [workspace, setWorkspace] = useState<CharacterVisualWorkspace | null>(null)
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [compare, setCompare] = useState<Record<string, boolean>>({})
  const [refinements, setRefinements] = useState<Record<string, string>>({})
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ProfileDraft | null>(null)
  const [identityViewAction, setIdentityViewAction] = useState<IdentityViewAction | null>(null)
  const [identityViewNote, setIdentityViewNote] = useState('')
  const [identityImageViewer, setIdentityImageViewer] = useState<IdentityImageViewer | null>(null)
  const [identityImageZoom, setIdentityImageZoom] = useState(100)
  const [identityVersionGallery, setIdentityVersionGallery] = useState<IdentityVersionGallery | null>(null)
  const [candidateImageViewer, setCandidateImageViewer] = useState<CandidateImageViewer | null>(null)
  const [candidateImageZoom, setCandidateImageZoom] = useState(100)
  const [candidatePromptDraft, setCandidatePromptDraft] = useState('')
  const [candidateGenerationCounts, setCandidateGenerationCounts] = useState<Record<string, 1 | 3>>({})
  const [identityReviewSelection, setIdentityReviewSelection] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCharacterId, setActiveCharacterId] = useState('')
  const characterIdKey = workspace?.characters.map((character) => character.id).join('|') ?? ''

  const refresh = useCallback(async () => {
    if (!projectId) return
    const next = await fetchCharacterVisuals(projectId)
    setWorkspace(next)
    setActiveCharacterId((current) => (
      next.characters.some((character) => character.id === current)
        ? current
        : next.characters[0]?.id ?? ''
    ))
    setSelected((current) => Object.fromEntries(next.characters.map((character) => [
      character.id,
      character.candidates.some((candidate) => candidate.id === current[character.id])
        ? current[character.id]
        : (
          character.identities.at(-1)?.sourceCandidateId
          ?? character.candidates.find((candidate) => candidate.selected)?.id
          ?? ''
        ),
    ])))
  }, [projectId])

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        await refresh()
        if (active) setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '角色形象数据读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const interval = window.setInterval(load, 3000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [refresh])

  useEffect(() => {
    if (loading || !characterIdKey) return
    const characterIds = characterIdKey.split('|')
    const elements = characterIds
      .map((characterId) => document.getElementById(`character-${characterId}`))
      .filter((element): element is HTMLElement => Boolean(element))
    if (!elements.length) return

    const hashAnchor = decodeURIComponent(window.location.hash.slice(1))
    const hashCharacterId = hashAnchor.startsWith('character-')
      ? hashAnchor.slice('character-'.length)
      : ''
    const initialCharacterId = characterIds.includes(hashCharacterId)
      ? hashCharacterId
      : characterIds[0]
    setActiveCharacterId(initialCharacterId)

    let updateFrame = 0
    const updateActiveCharacter = () => {
      window.cancelAnimationFrame(updateFrame)
      updateFrame = window.requestAnimationFrame(() => {
        const guideLine = characterScrollOffset()
        const current = elements.find((element) => {
          const bounds = element.getBoundingClientRect()
          return bounds.top <= guideLine && bounds.bottom > guideLine
        }) ?? elements.find((element) => element.getBoundingClientRect().top > guideLine)
          ?? elements.at(-1)
        if (current?.dataset.characterId) setActiveCharacterId(current.dataset.characterId)
      })
    }

    const observer = new IntersectionObserver(updateActiveCharacter, {
      rootMargin: '-88px 0px -68% 0px',
      threshold: [0, 0.01],
    })
    const startTracking = () => {
      elements.forEach((element) => observer.observe(element))
      window.addEventListener('resize', updateActiveCharacter)
      window.addEventListener('scroll', updateActiveCharacter, { passive: true })
      updateActiveCharacter()
    }
    const positionHashTarget = () => {
      if (!hashCharacterId) return
      const target = document.getElementById(`character-${hashCharacterId}`)
      if (!target) return
      window.scrollTo({
        top: Math.max(
          0,
          target.getBoundingClientRect().top + window.scrollY - characterScrollOffset(),
        ),
      })
      setActiveCharacterId(hashCharacterId)
    }
    const initialTimer = window.setTimeout(() => {
      positionHashTarget()
      startTracking()
    }, hashCharacterId ? 120 : 0)
    const hashRetryTimer = hashCharacterId
      ? window.setTimeout(() => {
          positionHashTarget()
          updateActiveCharacter()
        }, 600)
      : 0

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateActiveCharacter)
      window.removeEventListener('scroll', updateActiveCharacter)
      window.clearTimeout(initialTimer)
      window.clearTimeout(hashRetryTimer)
      window.cancelAnimationFrame(updateFrame)
    }
  }, [characterIdKey, loading])

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key)
    setError(null)
    try {
      await action()
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请稍后重试')
    } finally {
      setBusy(null)
    }
  }

  function openIdentityViewAction(action: IdentityViewAction) {
    setIdentityViewAction(action)
    setIdentityViewNote('')
  }

  function openIdentityImageViewer(viewer: IdentityImageViewer) {
    setIdentityImageViewer(viewer)
    setIdentityImageZoom(100)
  }

  function closeIdentityImageViewer() {
    setIdentityImageViewer(null)
    setIdentityImageZoom(100)
  }

  function regenerateIdentityImageViewer() {
    if (!identityImageViewer || identityImageViewer.readOnly) return
    const viewer = identityImageViewer
    closeIdentityImageViewer()
    window.setTimeout(() => {
      openIdentityViewAction({
        characterId: viewer.characterId,
        characterName: viewer.characterName,
        identityVersionId: viewer.identityVersionId,
        viewType: viewer.viewType,
        viewLabel: viewer.viewLabel,
        assetUrl: viewer.assetUrl,
        mode: 'regenerate',
        createsNewVersion: viewer.createsNewVersion,
      })
    }, 0)
  }

  function openIdentityGalleryAsset(
    asset: IdentityVersionGallery['assets'][number],
  ) {
    if (!identityVersionGallery) return
    const gallery = identityVersionGallery
    setIdentityVersionGallery(null)
    window.setTimeout(() => {
      openIdentityImageViewer({
        characterId: gallery.characterId,
        characterName: gallery.characterName,
        identityVersionId: gallery.identityVersionId,
        viewType: asset.viewType,
        viewLabel: dossierViewLabel(asset.viewType, gallery.entityKind),
        assetUrl: asset.assetUrl,
        createsNewVersion: true,
      })
    }, 0)
  }

  function regenerateIdentityGalleryAsset(
    asset: IdentityVersionGallery['assets'][number],
  ) {
    if (!identityVersionGallery) return
    const gallery = identityVersionGallery
    setIdentityVersionGallery(null)
    window.setTimeout(() => {
      openIdentityViewAction({
        characterId: gallery.characterId,
        characterName: gallery.characterName,
        identityVersionId: gallery.identityVersionId,
        viewType: asset.viewType,
        viewLabel: dossierViewLabel(asset.viewType, gallery.entityKind),
        assetUrl: asset.assetUrl,
        mode: 'regenerate',
        createsNewVersion: true,
      })
    }, 0)
  }

  function openCandidateImageViewer(viewer: CandidateImageViewer) {
    setCandidateImageViewer(viewer)
    setCandidateImageZoom(100)
    setCandidatePromptDraft(viewer.generationPrompt)
  }

  function closeCandidateImageViewer() {
    setCandidateImageViewer(null)
    setCandidateImageZoom(100)
    setCandidatePromptDraft('')
  }

  function selectCandidateFromViewer() {
    if (!candidateImageViewer?.selectable) return
    setSelected((current) => ({
      ...current,
      [candidateImageViewer.characterId]: candidateImageViewer.candidateId,
    }))
    closeCandidateImageViewer()
  }

  async function regenerateCandidateFromPrompt() {
    if (!projectId || !workspace || !candidateImageViewer?.selectable) return
    const prompt = candidatePromptDraft.trim()
    if (prompt.length < 20) return
    const character = workspace.characters.find(
      (item) => item.id === candidateImageViewer.characterId,
    )
    if (!character?.profile) return
    const viewer = candidateImageViewer
    setCandidateGenerationCounts((current) => ({ ...current, [character.id]: 1 }))
    await run(`custom-prompt-${viewer.candidateId}`, async () => {
      await generateCharacterVisualCandidates(
        projectId,
        character.id,
        character.lockVersion,
        character.profile!.id,
        {
          count: 1,
          sourceCandidateId: viewer.candidateId,
          customPrompt: prompt,
        },
      )
      closeCandidateImageViewer()
    })
  }

  async function deleteHistoricalCandidate(
    character: CharacterVisualRecord,
    candidate: CharacterVisualRecord['candidates'][number],
  ) {
    if (!projectId || busy !== null || !candidate.deletable) return
    const selectedLocally = selected[character.id] === candidate.id
    const confirmed = window.confirm(
      selectedLocally
        ? `确认删除“${candidate.variantLabel ?? `候选 ${candidate.ordinal}`}”？该图片将被永久删除，并清除当前选择。`
        : `确认删除“${candidate.variantLabel ?? `候选 ${candidate.ordinal}`}”？该历史图片将被永久删除。`,
    )
    if (!confirmed) return
    await run(`delete-candidate-${candidate.id}`, async () => {
      await deleteCharacterVisualCandidate(
        projectId,
        character.id,
        candidate.id,
        character.lockVersion,
      )
      if (selectedLocally) {
        setSelected((current) => ({ ...current, [character.id]: '' }))
      }
    })
  }

  function jumpToCharacter(characterId: string, actionSelector?: string) {
    const characterCard = document.getElementById(`character-${characterId}`)
    if (!characterCard) return
    const target = actionSelector
      ? characterCard.querySelector<HTMLElement>(actionSelector) ?? characterCard
      : characterCard
    setActiveCharacterId(characterId)
    characterCard.focus({ preventScroll: true })
    const nextUrl = new URL(window.location.href)
    nextUrl.hash = `character-${characterId}`
    window.history.replaceState(window.history.state, '', nextUrl)
    window.scrollTo({
      behavior: 'smooth',
      top: Math.max(
        0,
        target.getBoundingClientRect().top + window.scrollY - characterScrollOffset(),
      ),
    })
  }

  async function submitIdentityViewAction() {
    if (!projectId || !workspace || !identityViewAction) return
    const character = workspace.characters.find(
      (item) => item.id === identityViewAction.characterId,
    )
    if (!character) return
    const note = identityViewAction.mode === 'adjust' ? identityViewNote.trim() : undefined
    await run(`identity-view-${identityViewAction.viewType}`, async () => {
      await generateCharacterIdentityView(
        projectId,
        character.id,
        character.lockVersion,
        identityViewAction.identityVersionId,
        identityViewAction.viewType,
        note,
      )
      setIdentityViewAction(null)
      setIdentityViewNote('')
    })
  }

  function edit(character: CharacterVisualRecord) {
    setEditingId(character.id)
    setDraft(profileDraft(character))
  }

  async function saveProfile(character: CharacterVisualRecord) {
    if (!projectId || !draft) return
    await run(`save-${character.id}`, async () => {
      await updateCharacterVisualProfile(projectId, character.id, character.lockVersion, {
        identity_fields: {
          entity_kind: draft.entityKind,
          embodiment: draft.embodiment,
          age: draft.age,
          gender_expression: draft.genderExpression,
          region: draft.region,
          era: draft.era,
          occupation: draft.occupation,
          social_class: draft.socialClass,
          story_identity: draft.storyIdentity,
        },
        appearance_fields: {
          height: draft.height,
          face_shape: draft.faceShape,
          facial_features: draft.facialFeatures,
          brow_eye_shape: draft.browEyeShape,
          nose_shape: draft.noseShape,
          mouth_corner: draft.mouthCorner,
          skin_tone: draft.skinTone,
          hairstyle: draft.hairstyle,
          hair_texture: draft.hairTexture,
          body_type: draft.bodyType,
          identifying_features: draft.identifyingFeatures,
          life_marks: draft.lifeMarks,
        },
        personality_visualization: {
          expression: draft.expression,
          gaze: draft.gaze,
          posture: draft.posture,
          movement: draft.movement,
        },
        styling_fields: {
          wardrobe: draft.wardrobe,
          materials: draft.materials,
          colors: draft.colors,
          shoes_bags: draft.shoesBags,
          accessories: draft.accessories,
          forbidden_elements: splitItems(draft.forbiddenElements),
        },
        negative_constraints: splitItems(draft.negativeConstraints),
      })
      setEditingId(null)
      setDraft(null)
    })
  }

  async function confirmIdentityBaseline(
    character: CharacterVisualRecord,
    identityId: string,
  ) {
    if (!projectId) return
    await run(`lock-${character.id}`, async () => {
      const result = await lockCharacterVisualIdentity(
        projectId,
        character.id,
        character.lockVersion,
        identityId,
      )
      if (result.script_job) {
        navigate(`/tasks?project=${projectId}&jobType=GENERATE_SCRIPT_PACKAGE`)
      }
    })
  }

  const allLocked = Boolean(
    workspace?.characters.length
    && workspace.characters.every((character) => character.status === 'LOCKED'),
  )

  if (!loading && (!projectId || !workspace)) {
    return <ServiceRequiredState feature="角色形象生成与锁定" projectId={projectId} />
  }

  if (loading || !projectId || !workspace) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在准备角色结构化视觉档案…</strong></div>
  }

  const activeCharacterIndex = Math.max(
    0,
    workspace.characters.findIndex((character) => character.id === activeCharacterId),
  )

  return <div className="page page--characters page--character-visuals">
    <PageHeader
      eyebrow="角色身份 · 统一基准"
      title="确定每个角色的拍摄基准"
      description="从设定到候选、基准检查再到人工锁定。只有完成锁定的角色形象，才会进入后续分镜与画面生成。"
      actions={<><Link className="button button--secondary button--md" to={`/projects/${projectId}/story`}><ArrowLeft size={16} />返回角色文字设定</Link><Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button></>}
    />

    {error ? <div className="brief-save-message brief-save-message--error" role="alert"><AlertTriangle size={16} />{error}</div> : null}

    <div className="character-page-body">
      <aside aria-label="快速定位角色" className="character-locator">
        <header><strong>角色定位</strong><small>{activeCharacterIndex + 1}/{workspace.characters.length}</small></header>
        <ol>{workspace.characters.map((character) => {
          const active = character.id === activeCharacterId
          const referenceImage = characterReferenceImage(character)
          return <li key={character.id}><button aria-current={active ? 'location' : undefined} aria-label={`定位到${character.name}，当前状态：${statusLabel(character.status)}`} className={active ? 'is-active' : ''} onClick={() => jumpToCharacter(character.id)} type="button"><span className="character-locator__marker">{referenceImage ? <img alt="" src={referenceImage} /> : <span aria-hidden="true">{character.name.slice(0, 1)}</span>}<i className={`is-${character.status.toLowerCase()}`} /></span><span className="character-locator__copy"><strong>{character.name}</strong><small>{statusLabel(character.status)}</small></span></button></li>
        })}</ol>
      </aside>

      <div className="character-visual-list">{workspace.characters.map((character) => {
      const profile = character.profile
      const familyConstraint = character.familyResemblanceConstraint
      const familySimilarity = familyConstraint
        ? FAMILY_SIMILARITY_META[familyConstraint.similarityLevel]
          ?? { label: familyConstraint.similarityLevel, strength: 1 }
        : null
      const familySourceReferences = familyConstraint
        ? [...new Map(
          familyConstraint.inheritedFeatures.map((feature) => {
            const sourceCharacter = workspace.characters.find(
              (item) => item.id === feature.sourceCharacterId,
            )
            return [
              feature.sourceCharacterId,
              `${familyRelationshipLabel(sourceCharacter)}${feature.sourceCharacterName}`,
            ]
          }),
        ).values()]
        : []
      const familyFeatureLabels = familyConstraint
        ? [...new Set(familyConstraint.inheritedFeatures.map((feature) => feature.label))]
        : []
      const relativeToLock = familyConstraint?.status === 'WAITING_FOR_LOCKED_RELATIVE'
        ? familyConstraint.relationshipEvidence
          .map((evidence) => workspace.characters.find(
            (item) => item.characterKey === evidence.relativeCharacterKey,
          ))
          .find((item) => item && item.status !== 'LOCKED')
        : undefined
      const selectedCandidateId = selected[character.id]
      const selectedCandidate = character.candidates.find((item) => item.id === selectedCandidateId)
      const latestPendingIdentity = [...character.identities].reverse().find(
        (identity) => ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status),
      )
      const selectedReviewIdentity = character.identities.find(
        (identity) => (
          identity.id === identityReviewSelection[character.id]
          && ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status)
        ),
      )
      const pendingIdentity = selectedReviewIdentity ?? latestPendingIdentity
      const viewingReplacedIdentity = Boolean(
        pendingIdentity && latestPendingIdentity && pendingIdentity.id !== latestPendingIdentity.id,
      )
      const pendingIdentityAssets = new Map(
        pendingIdentity?.assets.map((asset) => [asset.viewType, asset]) ?? [],
      )
      const pendingIdentityJobs = new Map(
        pendingIdentity?.viewJobs.map((job) => [job.viewType, job]) ?? [],
      )
      const latestIdentityJobs = [...pendingIdentityJobs.values()]
      const activeIdentityJobs = latestIdentityJobs.filter((job) => (
        ACTIVE_DOSSIER_JOB_STATUSES.has(job.status)
      ))
      const failedIdentityJobs = latestIdentityJobs.filter((job) => (
        FAILED_DOSSIER_JOB_STATUSES.has(job.status)
      ))
      const generatingDossier = activeIdentityJobs.length > 0
        || (pendingIdentity?.status === 'GENERATING_DOSSIER' && !pendingIdentity?.viewJobs.length)
      const selectedHasIdentity = character.identities.some(
        (identity) => identity.sourceCandidateId === selectedCandidateId,
      )
      const isEditing = editingId === character.id && draft
      const blockers = profile?.conflictReport.filter((item) => item.severity === 'BLOCKER') ?? []
      const candidateBatchVersions = new Map(character.batches.map((batch) => [batch.id, batch.version]))
      const newestCandidateBatch = [...character.batches]
        .sort((left, right) => left.version - right.version)
        .at(-1)
      const generatingCandidates = character.status === 'GENERATING'
        || newestCandidateBatch?.status === 'GENERATING'
        || busy === `generate-${character.id}`
      const generating = generatingCandidates
        || generatingDossier
      const refinementNote = refinements[character.id] ?? ''
      const refinementControl = selectedCandidate && !generating && profile
        ? <div className="character-candidate-refinement">
          <textarea aria-label={`${character.name}候选微调说明`} onChange={(event) => setRefinements((current) => ({ ...current, [character.id]: event.target.value }))} placeholder="例如：保留五官，只让眼神更警觉，发型更利落" rows={2} value={refinementNote} />
          <Button disabled={!refinementNote.trim() || busy !== null} onClick={() => void run(`refine-${character.id}`, () => generateCharacterVisualCandidates(projectId, character.id, character.lockVersion, profile.id, { sourceCandidateId: selectedCandidate.id, note: refinementNote.trim() }))} variant="secondary">{busy === `refine-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <SlidersHorizontal size={15} />}生成微调版本</Button>
        </div>
        : null
      const candidatesByRecency = [...character.candidates].sort((left, right) => {
        const batchDifference = (candidateBatchVersions.get(left.batchId ?? '') ?? 0)
          - (candidateBatchVersions.get(right.batchId ?? '') ?? 0)
        return batchDifference || left.ordinal - right.ordinal
      })
      const batchIdsWithCandidates = new Set(
        character.candidates.map((candidate) => candidate.batchId).filter(Boolean),
      )
      const latestCompletedBatchId = [...character.batches]
        .filter((batch) => batchIdsWithCandidates.has(batch.id))
        .sort((left, right) => left.version - right.version)
        .at(-1)?.id
      const activeCandidateBatch = newestCandidateBatch?.status === 'GENERATING'
        ? newestCandidateBatch
        : undefined
      const displayedCandidateBatchId = generatingCandidates
        ? activeCandidateBatch?.id
        : latestCompletedBatchId
      const latestCandidateIds = new Set(
        displayedCandidateBatchId
          ? character.candidates
            .filter((candidate) => candidate.batchId === displayedCandidateBatchId)
            .map((candidate) => candidate.id)
          : generatingCandidates
            ? []
            : candidatesByRecency.slice(-3).map((candidate) => candidate.id),
      )
      const latestCandidates = character.candidates
        .filter((candidate) => latestCandidateIds.has(candidate.id))
        .sort((left, right) => left.ordinal - right.ordinal)
      const historicalCandidates = character.candidates.filter((candidate) => !latestCandidateIds.has(candidate.id))
      const requestedCandidateCount = candidateGenerationCounts[character.id] ?? 3
      const displayedCandidateCount = generatingCandidates
        ? activeCandidateBatch?.requestedCount ?? requestedCandidateCount
        : latestCandidates.length
      const candidateSlots = buildCandidateGenerationSlots(
        latestCandidates,
        displayedCandidateCount,
        generatingCandidates,
      )
      const completedCandidateCount = candidateSlots.filter(Boolean).length
      const pendingSourceLabels = character.pendingSourceChanges?.changedFields.map(
        (field) => VISUAL_SOURCE_FIELD_LABELS[field] ?? field,
      ) ?? []
      const entityKind = profile?.identityFields.entity_kind ?? 'HUMAN'
      const digitalEntity = entityKind === 'DIGITAL_ENTITY'
      const profileFacts = profile ? buildCharacterVisualFacts({
        entityKind,
        embodiment: profile.identityFields.embodiment ?? '',
        age: profile.identityFields.age ?? '',
        height: profile.appearanceFields.height ?? '',
        genderExpression: profile.identityFields.gender_expression ?? '',
        ethnicity: profile.identityFields.region ?? '',
        occupation: profile.identityFields.occupation ?? '',
        identifyingFeatures: profile.appearanceFields.identifying_features ?? '',
        gaze: profile.personalityVisualization.gaze ?? '',
        wardrobe: String(profile.stylingFields.wardrobe ?? ''),
      }) : []
      const currentWorkflowStep = blockers.length > 0
        ? 1
        : character.status === 'LOCKED'
          ? 4
          : pendingIdentity
            ? pendingIdentity.status === 'READY_FOR_REVIEW' && !viewingReplacedIdentity
              ? 4
              : 3
            : 2
      const currentStatusLabel = character.status === 'LOCKED'
        ? '已锁定'
        : currentWorkflowStep === 1
          ? '设定待处理'
          : currentWorkflowStep === 2
            ? digitalEntity ? '待选择方案' : '待选择形象'
            : currentWorkflowStep === 3
              ? '正在生成基准'
              : '待确认角色基准'
      return <article className="character-visual-card" data-character-id={character.id} id={`character-${character.id}`} key={character.id} tabIndex={-1}>
        <header className="character-visual-card__header">
          <div><p className="eyebrow">{localizeDisplayText(character.role)}</p><h2>{character.name}</h2><small>{character.status === 'LOCKED' ? '角色基准已生效，后续画面将引用当前锁定版本。' : '完成选择与基准检查后，再由你确认锁定。'}</small></div>
          <span className={`character-visual-status is-${character.status.toLowerCase()}`}>{currentStatusLabel}</span>
        </header>

        <ol aria-label={`${character.name}角色形象确认进度`} className="character-workflow-steps">
          {CHARACTER_WORKFLOW_STEPS.map((step, index) => {
            const stepLabel = digitalEntity && step === '选择形象' ? '选择方案' : step
            const stepNumber = index + 1
            const complete = character.status === 'LOCKED' || stepNumber < currentWorkflowStep
            const active = character.status !== 'LOCKED' && stepNumber === currentWorkflowStep
            return <li aria-current={active ? 'step' : undefined} className={`${complete ? 'is-complete' : ''} ${active ? 'is-active' : ''}`.trim()} key={step}>
              <span>{complete ? <Check size={15} strokeWidth={2.5} /> : stepNumber}</span>
              <strong>{stepLabel}</strong>
            </li>
          })}
        </ol>

        {profile ? <section className="character-profile-summary">
          <div className="character-profile-summary__heading"><div><strong>角色设定摘要</strong><span>第 {profile.version} 版</span></div><Button disabled={generating || busy !== null} onClick={() => edit(character)} size="sm" variant="secondary"><Pencil size={14} />调整设定</Button></div>
          <div className="character-profile-facts">
            {profileFacts.map((fact) => <span className={fact.needsAttention ? 'needs-attention' : ''} key={fact.label} title={`${fact.label}：${fact.value}`}><small>{fact.label}</small><strong>{fact.value}</strong>{fact.needsAttention ? <em>待补充</em> : null}</span>)}
          </div>
          <details className="character-profile-details" open={blockers.length > 0}>
            <summary>查看完整设定与一致性检查</summary>
            {digitalEntity
              ? <dl><div><dt>系统身份</dt><dd>{profile.identityFields.occupation} · {profile.identityFields.story_identity}</dd></div><div><dt>呈现载体</dt><dd>{profile.identityFields.embodiment || '待补充'}</dd></div><div><dt>视觉特征</dt><dd>{profile.appearanceFields.identifying_features}</dd></div><div><dt>性格关键词</dt><dd>{profile.personalityVisualization.traits || '待补充'}</dd></div><div><dt>状态表现</dt><dd>{profile.personalityVisualization.expression}；{profile.personalityVisualization.movement}</dd></div><div><dt>视觉语言</dt><dd>{String(profile.stylingFields.materials)}；{String(profile.stylingFields.colors)}</dd></div></dl>
              : <dl><div><dt>身份</dt><dd>{profile.identityFields.region} · {profile.identityFields.era} · {profile.identityFields.social_class}</dd></div><div><dt>外貌</dt><dd>{profile.appearanceFields.face_shape}；{profile.appearanceFields.identifying_features}</dd></div><div><dt>性格关键词</dt><dd>{profile.personalityVisualization.traits || '待补充'}</dd></div><div><dt>表演约束</dt><dd>{profile.personalityVisualization.expression}；{profile.personalityVisualization.gaze}</dd></div><div><dt>造型</dt><dd>{String(profile.stylingFields.wardrobe)}；{String(profile.stylingFields.colors)}</dd></div></dl>}
            <div className="character-profile-audit"><strong><ShieldCheck size={15} />一致性检查</strong><ul>{profile.conflictReport.map((issue) => <li data-severity={issue.severity.toLowerCase()} key={issue.code}><span>{issue.message}</span><small>{issue.suggestion}</small></li>)}</ul></div>
          </details>
        </section> : null}

        {character.sourceStale ? <div className="character-source-stale" role="alert">
          <AlertTriangle size={17} />
          <div>
            <strong>角色文字设定已有更新，当前视觉档案已过期</strong>
            <p>待同步字段：{pendingSourceLabels.join('、') || '角色设定'}。请先返回“故事与剧本”确认最新关系网；确认后系统会准备新的角色设定摘要。</p>
          </div>
          <Link className="button button--secondary button--sm" to={`/projects/${projectId}/story`}>返回确认</Link>
        </div> : null}

        {familyConstraint && familySimilarity ? <section className="character-family-constraint" data-status={familyConstraint.status.toLowerCase()}>
          <header>
            <div className="character-family-constraint__title">
              <span><Dna size={18} /></span>
              <strong>家族相似性</strong>
            </div>
            {familyConstraint.status === 'WAITING_FOR_LOCKED_RELATIVE' ? <em><i />等待亲属基准</em> : null}
          </header>
          {familyConstraint.status === 'ACTIVE' ? <div className="character-family-active">
            <div className="character-family-active__copy">
              <p className="character-family-conclusion">
                已参考{familySourceReferences.join('、') || '已锁定亲属'}的{familyFeatureLabels.join('、')}，保持{familySimilarity.label}。
              </p>
              <small>只继承家族特征，不复制五官，也不会覆盖已锁定身份。</small>
            </div>
            <div className="character-family-actions">
              <Link className="button button--secondary button--sm" to={`/projects/${projectId}/story#relationship-review`}>
                <SlidersHorizontal size={14} />调整相似度
              </Link>
              <details className="character-family-rules">
                <summary className="button button--ghost button--sm"><ShieldCheck size={14} />查看规则</summary>
                <div>
                  <p><strong>系统详情</strong><span>约束版本 V{familyConstraint.version} · {familySimilarity.label}</span></p>
                  <ul className="character-family-rule-traits">
                    {familyConstraint.inheritedFeatures.map((feature) => <li key={`${feature.field}-${feature.sourceIdentityVersionId}`}><strong>{feature.label}</strong><span>{feature.value}</span><small>来源：{feature.sourceCharacterName}</small></li>)}
                  </ul>
                  <strong>生成边界</strong>
                  <ul>{familyConstraint.independenceConstraints.map((item) => <li key={item}>{item}</li>)}</ul>
                  {character.status === 'LOCKED' ? <small>当前角色已锁定，调整只影响未来生成。</small> : null}
                </div>
              </details>
            </div>
          </div> : <div className="character-family-waiting">
            <LockKeyhole size={18} />
            <div className="character-family-waiting__copy">
              <strong>先锁定一位亲属，系统才会提取稳定家族特征，不会自动生图或覆盖已有内容。</strong>
            </div>
            {relativeToLock ? <Button
              aria-label={`去完成${relativeToLock.name}的角色基准锁定`}
              className="character-family-waiting__action"
              onClick={() => jumpToCharacter(
                relativeToLock.id,
                '[data-character-lock-action="true"], .character-identity-dossier, .character-candidate-section',
              )}
              size="sm"
              variant="secondary"
            >
              <UserRoundCheck size={14} />去锁定{relativeToLock.name}
            </Button> : null}
          </div>}
        </section> : null}

        {isEditing ? <section className="character-visual-editor">
          <header><div><span>生成前可调整</span><h3>外貌、造型与表演表现</h3></div><small>保存后会创建新版本并重新运行一致性审核</small></header>
          <div className="character-visual-editor__grid" data-entity-kind={draft.entityKind}>
            <label>角色形态<select onChange={(event) => setDraft({ ...draft, entityKind: event.target.value })} value={draft.entityKind}><option value="HUMAN">人类</option><option value="DIGITAL_ENTITY">数字实体</option><option value="ROBOT">机器人</option><option value="CREATURE">非人型生物</option><option value="OBJECT">拟人化物体</option></select></label>
            <label>呈现载体<input onChange={(event) => setDraft({ ...draft, embodiment: event.target.value })} placeholder="例如：屏幕界面、全息投影或实体终端" value={draft.embodiment} /></label>
            <label>{draft.entityKind === 'DIGITAL_ENTITY' ? '运行时长' : '年龄'}<input onChange={(event) => setDraft({ ...draft, age: event.target.value })} value={draft.age} /></label>
            <label className="is-human-only">身高<input onChange={(event) => setDraft({ ...draft, height: event.target.value })} placeholder="例如：178 cm" value={draft.height} /></label>
            <label className="is-human-only">性别表达<input onChange={(event) => setDraft({ ...draft, genderExpression: event.target.value })} value={draft.genderExpression} /></label>
            <label className="is-human-only">地域<input onChange={(event) => setDraft({ ...draft, region: event.target.value })} value={draft.region} /></label>
            <label>时代<input onChange={(event) => setDraft({ ...draft, era: event.target.value })} value={draft.era} /></label>
            <label>{draft.entityKind === 'DIGITAL_ENTITY' ? '系统定位' : '职业'}<input onChange={(event) => setDraft({ ...draft, occupation: event.target.value })} value={draft.occupation} /></label>
            <label className="is-human-only">阶层<input onChange={(event) => setDraft({ ...draft, socialClass: event.target.value })} value={draft.socialClass} /></label>
            <label className="is-wide">剧中身份<textarea onChange={(event) => setDraft({ ...draft, storyIdentity: event.target.value })} rows={2} value={draft.storyIdentity} /></label>
            <label className="is-human-only">脸型<input onChange={(event) => setDraft({ ...draft, faceShape: event.target.value })} value={draft.faceShape} /></label>
            <label className="is-human-only">五官<input onChange={(event) => setDraft({ ...draft, facialFeatures: event.target.value })} value={draft.facialFeatures} /></label>
            <label className="is-human-only">眉眼<input onChange={(event) => setDraft({ ...draft, browEyeShape: event.target.value })} value={draft.browEyeShape} /></label>
            <label className="is-human-only">鼻型<input onChange={(event) => setDraft({ ...draft, noseShape: event.target.value })} value={draft.noseShape} /></label>
            <label className="is-human-only">嘴角<input onChange={(event) => setDraft({ ...draft, mouthCorner: event.target.value })} value={draft.mouthCorner} /></label>
            <label className="is-human-only">肤色<input onChange={(event) => setDraft({ ...draft, skinTone: event.target.value })} value={draft.skinTone} /></label>
            <label className="is-human-only">发型<input onChange={(event) => setDraft({ ...draft, hairstyle: event.target.value })} value={draft.hairstyle} /></label>
            <label className="is-human-only">发质<input onChange={(event) => setDraft({ ...draft, hairTexture: event.target.value })} value={draft.hairTexture} /></label>
            <label>{draft.entityKind === 'DIGITAL_ENTITY' ? '形态结构' : '体型'}<input onChange={(event) => setDraft({ ...draft, bodyType: event.target.value })} value={draft.bodyType} /></label>
            <label>{draft.entityKind === 'DIGITAL_ENTITY' ? '视觉特征' : '识别特征'}<input onChange={(event) => setDraft({ ...draft, identifyingFeatures: event.target.value })} value={draft.identifyingFeatures} /></label>
            <label className="is-wide">生活痕迹<input onChange={(event) => setDraft({ ...draft, lifeMarks: event.target.value })} value={draft.lifeMarks} /></label>
            <label>表情<input onChange={(event) => setDraft({ ...draft, expression: event.target.value })} value={draft.expression} /></label>
            <label>眼神<input onChange={(event) => setDraft({ ...draft, gaze: event.target.value })} value={draft.gaze} /></label>
            <label>站姿<input onChange={(event) => setDraft({ ...draft, posture: event.target.value })} value={draft.posture} /></label>
            <label>动作<input onChange={(event) => setDraft({ ...draft, movement: event.target.value })} value={draft.movement} /></label>
            <label className="is-wide">服装<input onChange={(event) => setDraft({ ...draft, wardrobe: event.target.value })} value={draft.wardrobe} /></label>
            <label>材质<input onChange={(event) => setDraft({ ...draft, materials: event.target.value })} value={draft.materials} /></label>
            <label>色彩<input onChange={(event) => setDraft({ ...draft, colors: event.target.value })} value={draft.colors} /></label>
            <label>鞋包<input onChange={(event) => setDraft({ ...draft, shoesBags: event.target.value })} value={draft.shoesBags} /></label>
            <label>配饰<input onChange={(event) => setDraft({ ...draft, accessories: event.target.value })} value={draft.accessories} /></label>
            <label className="is-wide">禁止元素<input onChange={(event) => setDraft({ ...draft, forbiddenElements: event.target.value })} value={draft.forbiddenElements} /></label>
            <label className="is-wide">负面约束<textarea onChange={(event) => setDraft({ ...draft, negativeConstraints: event.target.value })} rows={2} value={draft.negativeConstraints} /></label>
          </div>
          <footer><Button onClick={() => { setEditingId(null); setDraft(null) }} variant="secondary">取消</Button><Button disabled={busy !== null} onClick={() => void saveProfile(character)}>{busy === `save-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}保存并重新审核</Button></footer>
        </section> : null}

        {profile && character.status !== 'LOCKED' ? <section className="character-candidate-section">
          <header><div><span>步骤 2 · {digitalEntity ? '选择视觉方案' : '选择形象'}</span><h3>{character.candidates.length ? latestCandidates.length === 1 ? `查看最新生成的${digitalEntity ? '视觉方案' : '形象方向'}` : digitalEntity ? '比较三个方案，确定数字实体的视觉语言' : '比较三个方向，确定角色第一印象' : digitalEntity ? '生成有差异的数字实体视觉方案' : '生成有差异的形象方向'}</h3><p>{character.candidates.length ? digitalEntity ? '选择不会立即锁定，下一步还会生成运行状态与场景载体基准图。' : '选择不会立即锁定，下一步还会生成多角度基准图供你检查。' : digitalEntity ? '系统会遵循角色功能与呈现载体；默认生成三个方案，也可以只生成一个。' : '系统会遵循角色设定与家族约束；默认生成三个方向，也可以只生成一张。'}</p></div><div>{character.candidates.length ? <Button onClick={() => setCompare((current) => ({ ...current, [character.id]: !current[character.id] }))} size="sm" variant="secondary"><GitCompare size={14} />{compare[character.id] ? '退出比较' : '专注比较'}</Button> : null}<CandidateGenerationControl disabled={character.sourceStale || blockers.length > 0 || generating || busy !== null} generating={busy === `generate-${character.id}` || generating} hasCandidates={character.candidates.length > 0} menuId={`candidate-generation-menu-${character.id}`} onGenerate={(count) => { setCandidateGenerationCounts((current) => ({ ...current, [character.id]: count })); void run(`generate-${character.id}`, () => generateCharacterVisualCandidates(projectId, character.id, character.lockVersion, profile.id, { count })) }} visualScheme={digitalEntity} /></div></header>
          {blockers.length ? <div className="character-candidate-blocked"><AlertTriangle size={15} />请先调整 {blockers.length} 个阻断问题，再生成形象。</div> : null}
          {character.candidates.length && !generating ? <div aria-live="polite" className={`character-candidate-decision ${selectedCandidate ? 'has-selection' : ''}`}>
            <div className="character-candidate-decision__summary">
              <span>{selectedCandidate ? <Check size={17} /> : <UserRound size={17} />}</span>
              <div>
                <strong>{selectedCandidate ? `已选择：${selectedCandidate.variantLabel ?? `候选 ${selectedCandidate.ordinal}`}` : '请选择一个形象方向'}</strong>
                <small>{selectedCandidate ? selectedHasIdentity ? '该形象已经进入基准检查，可继续查看多角度一致性。' : '确认后会生成正面、侧面、全身与表情等基准检查图。' : '先比较脸型、年龄感、气质和角色辨识度，再进入基准检查。'}</small>
              </div>
            </div>
            {selectedCandidate ? selectedHasIdentity
              ? <Button onClick={() => jumpToCharacter(character.id, '.character-identity-dossier')} variant="secondary"><Eye size={15} />查看基准检查</Button>
              : <Button disabled={busy !== null} onClick={() => void run(`select-${character.id}`, () => selectCharacterVisualCandidate(projectId, character.id, character.lockVersion, selectedCandidate.id))}>{busy === `select-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <UserRoundCheck size={15} />}确认此形象并生成基准图</Button>
              : null}
          </div> : null}
          {character.candidates.length || generatingCandidates ? <div aria-busy={generatingCandidates} aria-live="polite" className={`character-candidate-collection ${generatingCandidates ? 'is-generating' : ''}`}>
            <div aria-label={generatingCandidates ? `形象方向生成进度，已完成 ${completedCandidateCount} 个，共 ${displayedCandidateCount} 个` : undefined} className={`character-candidate-grid ${compare[character.id] ? 'is-comparing' : ''}`}>{candidateSlots.map((candidate, candidateIndex) => {
              const directionNumber = candidateIndex + 1
              if (!candidate) {
                const direction = CANDIDATE_PLACEHOLDERS[candidateIndex] ?? {
                  label: `候选方向 ${directionNumber}`,
                  description: '生成时动态抽取',
                }
                return <article aria-label={`候选方向 ${directionNumber} 正在生成`} className="character-candidate-placeholder" key={`placeholder-${displayedCandidateBatchId ?? 'pending'}-${directionNumber}`}>
                  <div aria-hidden="true" className="character-candidate-placeholder__portrait">
                    <div className="character-candidate-placeholder__figure">{digitalEntity ? <Monitor size={48} strokeWidth={1.25} /> : <UserRound size={48} strokeWidth={1.25} />}<Sparkles size={18} /></div>
                    <span>正在生成</span>
                  </div>
                  <div><strong>{direction.label}</strong><small>{direction.description}</small></div>
                </article>
              }
              const batchVersion = candidateBatchVersions.get(candidate.batchId ?? '')
              const selectable = !generating && candidate.profileVersionId === profile.id
              const variantLabel = candidate.variantLabel ?? `候选 ${directionNumber}`
              return <div className={`character-candidate ${selectedCandidateId === candidate.id ? 'character-candidate--selected' : ''}`} data-disabled={!selectable || undefined} key={candidate.id}>
                <button aria-pressed={selectedCandidateId === candidate.id} className="character-candidate__select" disabled={!selectable} onClick={() => setSelected((current) => ({ ...current, [character.id]: candidate.id }))} type="button">
                  <img alt={`${character.name} 形象候选 ${directionNumber}`} src={candidate.assetUrl} />
                  <span><strong>{variantLabel}</strong>{candidate.variantDescription ? <small className="character-candidate__description">{candidate.variantDescription}</small> : null}<small>第 {batchVersion ?? 1} 批生成</small></span>
                </button>
                <button aria-label={`查看${character.name}${variantLabel}完整图片`} className="character-candidate__open" onClick={() => openCandidateImageViewer({ characterId: character.id, candidateId: candidate.id, characterName: character.name, variantLabel, assetUrl: candidate.assetUrl, selectable, generationPrompt: candidate.generationPrompt })} type="button"><Maximize2 size={15} /><span>查看大图</span></button>
                {selectedCandidateId === candidate.id ? refinementControl : null}
              </div>
            })}</div>
            {generatingCandidates ? <p className="character-candidate-progress"><span aria-hidden="true" className="character-generation-dots"><i /><i /><i /></span><span>已完成 {completedCandidateCount}/{displayedCandidateCount}，其余形象方向仍在生成。</span></p> : null}
            {historicalCandidates.length ? <section className="character-candidate-history"><header><strong>历史候选</strong><small>{historicalCandidates.length} 张</small></header><div className="character-candidate-history__grid">{historicalCandidates.map((candidate) => {
              const batchVersion = candidateBatchVersions.get(candidate.batchId ?? '')
              const selectable = !generating && candidate.profileVersionId === profile.id
              const variantLabel = candidate.variantLabel ?? `候选 ${candidate.ordinal}`
              return <div className={`character-candidate character-candidate--compact ${selectedCandidateId === candidate.id ? 'character-candidate--selected' : ''}`} data-disabled={!selectable || undefined} key={candidate.id}>
                <button aria-pressed={selectedCandidateId === candidate.id} className="character-candidate__select" disabled={!selectable} onClick={() => setSelected((current) => ({ ...current, [character.id]: candidate.id }))} type="button"><img alt={`${character.name} 历史形象候选 ${candidate.ordinal}`} src={candidate.assetUrl} /><span><strong>{variantLabel}</strong><small>第 {batchVersion ?? 1} 批 · 候选 {candidate.ordinal}</small></span></button>
                <button aria-label={`查看${character.name}${variantLabel}完整图片`} className="character-candidate__open" onClick={() => openCandidateImageViewer({ characterId: character.id, candidateId: candidate.id, characterName: character.name, variantLabel, assetUrl: candidate.assetUrl, selectable, generationPrompt: candidate.generationPrompt })} type="button"><Maximize2 size={14} /><span>查看大图</span></button>
                <button aria-label={candidate.deletable ? `删除${character.name}${variantLabel}` : candidate.deleteBlockReason ?? '该历史候选不能删除'} className="character-candidate__delete" disabled={!candidate.deletable || busy !== null} onClick={() => void deleteHistoricalCandidate(character, candidate)} title={candidate.deletable ? '删除历史候选' : candidate.deleteBlockReason} type="button">{busy === `delete-candidate-${candidate.id}` ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}</button>
                {selectedCandidateId === candidate.id ? refinementControl : null}
              </div>
            })}</div></section> : null}
          </div> : <div aria-busy={generatingCandidates} aria-live="polite" className={`character-candidate-empty ${generatingCandidates ? 'is-generating' : ''}`}>
            <div aria-label={`待生成的${requestedCandidateCount === 1 ? '一个' : '三个'}${digitalEntity ? '视觉方案' : '形象方向'}`} className={`character-candidate-placeholder-grid ${requestedCandidateCount === 1 ? 'is-single' : ''}`}>
              {CANDIDATE_PLACEHOLDERS.slice(0, requestedCandidateCount).map((direction) => <article className="character-candidate-placeholder" key={direction.label}>
                <div aria-hidden="true" className="character-candidate-placeholder__portrait">
                  <div className="character-candidate-placeholder__figure">{digitalEntity ? <Monitor size={48} strokeWidth={1.25} /> : <UserRound size={48} strokeWidth={1.25} />}{generatingCandidates ? <Sparkles size={18} /> : null}</div>
                  <span>{generatingCandidates ? '正在生成' : '待生成'}</span>
                </div>
                <div><strong>{direction.label}</strong><small>{direction.description}</small></div>
              </article>)}
            </div>
            {generatingCandidates ? <p><span aria-hidden="true" className="character-generation-dots"><i /><i /><i /></span><span>正在生成{requestedCandidateCount === 1 ? '一个' : '三个'}{digitalEntity ? '视觉方案' : '形象方向'}，完成后会自动显示。</span></p> : null}
          </div>}
        </section> : null}

        {pendingIdentity ? <section className="character-identity-dossier" data-identity-version-id={pendingIdentity.id}>
          <header><div><span>{viewingReplacedIdentity ? '历史版本 · 仅供查看' : '第二步 · 基准检查'}</span><h3>角色身份 · 第 {pendingIdentity.version} 版</h3></div><StatusBadge description={viewingReplacedIdentity ? `当前以第 ${latestPendingIdentity?.version ?? pendingIdentity.version} 版为审核目标` : failedIdentityJobs.length ? `${failedIdentityJobs.length} 个角色身份视角生成失败，可以单独重新生成` : generatingDossier ? '正在生成多角度角色身份基准检查图' : '角色身份基准检查图已生成，等待确认'} label={viewingReplacedIdentity ? '已被新版本替代' : failedIdentityJobs.length ? `${failedIdentityJobs.length} 个视角生成失败` : undefined} status={viewingReplacedIdentity ? 'SUPERSEDED' : failedIdentityJobs.length ? 'GENERATION_FAILED' : pendingIdentity.status} /></header>
          <div aria-busy={generatingDossier} aria-live="polite">
            {DOSSIER_VIEW_TYPES.map((viewType) => {
              const asset = pendingIdentityAssets.get(viewType)
              const viewJob = pendingIdentityJobs.get(viewType)
              const label = dossierViewLabel(viewType, entityKind)
              const failed = Boolean(viewJob && FAILED_DOSSIER_JOB_STATUSES.has(viewJob.status))
              const active = Boolean(viewJob && ACTIVE_DOSSIER_JOB_STATUSES.has(viewJob.status))
              const retrying = viewJob ? busy === `retry-dossier-${viewJob.id}` : false
              return asset
                ? <figure aria-busy={active} className={`character-dossier-asset ${active ? 'is-updating' : ''} ${failed ? 'has-error' : ''}`} key={viewType}>
                    <div className="character-dossier-asset__media">
                      <button aria-label={`查看${character.name}${label}的完整原图`} className="character-dossier-asset__open" onClick={() => openIdentityImageViewer({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl })} type="button">
                        <img alt={`${character.name} ${label}`} src={asset.assetUrl} />
                        <span aria-hidden="true"><Maximize2 size={14} /></span>
                      </button>
                      {active ? <div className="character-dossier-regeneration" role="status">
                        <DossierGenerationMark />
                        <strong>正在生成新版本</strong>
                        <small>旧图会保留到新版本完成</small>
                      </div> : failed ? <span className="character-dossier-asset__status is-error"><AlertTriangle size={14} />上次生成失败</span> : null}
                      {!viewingReplacedIdentity ? <>
                        <Button aria-label={`调整${character.name}${label}的细节`} className="character-dossier-asset__action character-dossier-asset__action--adjust" disabled={busy !== null || active} onClick={() => openIdentityViewAction({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl, mode: 'adjust' })} size="sm" variant="secondary">调整细节</Button>
                        <Button aria-label={`重新生成${character.name}${label}`} className="character-dossier-asset__action character-dossier-asset__action--regenerate" disabled={busy !== null || active} onClick={() => openIdentityViewAction({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl, mode: 'regenerate' })} size="sm" variant="secondary">重新生成</Button>
                      </> : null}
                    </div>
                    <figcaption>{label}</figcaption>
                  </figure>
                : <figure aria-busy={active} aria-label={`${label}图片${failed ? '生成失败' : active ? '正在生成' : '等待生成'}`} className={`character-dossier-placeholder ${active ? 'is-generating' : ''} ${failed ? 'is-failed' : ''}`} key={viewType}>
                    <div className={`character-dossier-placeholder__visual ${active ? 'is-generating' : ''}`}>
                      {failed ? <AlertTriangle aria-hidden="true" size={32} strokeWidth={1.4} /> : active ? <DossierGenerationMark /> : digitalEntity ? <Monitor aria-hidden="true" size={36} strokeWidth={1.25} /> : <UserRound aria-hidden="true" size={36} strokeWidth={1.25} />}
                      <strong>{failed ? '生成失败' : viewJob?.status === 'RETRY_WAIT' ? '等待重试' : active ? '正在生成' : '等待生成'}</strong>
                      {active && viewJob ? <small>单次最长等待 {compactDuration(viewJob.maxWaitSeconds)}</small> : null}
                      {failed && viewJob?.retryable ? <Button disabled={busy !== null} onClick={() => void run(`retry-dossier-${viewJob.id}`, () => retryPersistedJob(viewJob.id))} size="sm" variant="secondary">{retrying ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}重新生成</Button> : null}
                    </div>
                    <figcaption>{label}</figcaption>
                  </figure>
            })}
            {failedIdentityJobs.length ? <div className="character-dossier-error-summary"><AlertTriangle size={18} />{failedIdentityJobs.length} 个视角未完成，可在对应卡片重新生成。</div> : null}
          </div>
          {!viewingReplacedIdentity ? <footer className={`character-identity-decision ${pendingIdentity.status === 'READY_FOR_REVIEW' ? 'is-ready' : ''}`}>
            <div><span>{pendingIdentity.status === 'READY_FOR_REVIEW' ? <ShieldCheck size={18} /> : <LoaderCircle className="spin" size={18} />}</span><div><strong>{pendingIdentity.status === 'READY_FOR_REVIEW' ? digitalEntity ? '数字实体状态基准已就绪' : '多角度基准图已就绪' : digitalEntity ? '正在完成数字实体状态基准' : '正在完成多角度基准检查'}</strong><small>{pendingIdentity.status === 'READY_FOR_REVIEW' ? digitalEntity ? '确认界面结构、配色、识别符号与不同运行状态一致后，再锁定为后续生成基准。' : '确认五官、年龄感、发型与体型一致后，再锁定为后续生成基准。' : '全部视角生成完成后，才能人工确认并锁定。'}</small></div></div>
            {pendingIdentity.status === 'READY_FOR_REVIEW' ? <Button data-character-lock-action="true" disabled={busy !== null} onClick={() => void confirmIdentityBaseline(character, pendingIdentity.id)}>{busy === `lock-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <LockKeyhole size={15} />}确认并锁定为角色基准</Button> : null}
          </footer> : null}
        </section> : null}

        {character.identities.length ? <section className="character-version-history">
          <header><div><History size={17} /><span><strong>角色基准版本</strong><small>每次设为基准都会保留完整版本，可恢复但不会覆盖既有镜头。</small></span></div><em>{character.identities.length} 个版本</em></header>
          <div>{[...character.identities].reverse().map((identity) => {
            const isActive = identity.id === character.lockedIdentityVersionId
            const reviewable = identity.id === latestPendingIdentity?.id
            const replacedPending = !isActive
              && !reviewable
              && !identity.lockedAt
              && ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status)
            const viewingThisVersion = identity.id === pendingIdentity?.id
            const readyToLock = reviewable
              && viewingThisVersion
              && identity.status === 'READY_FOR_REVIEW'
            const restorable = !isActive && Boolean(identity.lockedAt)
              && ['LOCKED', 'SUPERSEDED'].includes(identity.status)
            return <article className={`${isActive ? 'is-active' : ''} ${viewingThisVersion ? 'is-viewing' : ''}`.trim()} key={identity.id}>
              {identity.sourceCandidateAssetUrl ? <img alt={`${character.name}角色基准第${identity.version}版`} src={identity.sourceCandidateAssetUrl} /> : <div className="character-version-history__placeholder"><UserRoundCheck size={18} /></div>}
              <div>
                <span>第 {identity.version} 版</span>
                <strong>{isActive ? '当前角色基准' : replacedPending ? '已被新版本替代' : getStatusLabel(identity.status)}</strong>
                <small>{identity.lockedAt ? `曾由 ${identity.lockedBy ?? '创作者'} 设为基准` : replacedPending ? `当前以第 ${latestPendingIdentity?.version ?? identity.version} 版为审核目标` : '尚未完成基准确认'}</small>
              </div>
              <div className="character-version-history__actions">
                {identity.assets.length ? <Button onClick={() => setIdentityVersionGallery({
                  characterId: character.id,
                  characterName: character.name,
                  identityVersionId: identity.id,
                  identityVersion: identity.version,
                  entityKind,
                  assets: identity.assets,
                })} size="sm" variant="secondary"><Images size={14} />查看多角度图</Button> : null}
                {reviewable ? readyToLock ? <span className="character-version-history__reviewing"><Check size={14} />当前审核版本</span> : <Button disabled={busy !== null} onClick={() => {
                  if (!viewingThisVersion) {
                    setIdentityReviewSelection((current) => ({ ...current, [character.id]: identity.id }))
                    window.setTimeout(() => jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`), 0)
                  } else {
                    jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`)
                  }
                }} size="md" variant="secondary"><UserRoundCheck size={15} />{viewingThisVersion ? '查看生成进度' : '返回审核'}</Button> : replacedPending ? <Button onClick={() => {
                  setIdentityReviewSelection((current) => ({ ...current, [character.id]: identity.id }))
                  window.setTimeout(() => jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`), 0)
                }} size="sm" variant="secondary"><Eye size={14} />{viewingThisVersion ? '正在查看' : '查看此版本'}</Button> : restorable ? <Button disabled={busy !== null} onClick={() => void run(`restore-${identity.id}`, () => restoreCharacterVisualIdentity(projectId, character.id, character.lockVersion, identity.id))} size="sm" variant="secondary">{busy === `restore-${identity.id}` ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}恢复此版本</Button> : null}
              </div>
            </article>
          })}</div>
        </section> : null}

        {['TEXT_CHANGED', 'RE_REVIEW_REQUIRED'].includes(character.status) ? <div className="character-change-decision"><AlertTriangle size={18} /><div><strong>文字设定包含重大身份变化</strong><small>已锁定身份与既有镜头不会自动覆盖，请明确选择。</small></div><Button disabled={busy !== null} onClick={() => void run(`preserve-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'PRESERVE_IDENTITY'))} variant="secondary">保留原身份</Button><Button disabled={busy !== null} onClick={() => void run(`regenerate-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'REGENERATE'))}>重新生成</Button></div> : null}
      </article>
    })}</div>
    </div>

    {allLocked ? <section className="character-lock-bar">
      <div>
        <Check size={18} />
        <span>
          <strong>全部角色身份已锁定</strong>
          <small>后续剧本与分镜将引用这些固定版本。</small>
        </span>
      </div>
      <Link className="button button--primary button--md" to={`/projects/${projectId}/story`}>
        查看剧本进度
      </Link>
    </section> : null}

    <Modal
      className="modal--identity-view"
      description={identityViewAction?.mode === 'adjust' ? '以当前图片为参考生成调整版；新图成功前，当前图片会继续保留。' : '重新生成同一视角；新图成功前，当前图片会继续保留。'}
      footer={<>
        <Button disabled={busy !== null} onClick={() => { setIdentityViewAction(null); setIdentityViewNote('') }} variant="secondary">取消</Button>
        <Button disabled={busy !== null || (identityViewAction?.mode === 'adjust' && identityViewNote.trim().length < 4)} onClick={() => void submitIdentityViewAction()}>
          {busy?.startsWith('identity-view-') ? <LoaderCircle className="spin" size={15} /> : identityViewAction?.mode === 'adjust' ? <Sparkles size={15} /> : <RefreshCw size={15} />}
          {identityViewAction?.mode === 'adjust' ? '生成调整版' : '确认重新生成'}
        </Button>
      </>}
      onClose={() => { setIdentityViewAction(null); setIdentityViewNote('') }}
      open={identityViewAction !== null}
      title={identityViewAction ? `${identityViewAction.mode === 'adjust' ? '调整细节' : '重新生成'} · ${identityViewAction.characterName} ${identityViewAction.viewLabel}` : '调整角色身份图'}
    >
      {identityViewAction ? <div className="identity-view-dialog">
        <img alt={`${identityViewAction.characterName} ${identityViewAction.viewLabel} 当前版本`} src={identityViewAction.assetUrl} />
        <div>
          <div className="identity-view-dialog__summary"><span>{identityViewAction.viewLabel}</span><strong>{identityViewAction.mode === 'adjust' ? '只调整你指定的细节' : '生成一个身份一致的替代版本'}</strong><p>{identityViewAction.mode === 'adjust' ? '脸型、五官、年龄感、体型、发型和服装默认保持不变。' : '沿用已选角色身份与当前视角要求，画面细节会自然变化。'}</p></div>
          {identityViewAction.mode === 'adjust' ? <label className="identity-view-dialog__field" htmlFor="identity-view-refinement-note"><span>希望调整什么？</span><textarea autoFocus id="identity-view-refinement-note" maxLength={300} onChange={(event) => setIdentityViewNote(event.target.value)} placeholder="例如：保留五官和发型，让视线更坚定，减少笑意" rows={4} value={identityViewNote} /><small>{identityViewNote.trim().length}/300 · 至少输入 4 个字</small></label> : null}
          <div className="identity-view-dialog__notice" role="note"><ShieldCheck size={16} /><span><strong>{identityViewAction.createsNewVersion ? '当前角色基准不会被修改' : '当前图片不会立即被覆盖'}</strong><small>{identityViewAction.createsNewVersion ? '系统会复制为新的待确认版本并重新生成这个视角；确认锁定前，现有基准和已绑定镜头保持不变。' : '生成成功后才替换这个待确认视角；失败时仍保留现在的图片。'}</small></span></div>
        </div>
      </div> : null}
    </Modal>

    <Modal
      className="modal--identity-gallery"
      description={identityVersionGallery ? `${identityVersionGallery.characterName} · 第 ${identityVersionGallery.identityVersion} 版角色基准` : undefined}
      footer={<Button onClick={() => setIdentityVersionGallery(null)} variant="secondary">关闭</Button>}
      onClose={() => setIdentityVersionGallery(null)}
      open={identityVersionGallery !== null}
      title="查看多角度角色形象"
    >
      {identityVersionGallery ? <div className="identity-version-gallery">
        {[...identityVersionGallery.assets]
          .sort((left, right) => (
            dossierViewOrder(left.viewType) - dossierViewOrder(right.viewType)
          ))
          .map((asset) => {
            const label = dossierViewLabel(asset.viewType, identityVersionGallery.entityKind)
            return <article key={asset.id}>
              <button aria-label={`查看${identityVersionGallery.characterName}${label}完整原图`} className="identity-version-gallery__preview" onClick={() => openIdentityGalleryAsset(asset)} type="button">
                <img alt={`${identityVersionGallery.characterName} ${label}`} src={asset.assetUrl} />
              </button>
              <footer>
                <strong>{label}</strong>
                <div>
                  <button aria-label={`查看${identityVersionGallery.characterName}${label}完整原图`} onClick={() => openIdentityGalleryAsset(asset)} type="button"><Maximize2 size={14} />查看原图</button>
                  <button aria-label={`重新生成${identityVersionGallery.characterName}${label}`} disabled={busy !== null} onClick={() => regenerateIdentityGalleryAsset(asset)} type="button"><RefreshCw size={14} />重新生成</button>
                </div>
              </footer>
            </article>
          })}
      </div> : null}
    </Modal>

    <Modal
      className="modal--identity-image-viewer"
      description={candidateImageViewer ? `${candidateImageViewer.characterName} · ${candidateImageViewer.variantLabel} · 完整原图` : undefined}
      footer={<>
        <Button onClick={closeCandidateImageViewer} variant="secondary">关闭</Button>
        {candidateImageViewer?.selectable ? <Button disabled={busy !== null || candidatePromptDraft.trim().length < 20} onClick={() => void regenerateCandidateFromPrompt()} variant="secondary">{busy === `custom-prompt-${candidateImageViewer.candidateId}` ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}使用此提示词重新生成</Button> : null}
        {candidateImageViewer?.selectable ? <Button onClick={selectCandidateFromViewer}><Check size={15} />选择此形象</Button> : null}
      </>}
      onClose={closeCandidateImageViewer}
      open={candidateImageViewer !== null}
      title={candidateImageViewer ? `${candidateImageViewer.variantLabel}原图` : '查看候选原图'}
    >
      {candidateImageViewer ? <div className="identity-image-viewer">
        <div className="identity-image-viewer__toolbar">
          <span><Maximize2 size={15} /><strong>{candidateImageZoom === 100 ? '适应画面' : `${candidateImageZoom}%`}</strong></span>
          <div>
            <Button aria-label="缩小候选原图" disabled={candidateImageZoom <= 100} onClick={() => setCandidateImageZoom((current) => Math.max(100, current - 25))} size="sm" variant="secondary"><ZoomOut size={15} /></Button>
            <Button disabled={candidateImageZoom === 100} onClick={() => setCandidateImageZoom(100)} size="sm" variant="secondary">复位</Button>
            <Button aria-label="放大候选原图" disabled={candidateImageZoom >= 200} onClick={() => setCandidateImageZoom((current) => Math.min(200, current + 25))} size="sm" variant="secondary"><ZoomIn size={15} /></Button>
          </div>
        </div>
        <div className="identity-image-viewer__viewport">
          <div className="identity-image-viewer__canvas" style={{ height: `${candidateImageZoom}%`, width: `${candidateImageZoom}%` }}>
            <img alt={`${candidateImageViewer.characterName} ${candidateImageViewer.variantLabel}完整原图`} draggable={false} src={candidateImageViewer.assetUrl} />
          </div>
        </div>
        <details className="candidate-prompt-editor" open>
          <summary>查看并编辑生成提示词</summary>
          <label>
            <span>本图实际使用的提示词</span>
            <textarea disabled={!candidateImageViewer.selectable || busy !== null} maxLength={6000} onChange={(event) => setCandidatePromptDraft(event.target.value)} rows={5} value={candidatePromptDraft} />
          </label>
          <small>{candidateImageViewer.selectable ? '修改后点击“使用此提示词重新生成”。系统会继续执行纯白背景、角色身份一致性与无水印硬约束。' : '该候选已不属于当前角色设定版本，仅供查看。'} · {candidatePromptDraft.length}/6000</small>
        </details>
      </div> : null}
    </Modal>

    <Modal
      className="modal--identity-image-viewer"
      description={identityImageViewer ? `${identityImageViewer.characterName} · ${identityImageViewer.viewLabel} · 完整原图` : undefined}
      footer={<>
        <Button onClick={closeIdentityImageViewer} variant="secondary">关闭</Button>
        {!identityImageViewer?.readOnly ? <Button onClick={regenerateIdentityImageViewer}><RefreshCw size={15} />重新生成</Button> : null}
      </>}
      onClose={closeIdentityImageViewer}
      open={identityImageViewer !== null}
      title={identityImageViewer ? `${identityImageViewer.viewLabel}原图` : '查看角色原图'}
    >
      {identityImageViewer ? <div className="identity-image-viewer">
        <div className="identity-image-viewer__toolbar">
          <span><Maximize2 size={15} /><strong>{identityImageZoom === 100 ? '适应画面' : `${identityImageZoom}%`}</strong></span>
          <div>
            <Button aria-label="缩小原图" disabled={identityImageZoom <= 100} onClick={() => setIdentityImageZoom((current) => Math.max(100, current - 25))} size="sm" variant="secondary"><ZoomOut size={15} /></Button>
            <Button disabled={identityImageZoom === 100} onClick={() => setIdentityImageZoom(100)} size="sm" variant="secondary">复位</Button>
            <Button aria-label="放大原图" disabled={identityImageZoom >= 200} onClick={() => setIdentityImageZoom((current) => Math.min(200, current + 25))} size="sm" variant="secondary"><ZoomIn size={15} /></Button>
          </div>
        </div>
        <div className="identity-image-viewer__viewport">
          <div className="identity-image-viewer__canvas" style={{ height: `${identityImageZoom}%`, width: `${identityImageZoom}%` }}>
            <img alt={`${identityImageViewer.characterName} ${identityImageViewer.viewLabel}完整原图`} draggable={false} src={identityImageViewer.assetUrl} />
          </div>
        </div>
        <small>使用放大按钮查看面部、服装和全身细节；放大后可滚动画布。</small>
      </div> : null}
    </Modal>
  </div>
}
