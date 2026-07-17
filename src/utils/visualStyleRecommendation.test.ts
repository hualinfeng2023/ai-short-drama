import { describe, expect, it } from 'vitest'

import { recommendVisualStyle } from './visualStyleRecommendation'

describe('recommendVisualStyle', () => {
  it('recommends fantasy epic for a rebirth apocalypse story with magic pills', () => {
    expect(recommendVisualStyle('姐妹得到神秘药丸，重生后在末日求生。', 'fantasy')).toBe(
      'fantasy_epic',
    )
  })

  it('lets strong story nature override a broad genre', () => {
    expect(recommendVisualStyle('她追查密室失踪案背后的凶手。', 'urban_drama')).toBe(
      'dark_suspense',
    )
  })

  it('uses genre as a fallback when the idea has no strong visual signal', () => {
    expect(recommendVisualStyle('两个人重新认识彼此。', 'urban_romance')).toBe('warm_healing')
  })

  it('defaults to realistic cinematic for unknown or broad material', () => {
    expect(recommendVisualStyle('一个普通人做出一次选择。', 'urban_drama')).toBe(
      'realistic_cinematic',
    )
  })
})
