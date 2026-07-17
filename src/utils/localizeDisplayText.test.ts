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
})
