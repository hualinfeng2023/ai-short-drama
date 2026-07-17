import { describe, expect, it } from 'vitest'
import { TOPIC_SLATE_MIX, narrativeTargetingMissing } from './narrativeTargeting'

describe('independent narrative targeting', () => {
  it('keeps the first slate mix by production format', () => {
    expect(TOPIC_SLATE_MIX.live_action).toEqual({
      female_frequency: 50,
      general: 30,
      male_frequency: 20,
    })
    expect(TOPIC_SLATE_MIX.ai_comic).toEqual({
      male_frequency: 50,
      general: 30,
      female_frequency: 20,
    })
    expect(TOPIC_SLATE_MIX.high_concept_fantasy).toEqual(
      TOPIC_SLATE_MIX.ai_comic,
    )
  })

  it('requires protagonist and emotional reward without inferring either from audience', () => {
    expect(narrativeTargetingMissing('unspecified', [])).toEqual(['叙事主角', '情绪回报'])
    expect(narrativeTargetingMissing('male', ['family'])).toEqual([])
  })
})
