import { describe, expect, it } from 'vitest'
import {
  buildCharacterVisualFacts,
  buildCharacterVisualSummary,
} from './characterVisualSummary'

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

  it('builds scannable facts and marks incomplete identity fields', () => {
    expect(buildCharacterVisualFacts({
      age: '26岁',
      occupation: '待明确职业',
      identifyingFeatures: '26岁，留齐肩碎发，背着洗得发白的帆布包',
      gaze: '温和但强烈',
      wardrobe: '通勤休闲装',
    })).toEqual([
      { label: '年龄', value: '26岁', needsAttention: false },
      { label: '职业', value: '待明确职业', needsAttention: true },
      {
        label: '识别特征',
        value: '留齐肩碎发，背着洗得发白的帆布包',
        needsAttention: false,
      },
      { label: '眼神', value: '温和但强烈', needsAttention: false },
      { label: '造型', value: '通勤休闲装', needsAttention: false },
    ])
  })
})
