import { describe, expect, it } from 'vitest'
import type { CharacterVisualRecord } from '../api/client'
import { resolveLockedCharacterReferenceImage } from './lockedCharacterReference'

type CharacterReferenceSource = Pick<
  CharacterVisualRecord,
  'candidates' | 'identities' | 'lockedCandidateId' | 'lockedIdentityVersionId'
>

function characterReference(
  changes: Partial<CharacterReferenceSource> = {},
): CharacterReferenceSource {
  return {
    candidates: [],
    identities: [],
    ...changes,
  }
}

describe('resolveLockedCharacterReferenceImage', () => {
  it('returns the source image from the currently locked identity', () => {
    expect(resolveLockedCharacterReferenceImage(characterReference({
      lockedIdentityVersionId: 'identity-2',
      identities: [{
        id: 'identity-2',
        version: 2,
        sourceCandidateId: 'candidate-2',
        profileVersionId: 'profile-1',
        status: 'LOCKED',
        sourceCandidateAssetUrl: '/locked-wife.png',
        assets: [],
        viewJobs: [],
      }],
    }))).toBe('/locked-wife.png')
  })

  it('falls back to the locked candidate asset when the identity omits its source URL', () => {
    expect(resolveLockedCharacterReferenceImage(characterReference({
      lockedCandidateId: 'candidate-2',
      lockedIdentityVersionId: 'identity-2',
      candidates: [{
        id: 'candidate-2',
        ordinal: 2,
        assetId: 'asset-2',
        assetUrl: '/candidate-wife.png',
        seed: 'seed-2',
        status: 'READY',
        reviewStatus: 'LOCKED',
        qualityIssues: [],
        selected: true,
        deletable: false,
        generationPrompt: '',
      }],
      identities: [{
        id: 'identity-2',
        version: 2,
        sourceCandidateId: 'candidate-2',
        profileVersionId: 'profile-1',
        status: 'LOCKED',
        assets: [],
        viewJobs: [],
      }],
    }))).toBe('/candidate-wife.png')
  })

  it('does not expose an unlocked or superseded identity as the current avatar', () => {
    expect(resolveLockedCharacterReferenceImage(characterReference({
      lockedIdentityVersionId: 'identity-2',
      identities: [{
        id: 'identity-2',
        version: 2,
        sourceCandidateId: 'candidate-2',
        profileVersionId: 'profile-1',
        status: 'SUPERSEDED',
        sourceCandidateAssetUrl: '/stale-wife.png',
        assets: [],
        viewJobs: [],
      }],
    }))).toBeUndefined()
  })
})
