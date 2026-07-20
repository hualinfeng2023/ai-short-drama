export function buildCandidateGenerationSlots<T extends { ordinal: number }>(
  candidates: T[],
  requestedCount: number,
  generating: boolean,
): Array<T | null> {
  const sortedCandidates = [...candidates].sort((left, right) => left.ordinal - right.ordinal)
  if (!generating) return sortedCandidates

  return Array.from({ length: requestedCount }, (_, slotIndex) => (
    sortedCandidates.find((candidate) => candidate.ordinal === slotIndex + 1) ?? null
  ))
}
