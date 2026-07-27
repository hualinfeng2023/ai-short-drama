import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Surface } from './ui'

describe('Surface', () => {
  it('uses a flat, outline-free surface by default', () => {
    const markup = renderToStaticMarkup(<Surface>内容</Surface>)

    expect(markup).toContain('data-elevation="none"')
    expect(markup).toContain('data-outline="none"')
    expect(markup).toContain('data-tone="default"')
  })

  it('exposes semantic state variants without page-specific classes', () => {
    const markup = renderToStaticMarkup(
      <Surface elevation="floating" outline="warning" tone="warning">
        需要处理
      </Surface>,
    )

    expect(markup).toContain('data-elevation="floating"')
    expect(markup).toContain('data-outline="warning"')
    expect(markup).toContain('data-tone="warning"')
  })
})
