import { describe, expect, it } from 'vitest'
import {
  identityIssueViewTypes,
  isReviewableCharacterIdentityStatus,
} from './characterIdentityReview'

describe('character identity review states', () => {
  it('keeps quality-review-required identities visible and editable', () => {
    expect(isReviewableCharacterIdentityStatus('GENERATING_DOSSIER')).toBe(true)
    expect(isReviewableCharacterIdentityStatus('READY_FOR_REVIEW')).toBe(true)
    expect(isReviewableCharacterIdentityStatus('QC_REVIEW_REQUIRED')).toBe(true)
    expect(isReviewableCharacterIdentityStatus('LOCKED')).toBe(false)
  })

  it('combines failed assets and failed jobs without double counting a view', () => {
    expect(identityIssueViewTypes(
      [
        { viewType: 'FRONT', status: 'QC_FAILED' },
        { viewType: 'PROFILE', status: 'READY' },
      ],
      [
        { viewType: 'FRONT', status: 'SUCCEEDED' },
        { viewType: 'FULL_BODY', status: 'FAILED' },
      ],
    )).toEqual(['FRONT', 'FULL_BODY'])
  })
})
