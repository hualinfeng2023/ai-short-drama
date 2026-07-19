import { describe, expect, it } from 'vitest'
import { diffText } from './textDiff'

describe('剧本选区 Diff', () => {
  it('保留相同文字并区分删除和新增内容', () => {
    const parts = diffText('你现在必须离开这里', '你现在立刻离开这里！')
    expect(parts.some((part) => part.type === 'delete' && part.text.includes('必须'))).toBe(true)
    expect(parts.some((part) => part.type === 'insert' && part.text.includes('立刻'))).toBe(true)
    expect(parts.filter((part) => part.type === 'equal').map((part) => part.text).join('')).toContain('你现在')
  })

  it('按 Unicode 字符比较含表情的文本', () => {
    expect(diffText('停下🙂', '立刻停下🙂').at(-1)).toEqual({
      type: 'equal',
      text: '停下🙂',
    })
  })
})
