interface CharacterVisualSummaryInput {
  age: string
  height?: string
  entityKind?: string
  embodiment?: string
  genderExpression: string
  ethnicity: string
  occupation: string
  identifyingFeatures: string
  gaze: string
  wardrobe: string
  hairstyle?: string
  hairColor?: string
  fallbackSummary?: string
}

export interface CharacterVisualFact {
  label: string
  value: string
  needsAttention: boolean
}

function stripRepeatedLeadingAge(value: string, age: string): string {
  const text = value.trim()
  const normalizedAge = age.trim()
  if (!normalizedAge || !text.startsWith(normalizedAge)) {
    return text
  }
  return text.slice(normalizedAge.length).replace(/^[\s,，、·:：；;]+/, '')
}

export function buildCharacterVisualSummary({
  age,
  occupation,
  identifyingFeatures,
  gaze,
  wardrobe,
  hairstyle = '',
  hairColor = '',
  fallbackSummary = '',
}: CharacterVisualSummaryInput): string {
  const normalizedFeatures = identifyingFeatures.trim()
  const featureText = stripRepeatedLeadingAge(normalizedFeatures, age)
  const hairDetail = [hairColor.trim(), hairstyle.trim()].filter(Boolean).join('，')
  const parts = [
    age.trim(),
    occupation.trim(),
    featureText,
  ]
  if (hairDetail && !featureText.includes(hairDetail) && !featureText.includes(hairstyle.trim())) {
    parts.push(hairDetail)
  }
  parts.push(gaze.trim())
  if (wardrobe.trim() !== normalizedFeatures) {
    parts.push(wardrobe.trim())
  }

  const uniqueParts = parts.filter(
    (part, index) => part && parts.indexOf(part) === index,
  )
  return uniqueParts.join(' · ') || fallbackSummary.trim()
}

interface CharacterCardDescriptionInput {
  age: string
  genderExpression?: string
  height?: string
  occupation: string
  identifyingFeatures: string
  wardrobe?: string
  hairColor?: string
  hairstyle?: string
  fallbackSummary?: string
}

