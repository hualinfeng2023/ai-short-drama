import { describe, expect, it } from 'vitest'
import { directionKeyLabel, isQuestionStyleHook } from './storyDirection'

describe('story direction review helpers', () => {
  it('识别提问式和互动式结尾钩子', () => {
    expect(isQuestionStyleHook('如果你重生一次，你会怎么选？')).toBe(true)
    expect(isQuestionStyleHook('真正的幕后者现身，并带走主角的妹妹')).toBe(false)
  })

  it('把生成键翻译为可决策的方向标签', () => {
    expect(directionKeyLabel('emotion')).toBe('情绪驱动')
    expect(directionKeyLabel('plot_focused')).toBe('强情节')
    expect(directionKeyLabel('hook_focused')).toBe('市场钩子')
    expect(directionKeyLabel('hook_centric')).toBe('市场钩子')
    expect(directionKeyLabel('plot_centric')).toBe('强情节')
    expect(directionKeyLabel('emotion_centric')).toBe('情绪驱动')
    expect(directionKeyLabel('market')).toBe('市场钩子')
  })
})
