import { describe, expect, it } from 'vitest'

import {
  stripVisualIdentityConstraint,
  syncVisualNotesWithEthnicity,
} from './characterIdentityVisuals'

describe('syncVisualNotesWithEthnicity', () => {
  it('preserves individual traits and adds a non-stereotyping identity constraint', () => {
    const result = syncVisualNotesWithEthnicity(
      '短棕发，眼神疲惫，穿剪裁简洁的深灰色服装。',
      '台湾裔美国人',
    )

    expect(result).toContain('短棕发，眼神疲惫')
    expect(result).toContain('族裔／文化背景为“台湾裔美国人”')
    expect(result).toContain('不添加刻板五官、服饰或文化符号')
  })

  it('replaces the previous identity constraint instead of accumulating labels', () => {
    const first = syncVisualNotesWithEthnicity('自然卷发，身形高挑。', '韩裔美国人')
    const second = syncVisualNotesWithEthnicity(first, '越南裔美国人')

    expect(second).not.toContain('韩裔美国人')
    expect(second.match(/族裔／文化背景为/g)).toHaveLength(1)
    expect(second).toContain('越南裔美国人')
  })

  it('removes the generated constraint when identity is unspecified', () => {
    const synced = syncVisualNotesWithEthnicity('佩戴细框眼镜。', '日裔美国人')

    expect(syncVisualNotesWithEthnicity(synced, '未指定')).toBe('佩戴细框眼镜。')
    expect(stripVisualIdentityConstraint(synced)).toBe('佩戴细框眼镜。')
  })

  it('keeps the value within the API limit', () => {
    const result = syncVisualNotesWithEthnicity('特征'.repeat(1200), '华裔美国人')

    expect(result.length).toBeLessThanOrEqual(2000)
    expect(result).toContain('族裔／文化背景为“华裔美国人”')
  })
})
