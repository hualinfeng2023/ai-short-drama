interface CharacterVisualSummaryInput {
  age: string
  occupation: string
  identifyingFeatures: string
  gaze: string
  wardrobe: string
  fallbackSummary?: string
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
