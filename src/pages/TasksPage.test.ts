import { describe, expect, it } from 'vitest'
import { compactJobId, splitJobStage } from './TasksPage'

describe('task tracking code display', () => {
  it('keeps same-prefix task ids distinguishable', () => {
    expect(compactJobId('50000000-0000-4000-8000-000000000001')).toBe('5000…0001')
    expect(compactJobId('50000000-0000-4000-8000-000000000002')).toBe('5000…0002')
  })

  it('keeps already compact ids unchanged', () => {
    expect(compactJobId('task-42')).toBe('task-42')
  })
})

describe('task stage display', () => {
  it('removes redundant wait duration while keeping useful diagnostics', () => {
    expect(
      splitJobStage('正在生成故事设定与角色关系草案 · 已等待 20 秒 · 任务尝试 1/3'),
    ).toEqual({
      message: '正在生成故事设定与角色关系草案',
      meta: ['任务尝试 1/3'],
    })
  })

  it('keeps other stage metadata unchanged', () => {
    expect(splitJobStage('正在恢复任务 · 使用备用模型')).toEqual({
      message: '正在恢复任务',
      meta: ['使用备用模型'],
    })
  })
})
