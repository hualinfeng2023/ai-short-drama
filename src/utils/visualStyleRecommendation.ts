export type VisualStyleValue =
  | 'realistic_cinematic'
  | 'premium_commercial'
  | 'warm_healing'
  | 'dark_suspense'
  | 'documentary'
  | 'handheld_realism'
  | 'retro_film'
  | 'cyberpunk'
  | 'fantasy_epic'
  | 'anime_2d'
  | 'chinese_ink'
  | 'high_saturation_comic'

const KEYWORD_RULES: Array<[VisualStyleValue, RegExp]> = [
  ['cyberpunk', /赛博|人工智能|AI|机器人|仿生|芯片|虚拟世界|未来都市/i],
  ['fantasy_epic', /奇幻|神秘药|药丸|异能|魔法|神话|修仙|妖怪|末日|重生|穿越|龙族/i],
  ['dark_suspense', /悬疑|凶手|谋杀|失踪|犯罪|阴谋|秘密|复仇|追凶|密室/i],
  ['chinese_ink', /古装|江湖|武侠|山水|诗词|东方美学|朝堂|宫廷/i],
  ['high_saturation_comic', /喜剧|搞笑|荒诞|沙雕|漫画|夸张反转/i],
  ['warm_healing', /治愈|温暖|亲情|友情|成长|团聚|陪伴|和解/i],
  ['documentary', /纪录|纪实|采访|真实事件|社会观察/i],
  ['handheld_realism', /追逐|逃亡|灾难现场|战场|搏斗|街头行动/i],
  ['retro_film', /年代|怀旧|旧时光|九十年代|八十年代|民国/i],
]

const GENRE_DEFAULTS: Record<string, VisualStyleValue> = {
  action_crime: 'dark_suspense',
  comedy: 'high_saturation_comic',
  costume_intrigue: 'chinese_ink',
  costume_romance: 'chinese_ink',
  family_drama: 'warm_healing',
  fantasy: 'fantasy_epic',
  revenge: 'dark_suspense',
  sci_fi: 'cyberpunk',
  suspense: 'dark_suspense',
  urban_romance: 'warm_healing',
  urban_suspense: 'dark_suspense',
  workplace: 'premium_commercial',
  youth_campus: 'warm_healing',
}

export function recommendVisualStyle(idea: string, genre: string): VisualStyleValue {
  const normalizedIdea = idea.trim()
  for (const [style, pattern] of KEYWORD_RULES) {
    if (pattern.test(normalizedIdea)) return style
  }
  return GENRE_DEFAULTS[genre] ?? 'realistic_cinematic'
}
