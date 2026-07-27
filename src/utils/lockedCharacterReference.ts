import type { CharacterVisualRecord } from '../api/client'

type CharacterReferenceSource = Pick<
  CharacterVisualRecord,
  'candidates' | 'identities' | 'lockedCandidateId' | 'lockedIdentityVersionId'
>

export function resolveLockedCharacterReferenceImage(
  character: CharacterReferenceSource,
): string | undefined {
  if (!character.lockedIdentityVersionId) return undefined

  const lockedIdentity = character.identities.find(
    (identity) => (
      identity.id === character.lockedIdentityVersionId
      && identity.status === 'LOCKED'
    ),
  )
  if (!lockedIdentity) return undefined

  return lockedIdentity.sourceCandidateAssetUrl
    ?? character.candidates.find(
      (candidate) => candidate.id === character.lockedCandidateId,
    )?.assetUrl
}
