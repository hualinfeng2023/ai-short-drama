import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Dna,
  GitCompare,
  LoaderCircle,
  LockKeyhole,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
} from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  applyCharacterVisualChange,
  confirmCharacterVisualProfile,
  fetchCharacterVisuals,
  generateCharacterVisualCandidates,
  lockCharacterVisualIdentity,
  selectCharacterVisualCandidate,
  updateCharacterVisualProfile,
  type CharacterVisualRecord,
  type CharacterVisualWorkspace,
} from '../api/client'
import { Button, EmptyState, PageHeader } from '../components/ui'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const STATUS_LABELS: Record<string, string> = {
  NOT_GENERATED: '尚未生成',
  GENERATING: '正在生成',
  CANDIDATES_READY: '已生成候选',
  PENDING_SELECTION: '待选择',
  PENDING_REVIEW: '待审核',
  REVIEW_REQUIRED: '待审核',
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

const FAMILY_SIMILARITY_LABELS: Record<string, string> = {
  LOW: '低 · 1 个家族特征',
  MEDIUM: '中 · 2 个家族特征',
  HIGH: '高 · 最多 3 个家族特征',
  VERY_HIGH: '很高 · 双胞胎级，仍保持独立身份',
}

interface ProfileDraft {
  age: string
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
  selectedDirection: string
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
    age: identity.age ?? '',
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
    selectedDirection: profile.selectedDirection ?? 'cinematic',
  }
}

function splitItems(value: string): string[] {
  return value.split(/[、,，；;]/).map((item) => item.trim()).filter(Boolean)
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? localizeDisplayText(status)
}

