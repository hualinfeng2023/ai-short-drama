import type { DirectorProposal } from '../types'

const QUESTION_HOOK_PATTERN = /[?？]|你会|是否|评论区|你怎么选|你如何选/

export function isQuestionStyleHook(value: string): boolean {
  return QUESTION_HOOK_PATTERN.test(value.trim())
}

export function directionKeyLabel(key: string): string {
  const labels: Record<string, string> = {
    emotion: '情绪驱动',
    emotion_focused: '情绪驱动',
    emotion_centric: '情绪驱动',
    plot: '强情节',
    plot_focused: '强情节',
    plot_centric: '强情节',
    market: '市场钩子',
    market_focused: '市场钩子',
    market_centric: '市场钩子',
    hook_focused: '市场钩子',
    hook_centric: '市场钩子',
    merged: '融合方向',
  }
  return labels[key] ?? key
}

export function directionKeyTurns(direction: DirectorProposal): string[] {
  if (direction.keyTurns?.length) return direction.keyTurns
  return direction.scenes.map(
    (scene) => `${scene.durationSec} 秒 · ${scene.title}：${scene.purpose}`,
  )
}
