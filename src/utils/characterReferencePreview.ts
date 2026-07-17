import type { Shot, ShotCharacterBinding } from '../types'

const SHOT_SIZE_SCORE: Record<Shot['shotSize'], number> = {
  CU: 40,
  MCU: 30,
  MS: 20,
  WS: 10,
}

function includesBinding(shot: Shot, binding: ShotCharacterBinding): boolean {
  return Boolean(
    shot.characterBindings?.some((item) => (
      item.id === binding.id || item.lockedCandidateId === binding.lockedCandidateId
    ))
    || shot.characterIds?.includes(binding.id),
  )
}

export function resolveCharacterReferencePreview(
  shots: Shot[],
  binding: ShotCharacterBinding,
): string {
  const bestFrame = shots
    .filter((shot) => Boolean(shot.currentImageUrl) && includesBinding(shot, binding))
    .sort((left, right) => {
      const identityScore = (shot: Shot) => shot.currentIdentityStatus === 'PASSED' ? 8 : 0
      return (
        SHOT_SIZE_SCORE[right.shotSize] + identityScore(right)
        - SHOT_SIZE_SCORE[left.shotSize] - identityScore(left)
      )
    })[0]

  return bestFrame?.currentImageUrl ?? binding.referenceAssetUrl
}
