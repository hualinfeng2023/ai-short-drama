import { describe, expect, it } from 'vitest'
import { buildCharacterVisualSummary } from './characterVisualSummary'

describe('buildCharacterVisualSummary', () => {
  it('removes a repeated leading age and duplicate wardrobe description', () => {
    expect(buildCharacterVisualSummary({
      age: '26岁',
      occupation: '待明确职业',
      identifyingFeatures: '26岁，留齐肩碎发，背着洗得发白的帆布包',
      gaze: '稳定注视，保留环境警觉',
      wardrobe: '26岁，留齐肩碎发，背着洗得发白的帆布包',
      fallbackSummary: '26岁 · 待明确职业 · 26岁，留齐肩碎发',
    })).toBe('26岁 · 待明确职业 · 留齐肩碎发，背着洗得发白的帆布包 · 稳定注视，保留环境警觉')
  })

  it('keeps a distinct wardrobe description', () => {
    expect(buildCharacterVisualSummary({
      age: '52岁',
      occupation: '食堂员工',
      identifyingFeatures: '齐耳短发，有几根白发',
      gaze: '目光温和',
      wardrobe: '藏蓝色食堂工作服',
    })).toBe('52岁 · 食堂员工 · 齐耳短发，有几根白发 · 目光温和 · 藏蓝色食堂工作服')
  })
})
