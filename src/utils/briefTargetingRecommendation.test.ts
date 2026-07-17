import { describe, expect, it } from 'vitest'
import { recommendGenre } from './briefTargetingRecommendation'

describe('brief genre recommendation', () => {
  it('recognizes ancient military merchant intrigue without assigning an audience', () => {
    const idea = '九州商主被休后，公主、将军、军营与粮仓卷入一场朝堂清算。'
    expect(recommendGenre(idea)).toBe('costume_intrigue')
  })
  it('recognizes suspense independently of protagonist gender', () => {
    const idea = '女侦探追查密室失踪案，发现连环凶手留下的反转线索。'
    expect(recommendGenre(idea)).toBe('urban_suspense')
  })
  it('recognizes fantasy without assigning an audience', () => {
    const idea = '少年得到神秘药丸，穿越末日世界觉醒异能。'
    expect(recommendGenre(idea)).toBe('fantasy')
  })
})
