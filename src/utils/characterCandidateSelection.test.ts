import { describe, expect, it } from 'vitest'

import { isSelectableCharacterCandidate } from './characterCandidateSelection'

const readyCandidate = {
  profileVersionId: 'profile-current',
  reviewStatus: 'PENDING_SELECTION',
  status: 'READY',
}

describe('isSelectableCharacterCandidate', () => {
  it('allows a ready candidate from the current profile', () => {
    expect(
      isSelectableCharacterCandidate(readyCandidate, 'profile-current', false),
    ).toBe(true)
  })

  it('blocks a candidate that failed generation quality checks', () => {
    expect(
      isSelectableCharacterCandidate(
        { ...readyCandidate, reviewStatus: 'QC_FAILED', status: 'QC_FAILED' },
        'profile-current',
        false,
      ),
    ).toBe(false)
  })

  it('blocks stale and still-generating candidates', () => {
    expect(
      isSelectableCharacterCandidate(readyCandidate, 'profile-new', false),
    ).toBe(false)
    expect(
      isSelectableCharacterCandidate(readyCandidate, 'profile-current', true),
    ).toBe(false)
  })
})
