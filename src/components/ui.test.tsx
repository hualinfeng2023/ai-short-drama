import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Button, DurationSlider, HintTooltip, NumberStepper, StatusBadge, Surface } from './ui'

describe('Button', () => {
  it('exposes the shared AI-assist visual variant', () => {
    const markup = renderToStaticMarkup(<Button variant="ai">AI 推荐</Button>)

    expect(markup).toContain('button--ai')
    expect(markup).toContain('button--md')
  })
})

describe('HintTooltip', () => {
  it('supports hover and keyboard-accessible help copy', () => {
    const markup = renderToStaticMarkup(
      <HintTooltip label="查看填写说明">只保留主要动作。</HintTooltip>,
    )

    expect(markup).toContain('class="hint-tooltip"')
    expect(markup).toContain('aria-label="查看填写说明"')
    expect(markup).toContain('aria-describedby=')
    expect(markup).toContain('role="tooltip"')
    expect(markup).toContain('只保留主要动作。')
  })
})

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

describe('StatusBadge', () => {
  it('exposes a compact size variant for inline titles and dense layouts', () => {
    const markup = renderToStaticMarkup(<StatusBadge size="sm" status="APPROVED" />)

    expect(markup).toContain('status-badge--success')
    expect(markup).toContain('status-badge--sm')
  })
})

describe('NumberStepper', () => {
  it('exposes explicit decrement, input, increment, and unit labels', () => {
    const markup = renderToStaticMarkup(
      <NumberStepper
        label="镜头时长"
        min={0.5}
        onChange={() => undefined}
        step={0.5}
        unit="秒"
        value={2}
      />,
    )

    expect(markup).toContain('role="group"')
    expect(markup).toContain('aria-label="减少镜头时长"')
    expect(markup).toContain('aria-label="镜头时长"')
    expect(markup).toContain('aria-label="增加镜头时长"')
    expect(markup).toContain('>秒</span>')
  })

  it('disables decrement at the configured minimum', () => {
    const markup = renderToStaticMarkup(
      <NumberStepper
        label="镜头时长"
        min={0.5}
        onChange={() => undefined}
        step={0.5}
        value={0.5}
      />,
    )

    expect(markup).toMatch(/aria-label="减少镜头时长" disabled/)
    expect(markup).not.toMatch(/aria-label="增加镜头时长" disabled/)
  })
})

describe('DurationSlider', () => {
  it('exposes a native range input and the recommended story range', () => {
    const markup = renderToStaticMarkup(
      <DurationSlider
        label="镜头时长"
        max={12}
        min={0.5}
        onChange={() => undefined}
        recommendedMax={7}
        recommendedMin={5}
        recommendedValue={6}
        step={0.5}
        value={3}
      />,
    )

    expect(markup).toContain('type="range"')
    expect(markup).toContain('aria-label="镜头时长"')
    expect(markup).toContain('推荐 5–7 秒')
    expect(markup).toContain('最佳 6 秒')
    expect(markup).toContain('--duration-range-start:')
    expect(markup).toContain('--duration-range-end:')
    expect(markup).toContain('<output class="duration-slider__value"')
    expect(markup.indexOf('duration-slider__control')).toBeLessThan(
      markup.indexOf('duration-slider__value'),
    )
  })

  it('explains how to reveal a range before AI analysis runs', () => {
    const markup = renderToStaticMarkup(
      <DurationSlider
        label="镜头时长"
        onChange={() => undefined}
        value={3}
      />,
    )

    expect(markup).toContain('运行 AI 推荐后标注剧情适配范围')
    expect(markup).not.toContain('duration-slider__recommended-range')
  })
})
