interface CharacterVisualSummaryInput {
  age: string
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
  return !normalized || /待明确|待补充|未明确|未知|暂无/.test(normalized)
}

export function buildCharacterVisualFacts({
  age,
  occupation,
  identifyingFeatures,
  gaze,
  wardrobe,
}: CharacterVisualSummaryInput): CharacterVisualFact[] {
  const normalizedAge = age.trim()
  const normalizedOccupation = occupation.trim()
  const normalizedFeatures = stripRepeatedLeadingAge(identifyingFeatures, age)
  const facts: CharacterVisualFact[] = [
    {
      label: '年龄',
      value: normalizedAge || '待补充',
      needsAttention: needsCompletion(normalizedAge),
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
