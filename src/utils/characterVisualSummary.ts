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
  fallbackSummary = '',
}: CharacterVisualSummaryInput): string {
  const normalizedFeatures = identifyingFeatures.trim()
  const parts = [
    age.trim(),
    occupation.trim(),
    stripRepeatedLeadingAge(normalizedFeatures, age),
    gaze.trim(),
  ]
  if (wardrobe.trim() !== normalizedFeatures) {
    parts.push(wardrobe.trim())
  }

  const uniqueParts = parts.filter(
    (part, index) => part && parts.indexOf(part) === index,
  )
  return uniqueParts.join(' · ') || fallbackSummary.trim()
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
