export type GenreValue =
  | 'urban_drama' | 'urban_romance' | 'urban_suspense' | 'family_drama'
  | 'revenge' | 'costume_romance' | 'costume_intrigue' | 'youth_campus' | 'suspense'
  | 'workplace' | 'comedy' | 'fantasy' | 'action_crime' | 'sci_fi'

const GENRE_RULES: Array<[GenreValue, RegExp, number]> = [
  ['sci_fi', /科幻|星际|太空|机器人|仿生|芯片|人工智能|AI|时间机器|未来世界/i, 5],
  ['fantasy', /奇幻|魔法|异能|神秘药|药丸|修仙|妖怪|神话|重生|穿越|末日/i, 5],
  ['costume_intrigue', /古装|朝堂|皇帝|皇后|公主|王爷|将军|军营|粮仓|宫廷|权谋|谋反|九州|侯府|王朝/i, 5],
  ['costume_romance', /古代爱情|古言|赐婚|和亲|王妃|世子|花轿/i, 5],
  ['urban_suspense', /悬疑|凶手|谋杀|失踪|追凶|密室|连环案|阴谋|秘密|侦探/i, 5],
  ['action_crime', /犯罪|黑帮|警察|缉毒|绑架|追捕|枪战|卧底|劫案/i, 5],
  ['revenge', /复仇|逆袭|背叛|陷害|被害|重来一次|夺回|清算|渣男|打脸/i, 4],
  ['family_drama', /家庭|婆媳|婚姻|离婚|出轨|亲子|姐妹|兄弟|遗产|养老/i, 3],
  ['workplace', /职场|公司|老板|同事|创业|升职|裁员|商业|商战/i, 4],
  ['youth_campus', /校园|大学|高中|同学|青春|社团|高考|毕业/i, 4],
  ['urban_romance', /甜宠|恋爱|爱情|相亲|婚礼|男友|女友|总裁/i, 3],
  ['comedy', /喜剧|搞笑|荒诞|沙雕|乌龙|爆笑/i, 4],
  ['urban_drama', /都市|城市|租房|工作|生活|情感|成长/i, 2],
]

export function recommendGenre(idea: string): GenreValue {
  const normalized = idea.trim()
  let best: { value: GenreValue; score: number; index: number } = {
    value: 'urban_drama', score: 0, index: Number.POSITIVE_INFINITY,
  }
  GENRE_RULES.forEach(([value, pattern, weight], index) => {
    const flags = pattern.flags.includes('g') ? pattern.flags : `${pattern.flags}g`
    const score = (normalized.match(new RegExp(pattern.source, flags))?.length ?? 0) * weight
    if (score > best.score || (score === best.score && score > 0 && index < best.index)) {
      best = { value, score, index }
    }
  })
  return best.value
}
