import type { ReactNode } from 'react'

/**
 * 行话/术语解释：虚线下划线标注术语，hover 与键盘 focus 显示解释卡。
 * 纯 CSS 触发，无 JS 状态。嵌在按钮等可聚焦元素内时不要开启 focusable
 * （父元素的 focus-within 会代为触发）；独立使用时设 focusable。
 */
export function GlossaryTip({
  label,
  tip,
  align = 'center',
  focusable = false,
}: {
  label: ReactNode
  tip: ReactNode
  align?: 'center' | 'start' | 'end'
  focusable?: boolean
}) {
  return (
    <span className={`glossary glossary--${align}`} tabIndex={focusable ? 0 : undefined}>
      <span className="glossary__term">{label}</span>
      <span className="glossary__card" role="tooltip">
        {tip}
      </span>
    </span>
  )
}
