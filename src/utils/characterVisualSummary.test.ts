import { describe, expect, it } from 'vitest'
import {
  buildCharacterVisualFacts,
  buildCharacterVisualSummary,
} from './characterVisualSummary'

describe('buildCharacterVisualSummary', () => {
  it('removes a repeated leading age and duplicate wardrobe description', () => {
    expect(buildCharacterVisualSummary({
      age: '26岁',
      genderExpression: '女性表达',
      ethnicity: '东亚裔',
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
      genderExpression: '男性表达',
      ethnicity: '未指定',
      occupation: '食堂员工',
      identifyingFeatures: '齐耳短发，有几根白发',
      gaze: '目光温和',
      wardrobe: '藏蓝色食堂工作服',
    })).toBe('52岁 · 食堂员工 · 齐耳短发，有几根白发 · 目光温和 · 藏蓝色食堂工作服')
  })

  it('builds scannable facts and marks incomplete identity fields', () => {
    expect(buildCharacterVisualFacts({
      age: '26岁',
      height: '168 cm',
      genderExpression: '女性表达',
      ethnicity: '东亚裔',
      occupation: '待明确职业',
      identifyingFeatures: '26岁，留齐肩碎发，背着洗得发白的帆布包',
      gaze: '温和但强烈',
      wardrobe: '通勤休闲装',
    })).toEqual([
      { label: '年龄', value: '26岁', needsAttention: false },
      { label: '身高', value: '168 cm', needsAttention: false },
      { label: '性别', value: '女性', needsAttention: false },
      { label: '种族/族裔', value: '东亚裔', needsAttention: false },
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

  it('shows an unspecified ethnicity without inferring a default', () => {
    expect(buildCharacterVisualFacts({
      age: '38-42',
      genderExpression: '男性表达',
      ethnicity: '',
      occupation: '研发部门负责人',
      identifyingFeatures: '',
      gaze: '',
      wardrobe: '',
    })).toContainEqual({
      label: '种族/族裔',
      value: '未指定',
      needsAttention: true,
    })

    expect(buildCharacterVisualFacts({
      age: '38-42',
      genderExpression: '男性表达',
      ethnicity: '按故事发生地如实呈现，不使用刻板标签',
      occupation: '研发部门负责人',
      identifyingFeatures: '',
      gaze: '',
      wardrobe: '',
    })).toContainEqual({
      label: '种族/族裔',
      value: '未指定',
      needsAttention: true,
    })
  })

  it('shows an unspecified height without inferring one from identity fields', () => {
    expect(buildCharacterVisualFacts({
      age: '38-42',
      genderExpression: '男性表达',
      ethnicity: '西北欧背景',
      occupation: '研发部门负责人',
      identifyingFeatures: '',
      gaze: '',
      wardrobe: '',
    })).toContainEqual({
      label: '身高',
      value: '未指定',
      needsAttention: true,
    })
  })

  it('removes expression wording from the displayed gender', () => {
    expect(buildCharacterVisualFacts({
      age: '38-42',
      genderExpression: '男性表达',
      ethnicity: '东亚裔',
      occupation: '研发部门负责人',
      identifyingFeatures: '',
      gaze: '',
      wardrobe: '',
    })).toContainEqual({
      label: '性别',
      value: '男性',
      needsAttention: false,
    })
  })

  it('uses digital-entity facts instead of human casting attributes', () => {
    expect(buildCharacterVisualFacts({
      entityKind: 'DIGITAL_ENTITY',
      embodiment: '屏幕界面与全息投影',
      age: '运行 3 年',
      height: '',
      genderExpression: '不适用',
      ethnicity: '不适用',
      occupation: '自主通用人工智能系统',
      identifyingFeatures: '冷蓝色核心界面，无人类脸孔',
      gaze: '',
      wardrobe: '',
    })).toEqual([
      { label: '角色形态', value: '数字实体', needsAttention: false },
      { label: '运行时长', value: '运行 3 年', needsAttention: false },
      { label: '系统定位', value: '自主通用人工智能系统', needsAttention: false },
      { label: '呈现载体', value: '屏幕界面与全息投影', needsAttention: false },
      { label: '视觉特征', value: '冷蓝色核心界面，无人类脸孔', needsAttention: false },
    ])
  })
})
