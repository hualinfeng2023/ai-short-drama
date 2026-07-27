type CharacterCandidateSelectionState = {
  profileVersionId?: string
  reviewStatus: string
  status: string
}

export function isSelectableCharacterCandidate(
  candidate: CharacterCandidateSelectionState,
  profileVersionId: string,
  generating: boolean,
): boolean {
  return (
    !generating
    && candidate.profileVersionId === profileVersionId
    && candidate.status === 'READY'
    && candidate.reviewStatus !== 'QC_FAILED'
  )
}
