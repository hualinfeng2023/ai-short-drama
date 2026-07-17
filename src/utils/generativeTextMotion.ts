export function getGenerativeRevealDuration(characterCount: number): number {
  return Math.min(2400, Math.max(1100, characterCount * 2.4))
}

export function getGenerativeRevealProgress(elapsedMs: number, durationMs: number): number {
  if (durationMs <= 0) return 1
  const normalized = Math.min(1, Math.max(0, elapsedMs / durationMs))
  return 1 - (1 - normalized) ** 3
}

export function getGenerativeRevealCharacterCount(
  characterCount: number,
  elapsedMs: number,
  durationMs: number,
): number {
  return Math.min(
    characterCount,
    Math.floor(characterCount * getGenerativeRevealProgress(elapsedMs, durationMs)),
  )
}
