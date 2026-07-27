import { describe, expect, it } from 'vitest'

import { isIdentityViewPromptValid } from './identityViewPrompt'

describe('isIdentityViewPromptValid', () => {
  it('allows regeneration without a modification prompt', () => {
    expect(isIdentityViewPromptValid('')).toBe(true)
  })

  it('requires at least four characters once an optional prompt is started', () => {
    expect(isIdentityViewPromptValid('改短')).toBe(false)
    expect(isIdentityViewPromptValid('减少脚下阴影')).toBe(true)
  })
})