export function CharactersPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [workspace, setWorkspace] = useState<CharacterVisualWorkspace | null>(null)
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [compare, setCompare] = useState<Record<string, boolean>>({})
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ProfileDraft | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!projectId) return
    const next = await fetchCharacterVisuals(projectId)
    setWorkspace(next)
    setSelected((current) => Object.fromEntries(next.characters.map((character) => [
      character.id,
      current[character.id]
        ?? character.identities.at(-1)?.sourceCandidateId
        ?? character.candidates.find((candidate) => candidate.selected)?.id
        ?? '',
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

  function edit(character: CharacterVisualRecord) {
    setEditingId(character.id)
    setDraft(profileDraft(character))
  }

  async function saveProfile(character: CharacterVisualRecord) {
    if (!projectId || !draft) return
    await run(`save-${character.id}`, async () => {
      await updateCharacterVisualProfile(projectId, character.id, character.lockVersion, {
        identity_fields: {
          age: draft.age,
          gender_expression: draft.genderExpression,
          region: draft.region,
          era: draft.era,
          occupation: draft.occupation,
          social_class: draft.socialClass,
          story_identity: draft.storyIdentity,
        },
        appearance_fields: {
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
        selected_direction: draft.selectedDirection,
      })
      setEditingId(null)
      setDraft(null)
    })
  }

  const allLocked = Boolean(
    workspace?.characters.length
    && workspace.characters.every((character) => character.status === 'LOCKED'),
  )

  if (!loading && (!projectId || !workspace)) {
    return <div className="page"><EmptyState title="无法打开角色形象" description="角色文字设定或关系基线尚未准备，请返回故事工作区检查。" action={<Link className="button button--secondary button--md" to={projectId ? `/projects/${projectId}/story` : '/projects'}><ArrowLeft size={16} />返回角色文字设定</Link>} /></div>
  }

  if (loading || !projectId || !workspace) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在准备角色结构化视觉档案…</strong></div>
  }

  return <div className="page page--characters page--character-visuals">
    <PageHeader
      eyebrow="角色文字设定之后 · 角色形象"
      title="生成并锁定角色身份"
      description="系统已自动提取生图字段并完成一致性检查。只有你点击后才会生成图片；候选不会自动采用，锁定前也不会进入剧本和分镜。"
      actions={<><Link className="button button--secondary button--md" to={`/projects/${projectId}/story`}><ArrowLeft size={16} />返回角色文字设定</Link><Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button></>}
    />

    {error ? <div className="brief-save-message brief-save-message--error" role="alert"><AlertTriangle size={16} />{error}</div> : null}

    <section className="character-visual-policy">
      <div><ShieldCheck size={18} /><span><strong>系统自动准备</strong><small>字段提取、冲突检查、性格视觉化、3 个方向建议</small></span></div>
      <div><Sparkles size={18} /><span><strong>用户主动触发</strong><small>默认 3 张统一构图胸像，不在后台自动生图</small></span></div>
      <div><LockKeyhole size={18} /><span><strong>人工确认锁定</strong><small>身份、造型、剧情状态分版本保存，既有镜头不被覆盖</small></span></div>
    </section>

    <div className="character-visual-list">{workspace.characters.map((character) => {
      const profile = character.profile
      const familyConstraint = character.familyResemblanceConstraint
      const selectedCandidateId = selected[character.id]
      const selectedCandidate = character.candidates.find((item) => item.id === selectedCandidateId)
      const latestIdentity = character.identities.at(-1)
      const selectedHasIdentity = character.identities.some(
        (identity) => identity.sourceCandidateId === selectedCandidateId,
      )
      const isEditing = editingId === character.id && draft
      const blockers = profile?.conflictReport.filter((item) => item.severity === 'BLOCKER') ?? []
      const confirmed = profile?.status === 'CONFIRMED'
      const generating = character.status === 'GENERATING'
        || latestIdentity?.status === 'GENERATING_DOSSIER'
      return <article className="character-visual-card" key={character.id}>
        <header>
          <div><p className="eyebrow">{localizeDisplayText(character.role)}</p><h2>{character.name}</h2><p>{character.visualBrief}</p></div>
          <span className={`character-visual-status is-${character.status.toLowerCase()}`}>{statusLabel(character.status)}</span>
        </header>

        {profile ? <section className="character-profile-summary">
          <div className="character-profile-summary__heading"><div><span>结构化生图档案 · 第 {profile.version} 版</span><strong>{profile.identityFields.age} · {profile.identityFields.occupation}</strong></div><Button disabled={generating || busy !== null} onClick={() => edit(character)} size="sm" variant="secondary"><Pencil size={14} />调整设定</Button></div>
          <dl><div><dt>身份</dt><dd>{profile.identityFields.region} · {profile.identityFields.era} · {profile.identityFields.social_class}</dd></div><div><dt>外貌</dt><dd>{profile.appearanceFields.face_shape}；{profile.appearanceFields.identifying_features}</dd></div><div><dt>可见性格</dt><dd>{profile.personalityVisualization.expression}；{profile.personalityVisualization.gaze}</dd></div><div><dt>造型</dt><dd>{String(profile.stylingFields.wardrobe)}；{String(profile.stylingFields.colors)}</dd></div></dl>
          <div className="character-direction-list">{profile.recommendedDirections.map((direction) => <span className={direction.key === profile.selectedDirection ? 'is-selected' : ''} key={direction.key}><strong>{direction.label}</strong><small>{direction.reason}</small></span>)}</div>
          <div className="character-profile-audit"><strong><ShieldCheck size={15} />一致性审核</strong><ul>{profile.conflictReport.map((issue) => <li data-severity={issue.severity.toLowerCase()} key={issue.code}><span>{issue.message}</span><small>{issue.suggestion}</small></li>)}</ul></div>
        </section> : null}

        {familyConstraint ? <section className="character-family-constraint" data-status={familyConstraint.status.toLowerCase()}>
          <header><div><Dna size={17} /><span><strong>Family Resemblance Constraint · 第 {familyConstraint.version} 版</strong><small>{FAMILY_SIMILARITY_LABELS[familyConstraint.similarityLevel] ?? familyConstraint.similarityLevel}</small></span></div><em>{familyConstraint.status === 'ACTIVE' ? '已启用' : '等待亲属身份锁定'}</em></header>
          {familyConstraint.status === 'ACTIVE' ? <><div className="character-family-traits">{familyConstraint.inheritedFeatures.map((feature) => <span key={`${feature.field}-${feature.sourceIdentityVersionId}`}><strong>{feature.label}</strong><small>{feature.value}</small><em>来自 {feature.sourceCharacterName}</em></span>)}</div><p>{familyConstraint.temperamentAffinity.instruction}</p><ul>{familyConstraint.independenceConstraints.map((item) => <li key={item}>{item}</li>)}</ul></> : <p>关系网已标记为亲生血缘；首位家族角色仍可独立生成。锁定一位亲属后，系统才会为其他角色提取稳定家族特征，不会后台自动生图。</p>}
          {character.status === 'LOCKED' ? <small>当前身份已经锁定，新约束只用于未来主动重新生成，不会覆盖现有身份或镜头。</small> : null}
        </section> : null}

        {isEditing ? <section className="character-visual-editor">
          <header><div><span>生成前可调整</span><h3>外貌、造型与可见性格</h3></div><small>保存后会创建新版本并重新运行一致性审核</small></header>
          <div className="character-visual-editor__grid">
            <label>年龄<input onChange={(event) => setDraft({ ...draft, age: event.target.value })} value={draft.age} /></label>
            <label>性别表达<input onChange={(event) => setDraft({ ...draft, genderExpression: event.target.value })} value={draft.genderExpression} /></label>
            <label>地域<input onChange={(event) => setDraft({ ...draft, region: event.target.value })} value={draft.region} /></label>
            <label>时代<input onChange={(event) => setDraft({ ...draft, era: event.target.value })} value={draft.era} /></label>
            <label>职业<input onChange={(event) => setDraft({ ...draft, occupation: event.target.value })} value={draft.occupation} /></label>
            <label>阶层<input onChange={(event) => setDraft({ ...draft, socialClass: event.target.value })} value={draft.socialClass} /></label>
            <label className="is-wide">剧中身份<textarea onChange={(event) => setDraft({ ...draft, storyIdentity: event.target.value })} rows={2} value={draft.storyIdentity} /></label>
            <label>脸型<input onChange={(event) => setDraft({ ...draft, faceShape: event.target.value })} value={draft.faceShape} /></label>
            <label>五官<input onChange={(event) => setDraft({ ...draft, facialFeatures: event.target.value })} value={draft.facialFeatures} /></label>
            <label>眉眼<input onChange={(event) => setDraft({ ...draft, browEyeShape: event.target.value })} value={draft.browEyeShape} /></label>
            <label>鼻型<input onChange={(event) => setDraft({ ...draft, noseShape: event.target.value })} value={draft.noseShape} /></label>
            <label>嘴角<input onChange={(event) => setDraft({ ...draft, mouthCorner: event.target.value })} value={draft.mouthCorner} /></label>
            <label>肤色<input onChange={(event) => setDraft({ ...draft, skinTone: event.target.value })} value={draft.skinTone} /></label>
            <label>发型<input onChange={(event) => setDraft({ ...draft, hairstyle: event.target.value })} value={draft.hairstyle} /></label>
            <label>发质<input onChange={(event) => setDraft({ ...draft, hairTexture: event.target.value })} value={draft.hairTexture} /></label>
            <label>体型<input onChange={(event) => setDraft({ ...draft, bodyType: event.target.value })} value={draft.bodyType} /></label>
            <label>识别特征<input onChange={(event) => setDraft({ ...draft, identifyingFeatures: event.target.value })} value={draft.identifyingFeatures} /></label>
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
            <label>视觉方向<select onChange={(event) => setDraft({ ...draft, selectedDirection: event.target.value })} value={draft.selectedDirection}>{(profile?.recommendedDirections ?? []).map((direction) => <option key={direction.key} value={direction.key}>{direction.label}</option>)}</select></label>
          </div>
          <footer><Button onClick={() => { setEditingId(null); setDraft(null) }} variant="secondary">取消</Button><Button disabled={busy !== null} onClick={() => void saveProfile(character)}>{busy === `save-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}保存并重新审核</Button></footer>
        </section> : null}

        {!isEditing && profile && !confirmed && character.status !== 'LOCKED' ? <div className="character-baseline-gate"><div><strong>确认角色基线后才能生图</strong><small>{blockers.length ? `仍有 ${blockers.length} 个阻断问题` : '一致性审核已完成，确认不会触发生图。'}</small></div><Button disabled={blockers.length > 0 || busy !== null} onClick={() => void run(`confirm-${character.id}`, () => confirmCharacterVisualProfile(projectId, character.id, character.lockVersion, profile.id))}>{busy === `confirm-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}确认角色基线</Button></div> : null}

        {profile && confirmed && character.status !== 'LOCKED' ? <section className="character-candidate-section">
          <header><div><span>第一步 · 低成本候选</span><h3>统一构图正面胸像</h3><p>每批固定 3 张、相同背景与焦段，方便只比较身份差异。</p></div><div>{character.candidates.length ? <Button onClick={() => setCompare((current) => ({ ...current, [character.id]: !current[character.id] }))} size="sm" variant="secondary"><GitCompare size={14} />{compare[character.id] ? '退出比较' : '比较候选'}</Button> : null}<Button disabled={generating || busy !== null} onClick={() => void run(`generate-${character.id}`, () => generateCharacterVisualCandidates(projectId, character.id, character.lockVersion, profile.id))}>{busy === `generate-${character.id}` || generating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{character.candidates.length ? '生成更多' : '生成形象候选'}</Button></div></header>
          {character.candidates.length ? <div className={`character-candidate-grid ${compare[character.id] ? 'is-comparing' : ''}`}>{character.candidates.map((candidate) => <button aria-pressed={selectedCandidateId === candidate.id} className={`character-candidate ${selectedCandidateId === candidate.id ? 'character-candidate--selected' : ''}`} disabled={generating || candidate.profileVersionId !== profile.id} key={candidate.id} onClick={() => setSelected((current) => ({ ...current, [character.id]: candidate.id }))}><img alt={`${character.name} 形象候选 ${candidate.ordinal}`} src={candidate.assetUrl} /><span><strong>候选 {candidate.ordinal}</strong><small>统一胸像 · 种子 {candidate.seed}</small></span>{selectedCandidateId === candidate.id ? <em><Check size={15} />已选择</em> : null}</button>)}</div> : <div className="character-candidate-empty"><UserRoundCheck size={22} /><strong>尚未生成候选</strong><p>系统只准备了提示词与 3 个候选位，不会在后台调用生图服务。</p></div>}
          {selectedCandidate && !selectedHasIdentity && !generating ? <footer><div><strong>已选候选 {selectedCandidate.ordinal}</strong><small>确认后将生成正面、45°、侧面、全身与基础表情组；仍不会自动锁定。</small></div><Button disabled={busy !== null} onClick={() => void run(`select-${character.id}`, () => selectCharacterVisualCandidate(projectId, character.id, character.lockVersion, selectedCandidate.id))}><UserRoundCheck size={15} />选定并生成身份档案</Button></footer> : null}
        </section> : null}

        {latestIdentity ? <section className="character-identity-dossier">
          <header><div><span>第二步 · 身份档案</span><h3>Character Identity · 第 {latestIdentity.version} 版</h3></div><span>{localizeDisplayText(latestIdentity.status)}</span></header>
          <div>{latestIdentity.assets.map((asset) => <figure key={asset.id}><img alt={`${character.name} ${VIEW_LABELS[asset.viewType] ?? asset.viewType}`} src={asset.assetUrl} /><figcaption>{VIEW_LABELS[asset.viewType] ?? asset.viewType}</figcaption></figure>)}{latestIdentity.status === 'GENERATING_DOSSIER' ? <div className="character-dossier-loading"><LoaderCircle className="spin" size={20} />正在生成统一身份档案…</div> : null}</div>
          {latestIdentity.status === 'READY_FOR_REVIEW' ? <footer><div><strong>请人工检查五官、年龄感、体型和识别特征</strong><small>锁定后才会生成基础 Look Version，并允许剧本继续生成。</small></div><Button disabled={busy !== null} onClick={() => void run(`lock-${character.id}`, async () => { const result = await lockCharacterVisualIdentity(projectId, character.id, character.lockVersion, latestIdentity.id); if (result.script_job) navigate(`/tasks?project=${projectId}&jobType=GENERATE_SCRIPT_PACKAGE`) })}>{busy === `lock-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <LockKeyhole size={15} />}锁定角色身份</Button></footer> : null}
        </section> : null}

        {character.status === 'LOCKED' ? <div className="character-locked-summary"><LockKeyhole size={18} /><div><strong>角色身份已锁定</strong><small>Identity {character.lockedIdentityVersionId?.slice(0, 8)} · Look {character.activeLookVersionId?.slice(0, 8)} · Story State {character.activeStoryStateVersionId?.slice(0, 8)}</small></div></div> : null}
        {['TEXT_CHANGED', 'RE_REVIEW_REQUIRED'].includes(character.status) ? <div className="character-change-decision"><AlertTriangle size={18} /><div><strong>文字设定包含重大身份变化</strong><small>已锁定身份与既有镜头不会自动覆盖，请明确选择。</small></div><Button disabled={busy !== null} onClick={() => void run(`preserve-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'PRESERVE_IDENTITY'))} variant="secondary">保留原身份</Button><Button disabled={busy !== null} onClick={() => void run(`regenerate-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'REGENERATE'))}>重新生成</Button></div> : null}
      </article>
    })}</div>

    <section className="character-lock-bar"><div>{allLocked ? <Check size={18} /> : <LockKeyhole size={18} />}<span><strong>{allLocked ? '全部角色身份已锁定' : '等待全部角色人工锁定'}</strong><small>{allLocked ? '剧本任务已由最后一次锁定操作启动；后续分镜将引用这些固定版本。' : '台词和普通剧情修改不触发生图；服装与临时状态分别进入 Look Version 和 Story State。'}</small></span></div>{allLocked ? <Link className="button button--primary button--md" to={`/projects/${projectId}/story`}>查看剧本进度</Link> : null}</section>
  </div>
}
