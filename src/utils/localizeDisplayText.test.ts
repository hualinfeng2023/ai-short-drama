import { describe, expect, it } from 'vitest'

import { localizeDisplayText } from './localizeDisplayText'

describe('localizeDisplayText', () => {
  it('displays internal project genre values in Simplified Chinese', () => {
    expect(localizeDisplayText('costume_intrigue')).toBe('古装权谋')
    expect(localizeDisplayText('fantasy')).toBe('奇幻')
    expect(localizeDisplayText('suspense')).toBe('悬疑')
    expect(localizeDisplayText('urban_drama')).toBe('都市情感')
  })

  it('preserves unknown project genre values for forward compatibility', () => {
    expect(localizeDisplayText('new_genre')).toBe('new_genre')
  })

  it('localizes character dossier view labels embedded in task names', () => {
    expect(localizeDisplayText('林微 · 身份档案 · FULL_BODY · 细节调整')).toBe('林微 · 身份档案 · 全身 · 细节调整')
    expect(localizeDisplayText('张莉 · 身份档案 · THREE_QUARTER · 重新生成')).toBe('张莉 · 身份档案 · 45°侧面 · 重新生成')
    expect(localizeDisplayText('李大伟 · 身份档案 · EXPRESSIONS · 重新生成')).toBe('李大伟 · 身份档案 · 基础表情组 · 重新生成')
  })
})
