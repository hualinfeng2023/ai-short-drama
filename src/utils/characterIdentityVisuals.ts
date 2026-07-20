const VISUAL_IDENTITY_SUFFIX_PATTERN =
  /\s*族裔／文化背景为“[^”]*”；具体外貌应与该身份一致，同时保留自然个体差异，不添加刻板五官、服饰或文化符号。?\s*$/u

const UNSPECIFIED_ETHNICITIES = new Set(['', 'unspecified', '未指定'])
const MAX_VISUAL_NOTES_LENGTH = 2000

export function stripVisualIdentityConstraint(visualNotes: string): string {
  return visualNotes.replace(VISUAL_IDENTITY_SUFFIX_PATTERN, '').trim()
}

export function syncVisualNotesWithEthnicity(visualNotes: string, ethnicity: string): string {
  const baseNotes = stripVisualIdentityConstraint(visualNotes)
  const normalizedEthnicity = ethnicity.trim().replace(/[“”]/g, '')
  if (UNSPECIFIED_ETHNICITIES.has(normalizedEthnicity)) return baseNotes

  const identityConstraint = `族裔／文化背景为“${normalizedEthnicity}”；具体外貌应与该身份一致，同时保留自然个体差异，不添加刻板五官、服饰或文化符号。`
  if (!baseNotes) return identityConstraint

  const availableBaseLength = Math.max(0, MAX_VISUAL_NOTES_LENGTH - identityConstraint.length - 1)
  return `${baseNotes.slice(0, availableBaseLength).trimEnd()} ${identityConstraint}`.trim()
}
