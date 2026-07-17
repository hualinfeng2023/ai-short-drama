import type {
  EmotionalReward,
  NarrativeProtagonist,
  ProductionFormat,
  TargetAudience,
} from '../types'

export const NARRATIVE_PROTAGONIST_OPTIONS: ReadonlyArray<readonly [NarrativeProtagonist, string]> = [
  ['unspecified', '待确认'],
  ['male', '男性'],
  ['female', '女性'],
  ['dual', '双主角'],
  ['ensemble', '群像'],
]

export const TARGET_AUDIENCE_OPTIONS: ReadonlyArray<readonly [TargetAudience, string]> = [
  ['general', '泛人群'],
  ['female_frequency', '女频'],
  ['male_frequency', '男频'],
]

export const EMOTIONAL_REWARD_OPTIONS: ReadonlyArray<readonly [EmotionalReward, string]> = [
  ['romance', '爱情'],
  ['identity', '身份'],
  ['career', '事业'],
  ['revenge', '复仇'],
  ['family', '亲情'],
  ['power', '权力'],
  ['public_mission', '公共使命'],
]

export const PRODUCTION_FORMAT_OPTIONS: ReadonlyArray<readonly [ProductionFormat, string]> = [
  ['live_action', '真人仿真短剧'],
  ['ai_comic', 'AI 漫剧'],
  ['high_concept_fantasy', '高概念奇幻'],
]

export const TOPIC_SLATE_MIX: Record<ProductionFormat, Record<TargetAudience, number>> = {
  live_action: { female_frequency: 50, general: 30, male_frequency: 20 },
  ai_comic: { male_frequency: 50, general: 30, female_frequency: 20 },
  high_concept_fantasy: { male_frequency: 50, general: 30, female_frequency: 20 },
}

export function narrativeTargetingMissing(
  narrativeProtagonist: NarrativeProtagonist,
  emotionalRewards: EmotionalReward[],
): string[] {
  return [
    ...(narrativeProtagonist === 'unspecified' ? ['叙事主角'] : []),
    ...(emotionalRewards.length === 0 ? ['情绪回报'] : []),
  ]
}