function truncateCardPart(value: string, maxLength: number): string {
  const text = value.trim()
  if (!text) return ''
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength).replace(/[\s,，、·;；]+$/, '')}…`
}

function compactCardAge(age: string): string {
  const text = age.trim()
  if (!text) return ''
  const rangeMatch = text.match(/(\d{1,3})\s*[-~～]\s*(\d{1,3})/)
  if (rangeMatch) return `${rangeMatch[1]}-${rangeMatch[2]}岁`
  const numberMatch = text.match(/(\d{1,3})\s*岁?/)
  if (numberMatch) return `${numberMatch[1]}岁`
  const segment = text.split(/[,，]/)[0]?.trim() ?? ''
  return truncateCardPart(segment, 10)
}

function compactCardGender(genderExpression: string): string {
  const normalized = genderExpression.trim().replace(/^(男性|女性|非二元性别)表达$/, '$1')
  if (!normalized || /待明确|待补充|未明确|未指定|暂无|不适用|未知|按角色设定自然表达/.test(normalized)) {
    return ''
  }
  return normalized
}

function compactCardHeight(height: string): string {
  const normalized = height.trim()
  if (!normalized || /待明确|待补充|未明确|未指定|暂无|不适用|未知/.test(normalized)) {
    return ''
  }
  return normalized
}

function pickCardVisualHook(
  identifyingFeatures: string,
  wardrobe: string,
  hairColor: string,
  hairstyle: string,
  age: string,
  occupation: string,
): string {
  const ageNumber = age.match(/\d{1,3}/)?.[0] ?? ''
  const context = `${age} ${occupation}`.toLowerCase()
  const sources = [
    identifyingFeatures,
    wardrobe,
    [hairColor.trim(), hairstyle.trim()].filter(Boolean).join('，'),
  ]

  for (const source of sources) {
    const text = source.trim()
    if (!text || /待明确|待补充|未明确|未指定|暂无/.test(text)) continue
    const clauses = text.split(/[,，;；]/).map((clause) => clause.trim()).filter(Boolean)
    for (const clause of clauses) {
      if (ageNumber && new RegExp(`\\b${ageNumber}\\b`).test(clause) && clauses.length > 1) continue
      if (context.includes(clause.toLowerCase())) continue
      return truncateCardPart(clause, 28)
    }
  }
  return ''
}

export function buildCharacterCardDescription({
  age,
  genderExpression = '',
  height = '',
  occupation,
  identifyingFeatures,
  wardrobe = '',
  hairColor = '',
  hairstyle = '',
  fallbackSummary = '',
}: CharacterCardDescriptionInput): string {
  const parts: string[] = []
  const compactAge = compactCardAge(age)
  const compactGender = compactCardGender(genderExpression)
  const compactHeight = compactCardHeight(height)
  const compactOccupation = truncateCardPart(occupation, 28)
  if (compactAge) parts.push(compactAge)
  if (compactGender) parts.push(compactGender)
  if (compactHeight) parts.push(compactHeight)
  if (compactOccupation && !/待明确|待补充|未明确|未指定|暂无/.test(compactOccupation)) {
    parts.push(compactOccupation)
  }

  const hook = pickCardVisualHook(
    identifyingFeatures,
    wardrobe,
    hairColor,
    hairstyle,
    age,
    occupation,
  )
  if (hook && !parts.some((part) => part.includes(hook) || hook.includes(part))) {
    parts.push(hook)
  }

  if (parts.length > 0) return parts.join(' · ')
  return truncateCardPart(fallbackSummary, 72) || '暂无角色描述'
}

function needsCompletion(value: string): boolean {
  const normalized = value.trim()
  return !normalized || /待明确|待补充|未明确|未指定|未知|暂无/.test(normalized)
}

function normalizeEthnicity(value: string): string {
  const normalized = value.trim()
  if (
    !normalized
    || /未指定|不(?:得|应|要)?(?:从.+)?推断|不使用刻板标签|按故事发生地/.test(normalized)
  ) {
    return ''
  }
  return normalized
}

function normalizeGender(value: string): string {
  return value.trim().replace(/^(男性|女性|非二元性别)表达$/, '$1')
}

export function buildCharacterVisualFacts({
  age,
  height = '',
  entityKind = 'HUMAN',
  embodiment = '',
  genderExpression,
  ethnicity,
  occupation,
  identifyingFeatures,
  gaze,
  wardrobe,
}: CharacterVisualSummaryInput): CharacterVisualFact[] {
  const normalizedAge = age.trim()
  const normalizedHeight = height.trim()
  const normalizedGenderExpression = normalizeGender(genderExpression)
  const normalizedEthnicity = normalizeEthnicity(ethnicity)
  const normalizedOccupation = occupation.trim()
  const normalizedFeatures = stripRepeatedLeadingAge(identifyingFeatures, age)
  const normalizedEntityKind = entityKind.trim().toUpperCase()
  if (normalizedEntityKind !== 'HUMAN') {
    const entityLabels: Record<string, string> = {
      DIGITAL_ENTITY: '数字实体',
      ROBOT: '机器人',
      CREATURE: '非人型生物',
      OBJECT: '拟人化物体',
    }
    const nonHumanFacts: CharacterVisualFact[] = [
      {
        label: '角色形态',
        value: entityLabels[normalizedEntityKind] ?? entityKind,
        needsAttention: false,
      },
      {
        label: normalizedEntityKind === 'DIGITAL_ENTITY' ? '运行时长' : '存在时长',
        value: normalizedAge || '未指定',
        needsAttention: needsCompletion(normalizedAge),
      },
      {
        label: normalizedEntityKind === 'DIGITAL_ENTITY' ? '系统定位' : '角色定位',
        value: normalizedOccupation || '待补充',
        needsAttention: needsCompletion(normalizedOccupation),
      },
      {
        label: '呈现载体',
        value: embodiment.trim() || '待补充',
        needsAttention: needsCompletion(embodiment),
      },
    ]
    if (normalizedFeatures) {
      nonHumanFacts.push({
        label: '视觉特征',
        value: normalizedFeatures,
        needsAttention: false,
      })
    }
    return nonHumanFacts
  }
  const facts: CharacterVisualFact[] = [
    {
      label: '年龄',
      value: normalizedAge || '待补充',
      needsAttention: needsCompletion(normalizedAge),
    },
    {
      label: '身高',
      value: normalizedHeight || '未指定',
      needsAttention: needsCompletion(normalizedHeight),
    },
    {
      label: '性别',
      value: normalizedGenderExpression || '待明确',
      needsAttention: needsCompletion(normalizedGenderExpression),
    },
    {
      label: '种族/族裔',
      value: normalizedEthnicity || '未指定',
      needsAttention: needsCompletion(normalizedEthnicity),
    },
    {
      label: '职业',
      value: normalizedOccupation || '待补充',
      needsAttention: needsCompletion(normalizedOccupation),
    },
  ]

  if (normalizedFeatures) {
    facts.push({
      label: '识别特征',
      value: normalizedFeatures,
      needsAttention: false,
    })
  }
  if (gaze.trim()) {
    facts.push({
      label: '眼神',
      value: gaze.trim(),
      needsAttention: false,
    })
  }
  if (wardrobe.trim() && wardrobe.trim() !== identifyingFeatures.trim()) {
    facts.push({
      label: '造型',
      value: wardrobe.trim(),
      needsAttention: false,
    })
  }

  return facts
}
