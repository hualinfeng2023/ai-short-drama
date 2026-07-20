export function resolveBatchFirstOrdinal(
  candidates: Array<{ ordinal: number; batchId?: string }>,
  batchId: string | undefined,
): number {
  if (!batchId) return 1
  const otherOrdinals = candidates
    .filter((candidate) => candidate.batchId !== batchId)
    .map((candidate) => candidate.ordinal)
  return (otherOrdinals.length > 0 ? Math.max(...otherOrdinals) : 0) + 1
}

export function buildCandidateGenerationSlots<T extends { ordinal: number }>(
  candidates: T[],
  requestedCount: number,
  generating: boolean,
  batchFirstOrdinal = 1,
): Array<T | null> {
  const sortedCandidates = [...candidates].sort((left, right) => left.ordinal - right.ordinal)
  if (!generating) return sortedCandidates

  return Array.from({ length: requestedCount }, (_, slotIndex) => (
    sortedCandidates.find((candidate) => candidate.ordinal === batchFirstOrdinal + slotIndex) ?? null
  ))
}
