import {
  Children,
  Fragment,
  cloneElement,
  createElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ComponentPropsWithoutRef,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, AlertTriangle, Check, CheckCircle2, ChevronDown, CircleDot, Info, LoaderCircle, LockKeyhole, Search, X } from 'lucide-react'

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  children,
  type,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}) {
  return (
    <button className={`button button--${variant} button--${size} ${className}`} type={type ?? 'button'} {...props}>
      {children}
    </button>
  )
}

type SurfaceProps = HTMLAttributes<HTMLElement> & {
  as?: 'article' | 'aside' | 'div' | 'section'
  elevation?: 'none' | 'surface' | 'floating'
  padding?: 'none' | 'sm' | 'md' | 'lg'
  radius?: 'control' | 'surface' | 'panel'
  tone?: 'default' | 'subtle' | 'muted' | 'transparent'
}

export function Surface({
  as = 'section',
  className = '',
  elevation = 'surface',
  padding = 'lg',
  radius = 'surface',
  tone = 'default',
  ...props
}: SurfaceProps) {
  return createElement(as, {
    ...props,
    className: `ds-surface ${className}`.trim(),
    'data-elevation': elevation,
    'data-padding': padding,
    'data-radius': radius,
    'data-tone': tone,
  })
}

type StackProps = HTMLAttributes<HTMLElement> & {
  align?: 'start' | 'center' | 'end' | 'stretch'
  as?: 'div' | 'section'
  direction?: 'column' | 'row'
  gap?: 'none' | 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl'
  justify?: 'start' | 'center' | 'end' | 'between'
  wrap?: boolean
}

export function Stack({
  align = 'stretch',
  as = 'div',
  className = '',
  direction = 'column',
  gap = 'md',
  justify = 'start',
  wrap = false,
  ...props
}: StackProps) {
  return createElement(as, {
    ...props,
    className: `ds-stack ${className}`.trim(),
    'data-align': align,
    'data-direction': direction,
    'data-gap': gap,
    'data-justify': justify,
    'data-wrap': wrap,
  })
}

type FieldControlProps = {
  'aria-describedby'?: string
  'aria-invalid'?: boolean
  id?: string
}

export function FormField({
  children,
  className = '',
  error,
  hint,
  id,
  label,
  optional = false,
}: {
  children: ReactElement<FieldControlProps>
  className?: string
  error?: ReactNode
  hint?: ReactNode
  id?: string
  label: ReactNode
  optional?: boolean
}) {
  const generatedId = useId()
  const controlId = id ?? `field-${generatedId.replace(/:/g, '')}`
  const hintId = hint ? `${controlId}-hint` : undefined
  const errorId = error ? `${controlId}-error` : undefined
  const describedBy = [children.props['aria-describedby'], hintId, errorId].filter(Boolean).join(' ') || undefined
  const control = cloneElement(children, {
    'aria-describedby': describedBy,
    'aria-invalid': Boolean(error) || undefined,
    id: children.props.id ?? controlId,
  })

  return (
    <div className={`ds-form-field ${className}`.trim()} data-invalid={Boolean(error)}>
      <label className="ds-form-field__label" htmlFor={controlId}>
        <span>{label}</span>
        {optional ? <em>选填</em> : null}
      </label>
      <div className="ds-form-field__control">{control}</div>
      {hint ? <p className="ds-form-field__hint" id={hintId}>{hint}</p> : null}
      {error ? <p className="ds-form-field__error" id={errorId} role="alert">{error}</p> : null}
    </div>
  )
}

export function IconButton({
  className = '',
  label,
  size = 'md',
  title,
  type,
  variant = 'secondary',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string
  size?: 'sm' | 'md'
  variant?: 'secondary' | 'ghost' | 'danger'
}) {
  return (
    <button
      aria-label={label}
      className={`icon-button ${className}`.trim()}
      data-size={size}
      data-variant={variant}
      title={title ?? label}
      type={type ?? 'button'}
      {...props}
    />
  )
}

export function Tabs({ className = '', ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`tabs ${className}`.trim()} {...props} />
}

export function TabList({
  className = '',
  onKeyDown,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`tab-list ${className}`.trim()}
      role="tablist"
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (event.defaultPrevented || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)'))
        const currentIndex = tabs.indexOf(document.activeElement as HTMLButtonElement)
        if (currentIndex < 0 || tabs.length === 0) return
        event.preventDefault()
        const nextIndex = event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? tabs.length - 1
            : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
        tabs[nextIndex]?.focus()
      }}
      {...props}
    />
  )
}

export function Tab({
  className = '',
  controls,
  selected,
  type,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  controls?: string
  selected: boolean
}) {
  return (
    <button
      aria-controls={controls}
      aria-selected={selected}
      className={`tab ${className}`.trim()}
      role="tab"
      tabIndex={selected ? 0 : -1}
      type={type ?? 'button'}
      {...props}
    />
  )
}

export function TabPanel({
  active,
  className = '',
  labelledBy,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  active: boolean
  labelledBy?: string
}) {
  return (
    <div
      aria-labelledby={labelledBy}
      className={`tab-panel ${className}`.trim()}
      hidden={!active}
      role="tabpanel"
      tabIndex={0}
      {...props}
    />
  )
}

export function Toolbar({
  className = '',
  label,
  surface = false,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  label: string
  surface?: boolean
}) {
  return (
    <div
      aria-label={label}
      className={`toolbar ${className}`.trim()}
      data-surface={surface}
      role="toolbar"
      {...props}
    />
  )
}

type SelectOptionData = {
  disabled: boolean
  group?: string
  label: string
  value: string
}

type SelectControlProps = ComponentPropsWithoutRef<'select'> & {
  searchable?: boolean
}

function optionText(node: ReactNode): string {
  return Children.toArray(node).map((item) => typeof item === 'string' || typeof item === 'number' ? String(item) : '').join('').trim()
}

function collectSelectOptions(children: ReactNode, group?: string): SelectOptionData[] {
  const options: SelectOptionData[] = []
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return
    if (child.type === 'option') {
      const optionProps = child.props as ComponentPropsWithoutRef<'option'>
      const label = optionText(optionProps.children)
      options.push({
        disabled: Boolean(optionProps.disabled),
        group,
        label,
        value: String(optionProps.value ?? label),
      })
      return
    }
    if (child.type === 'optgroup') {
      const groupProps = child.props as ComponentPropsWithoutRef<'optgroup'>
      options.push(...collectSelectOptions(groupProps.children, groupProps.label))
    }
  })
  return options
}

function normalizedSelectValue(value: ComponentPropsWithoutRef<'select'>['value']): string {
  if (Array.isArray(value)) return String(value[0] ?? '')
  return value === undefined || value === null ? '' : String(value)
}

export function SelectControl({
  children,
  className = '',
  defaultValue,
  disabled = false,
  id,
  onChange,
  searchable,
  title,
  value,
  ...props
}: SelectControlProps) {
  const {
    'aria-describedby': ariaDescribedBy,
    'aria-label': ariaLabel,
    'aria-labelledby': ariaLabelledBy,
    ...nativeProps
  } = props
  const options = useMemo(() => collectSelectOptions(children), [children])
  const firstEnabledValue = options.find((option) => !option.disabled)?.value ?? ''
  const controlled = value !== undefined
  const [internalValue, setInternalValue] = useState(() => normalizedSelectValue(value ?? defaultValue) || firstEnabledValue)
  const selectedValue = controlled ? normalizedSelectValue(value) : internalValue
  const selectedOption = options.find((option) => option.value === selectedValue)
  const selectedLabel = selectedOption?.label ?? selectedValue
  const searchEnabled = searchable ?? options.length >= 8
  const selectRef = useRef<HTMLSelectElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const focusSearchOnOpenRef = useRef(false)
  const typeaheadRef = useRef('')
  const typeaheadTimerRef = useRef<number | null>(null)
  const rawId = useId()
  const componentId = rawId.replace(/:/g, '')
  const triggerId = id ?? `${componentId}-trigger`
  const listboxId = `${componentId}-listbox`
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeValue, setActiveValue] = useState(selectedOption?.disabled ? firstEnabledValue : selectedValue || firstEnabledValue)
  const [menuPosition, setMenuPosition] = useState({
    left: 0,
    maxHeight: 320,
    placement: 'bottom' as 'bottom' | 'top',
    top: 0,
    width: 240,
  })
  const visibleOptions = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
    if (!normalizedQuery) return options
    return options.filter((option) => `${option.label} ${option.group ?? ''}`.toLocaleLowerCase('zh-CN').includes(normalizedQuery))
  }, [options, query])
  const enabledVisibleOptions = visibleOptions.filter((option) => !option.disabled)
  const activeIndex = visibleOptions.findIndex((option) => option.value === activeValue && !option.disabled)
  const activeOptionId = activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined

  useEffect(() => {
    if (!controlled && !options.some((option) => option.value === internalValue)) {
      setInternalValue(firstEnabledValue)
    }
  }, [controlled, firstEnabledValue, internalValue, options])

  useEffect(() => () => {
    if (typeaheadTimerRef.current !== null) window.clearTimeout(typeaheadTimerRef.current)
  }, [])

  const updateMenuPosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const visualViewport = window.visualViewport
    const viewportLeft = visualViewport?.offsetLeft ?? 0
    const viewportTop = visualViewport?.offsetTop ?? 0
    const viewportWidth = visualViewport?.width ?? window.innerWidth
    const viewportHeight = visualViewport?.height ?? window.innerHeight
    const gutter = 8
    const gap = 6
    const groupCount = new Set(visibleOptions.map((option) => option.group).filter(Boolean)).size
    const estimatedHeight = Math.min(420, visibleOptions.length * 38 + groupCount * 25 + (searchEnabled ? 54 : 0) + 14)
    const spaceBelow = viewportTop + viewportHeight - rect.bottom - gutter - gap
    const spaceAbove = rect.top - viewportTop - gutter - gap
    const placement = spaceBelow < Math.min(estimatedHeight, 240) && spaceAbove > spaceBelow ? 'top' : 'bottom'
    const availableHeight = Math.max(132, placement === 'bottom' ? spaceBelow : spaceAbove)
    const maxHeight = Math.min(420, availableHeight)
    const menu = menuRef.current
    const menuViewport = menu?.querySelector<HTMLElement>('.select-menu__viewport')
    const menuHeight = menu?.getBoundingClientRect().height ?? 0
    const menuViewportHeight = menuViewport?.getBoundingClientRect().height ?? 0
    const naturalHeight = menuViewport ? menuViewport.scrollHeight + Math.max(0, menuHeight - menuViewportHeight) : menuHeight
    const renderedHeight = Math.min(naturalHeight > 0 ? naturalHeight : estimatedHeight, maxHeight)
    const width = Math.min(Math.max(rect.width, Math.min(280, viewportWidth - gutter * 2)), viewportWidth - gutter * 2)
    const left = Math.min(
      Math.max(rect.left, viewportLeft + gutter),
      viewportLeft + viewportWidth - gutter - width,
    )
    const top = placement === 'bottom'
      ? rect.bottom + gap
      : Math.max(viewportTop + gutter, rect.top - gap - renderedHeight)
    setMenuPosition({ left, maxHeight, placement, top, width })
  }, [searchEnabled, visibleOptions])

  useLayoutEffect(() => {
    if (!open) return
    const menu = menuRef.current
    if (!menu || typeof menu.showPopover !== 'function') return
    if (!menu.matches(':popover-open')) menu.showPopover()
    return () => {
      if (menu.matches(':popover-open')) menu.hidePopover()
    }
  }, [open])

  useLayoutEffect(() => {
    if (open) updateMenuPosition()
  }, [open, updateMenuPosition])

  useEffect(() => {
    if (!open) return
    const handleOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
      setQuery('')
    }
    window.addEventListener('pointerdown', handleOutsidePointer, true)
    window.addEventListener('resize', updateMenuPosition)
    window.addEventListener('scroll', updateMenuPosition, true)
    window.visualViewport?.addEventListener('resize', updateMenuPosition)
    window.visualViewport?.addEventListener('scroll', updateMenuPosition)
    return () => {
      window.removeEventListener('pointerdown', handleOutsidePointer, true)
      window.removeEventListener('resize', updateMenuPosition)
      window.removeEventListener('scroll', updateMenuPosition, true)
      window.visualViewport?.removeEventListener('resize', updateMenuPosition)
      window.visualViewport?.removeEventListener('scroll', updateMenuPosition)
    }
  }, [open, updateMenuPosition])

  useEffect(() => {
    if (!open) return
    const nextActive = visibleOptions.find((option) => option.value === activeValue && !option.disabled)
      ?? visibleOptions.find((option) => option.value === selectedValue && !option.disabled)
      ?? enabledVisibleOptions[0]
    if (nextActive && nextActive.value !== activeValue) setActiveValue(nextActive.value)
  }, [activeValue, enabledVisibleOptions, open, selectedValue, visibleOptions])

  useEffect(() => {
    if (!open || !searchEnabled) return
    if (!focusSearchOnOpenRef.current) return
    focusSearchOnOpenRef.current = false
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [open, searchEnabled])

  useEffect(() => {
    if (!open || !activeOptionId) return
    document.getElementById(activeOptionId)?.scrollIntoView({ block: 'nearest' })
  }, [activeOptionId, open])

  const openMenu = (preferredValue = selectedValue, focusSearch = false) => {
    if (disabled) return
    focusSearchOnOpenRef.current = focusSearch
    setQuery('')
    setActiveValue(options.find((option) => option.value === preferredValue && !option.disabled)?.value ?? firstEnabledValue)
    setOpen(true)
  }

  const closeMenu = (restoreFocus = false) => {
    if (restoreFocus) triggerRef.current?.focus({ preventScroll: true })
    setOpen(false)
    setQuery('')
  }

  const focusAdjacentControl = (backward: boolean) => {
    const trigger = triggerRef.current
    if (!trigger) return
    const controls = Array.from(document.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !menuRef.current?.contains(element) && element.getClientRects().length > 0)
    const currentIndex = controls.indexOf(trigger)
    const nextControl = controls[currentIndex + (backward ? -1 : 1)]
    nextControl?.focus({ preventScroll: false })
  }

  const commitValue = (nextValue: string) => {
    const option = options.find((item) => item.value === nextValue)
    if (!option || option.disabled) return
    if (!controlled) setInternalValue(nextValue)
    const select = selectRef.current
    if (select) {
      const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set
      valueSetter?.call(select, nextValue)
      select.dispatchEvent(new Event('change', { bubbles: true }))
    }
    closeMenu(true)
  }

  const moveActive = (direction: 1 | -1, boundary?: 'end' | 'start') => {
    if (!enabledVisibleOptions.length) return
    if (boundary === 'start') {
      setActiveValue(enabledVisibleOptions[0].value)
      return
    }
    if (boundary === 'end') {
      setActiveValue(enabledVisibleOptions[enabledVisibleOptions.length - 1].value)
      return
    }
    const currentIndex = enabledVisibleOptions.findIndex((option) => option.value === activeValue)
    const nextIndex = currentIndex < 0
      ? direction === 1 ? 0 : enabledVisibleOptions.length - 1
      : (currentIndex + direction + enabledVisibleOptions.length) % enabledVisibleOptions.length
    setActiveValue(enabledVisibleOptions[nextIndex].value)
  }

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) openMenu(selectedValue, searchEnabled)
      else moveActive(event.key === 'ArrowDown' ? 1 : -1)
      return
    }
    if (event.key === 'Home' && open) {
      event.preventDefault()
      moveActive(1, 'start')
      return
    }
    if (event.key === 'End' && open) {
      event.preventDefault()
      moveActive(1, 'end')
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open && activeValue) commitValue(activeValue)
      else openMenu(selectedValue, searchEnabled)
      return
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      closeMenu()
      return
    }
    if (event.key === 'Tab' && open) {
      closeMenu()
      return
    }
    if (!open && !searchEnabled && event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
      typeaheadRef.current += event.key.toLocaleLowerCase('zh-CN')
      if (typeaheadTimerRef.current !== null) window.clearTimeout(typeaheadTimerRef.current)
      typeaheadTimerRef.current = window.setTimeout(() => { typeaheadRef.current = '' }, 650)
      const match = options.find((option) => !option.disabled && option.label.toLocaleLowerCase('zh-CN').startsWith(typeaheadRef.current))
      if (match) commitValue(match.value)
    }
  }

  const handleSearchKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      moveActive(event.key === 'ArrowDown' ? 1 : -1)
      return
    }
    if (event.key === 'Enter' && activeValue) {
      event.preventDefault()
      commitValue(activeValue)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeMenu(true)
      return
    }
    if (event.key === 'Tab') {
      event.preventDefault()
      const backward = event.shiftKey
      closeMenu()
      window.requestAnimationFrame(() => focusAdjacentControl(backward))
    }
  }

  const dropdown = open && typeof document !== 'undefined' ? createPortal(
    <div
      className="select-menu"
      data-placement={menuPosition.placement}
      popover="manual"
      ref={menuRef}
      style={{
        left: menuPosition.left,
        maxHeight: menuPosition.maxHeight,
        top: menuPosition.top,
        width: menuPosition.width,
      }}
    >
      {searchEnabled ? (
        <div className="select-menu__search">
          <Search aria-hidden="true" size={15} />
          <input
            aria-activedescendant={activeOptionId}
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-label={`搜索${ariaLabel ?? '选项'}`}
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleSearchKeyDown}
            placeholder="搜索选项"
            ref={searchRef}
            type="search"
            value={query}
          />
          {query ? <button aria-label="清空搜索" onClick={() => setQuery('')} type="button"><X size={13} /></button> : null}
        </div>
      ) : null}
      <div
        aria-label={ariaLabel ?? selectedLabel}
        className="select-menu__viewport"
        id={listboxId}
        role="listbox"
      >
        {visibleOptions.length ? visibleOptions.map((option, index) => {
          const showGroup = Boolean(option.group && option.group !== visibleOptions[index - 1]?.group)
          const selected = option.value === selectedValue
          const active = option.value === activeValue && !option.disabled
          return (
            <Fragment key={`${option.group ?? 'option'}-${option.value}-${index}`}>
              {showGroup ? <div className="select-menu__group" role="presentation">{option.group}</div> : null}
              <div
                aria-disabled={option.disabled || undefined}
                aria-selected={selected}
                className={`select-menu__option${active ? ' is-active' : ''}${selected ? ' is-selected' : ''}${option.disabled ? ' is-disabled' : ''}`}
                data-active={active || undefined}
                id={`${listboxId}-option-${index}`}
                onClick={() => commitValue(option.value)}
                onMouseEnter={() => { if (!option.disabled) setActiveValue(option.value) }}
                onPointerDown={(event) => { if (event.pointerType === 'mouse') event.preventDefault() }}
                role="option"
                title={option.label}
              >
                <span aria-hidden="true" className="select-menu__indicator">{selected ? <Check size={14} strokeWidth={2.3} /> : null}</span>
                <span className="select-menu__option-label">{option.label}</span>
                {selected ? <small>当前</small> : null}
              </div>
            </Fragment>
          )
        }) : (
          <div aria-live="polite" className="select-menu__empty" role="status">
            <Search aria-hidden="true" size={16} />
            <span>没有匹配的选项</span>
            <small>换个关键词试试</small>
          </div>
        )}
      </div>
      <div aria-hidden="true" className="select-menu__hint">
        {searchEnabled ? `${visibleOptions.length} 个选项` : '↑↓ 移动 · Enter 选择 · Esc 关闭'}
      </div>
    </div>,
    triggerRef.current?.closest('dialog') ?? document.body,
  ) : null

  return (
    <span className={`select-control${open ? ' is-open' : ''}${disabled ? ' is-disabled' : ''}`}>
      <button
        aria-activedescendant={open && !searchEnabled ? activeOptionId : undefined}
        aria-controls={open ? listboxId : undefined}
        aria-describedby={ariaDescribedBy}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        className={`select-control__trigger ${className}`}
        disabled={disabled}
        id={triggerId}
        onClick={() => open ? closeMenu() : openMenu()}
        onKeyDown={handleTriggerKeyDown}
        ref={triggerRef}
        role="combobox"
        title={title ?? selectedLabel}
        type="button"
      >
        <span className="select-control__value">{selectedLabel}</span>
        <ChevronDown aria-hidden="true" className="select-control__chevron" size={15} strokeWidth={1.9} />
      </button>
      <select
        {...nativeProps}
        aria-hidden="true"
        className="select-control__native"
        disabled={disabled}
        id={`${triggerId}--native`}
        onChange={(event) => {
          if (!controlled) setInternalValue(event.currentTarget.value)
          onChange?.(event)
        }}
        ref={selectRef}
        tabIndex={-1}
        value={selectedValue}
      >
        {children}
      </select>
      {dropdown}
    </span>
  )
}

const statusMeta: Record<string, { label: string; tone: string; icon: ReactNode }> = {
  DRAFT: { label: '草稿', tone: 'neutral', icon: <CircleDot size={12} /> },
  BRIEF_LOCKED: { label: '设定已锁定', tone: 'neutral', icon: <LockKeyhole size={12} /> },
  READY: { label: '待开始', tone: 'neutral', icon: <CircleDot size={12} /> },
  QUEUED: { label: '排队中', tone: 'info', icon: <LoaderCircle size={12} /> },
  GENERATING: { label: '生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  GENERATING_DOSSIER: { label: '身份档案生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  GENERATED: { label: '已生成', tone: 'info', icon: <Check size={12} /> },
  PENDING_REVIEW: { label: '待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  APPROVED: { label: '已批准', tone: 'success', icon: <Check size={12} /> },
  FAILED: { label: '失败', tone: 'danger', icon: <X size={12} /> },
  BLOCKED: { label: '已阻断', tone: 'danger', icon: <AlertTriangle size={12} /> },
  RUNNING: { label: '运行中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  RETRY_WAIT: { label: '等待重试', tone: 'warning', icon: <LoaderCircle size={12} /> },
  CANCEL_REQUESTED: { label: '取消中', tone: 'warning', icon: <LoaderCircle size={12} /> },
  SUCCEEDED: { label: '已完成', tone: 'success', icon: <Check size={12} /> },
  CANCELLED: { label: '已取消', tone: 'neutral', icon: <X size={12} /> },
  PENDING: { label: '等待中', tone: 'neutral', icon: <CircleDot size={12} /> },
  PRODUCING: { label: '制作中', tone: 'info', icon: <LoaderCircle size={12} /> },
  PROPOSAL_RUNNING: { label: '方案生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  PROPOSAL_READY: { label: '方案待批准', tone: 'warning', icon: <AlertTriangle size={12} /> },
  STORY_STRUCTURE_RUNNING: { label: '故事结构生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  RELATIONSHIP_READY: { label: '角色关系待确认', tone: 'warning', icon: <AlertTriangle size={12} /> },
  CHARACTER_VISUAL_READY: { label: '角色形象待锁定', tone: 'warning', icon: <AlertTriangle size={12} /> },
  SCRIPT_PACKAGE_RUNNING: { label: '分集大纲与剧本生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  STORY_APPROVED: { label: '故事已批准', tone: 'success', icon: <Check size={12} /> },
  CHARACTER_LOCKED: { label: '角色已锁定', tone: 'success', icon: <Check size={12} /> },
  STORY_PACKAGE_RUNNING: { label: '故事资料生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  SCRIPT_READY: { label: '剧本待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PREPRODUCTION_READY: { label: '前期资产待批准', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PREPRODUCTION_APPROVED: { label: '前期资产已批准', tone: 'success', icon: <Check size={12} /> },
  STORYBOARD_READY: { label: '分镜待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  STORYBOARD_APPROVED: { label: '分镜已批准', tone: 'success', icon: <Check size={12} /> },
  EXPORTING: { label: '导出中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  ARCHIVED: { label: '已归档', tone: 'neutral', icon: <CircleDot size={12} /> },
  ACTIVE: { label: '进行中', tone: 'info', icon: <LoaderCircle size={12} /> },
  CANDIDATES_READY: { label: '候选已就绪', tone: 'success', icon: <Check size={12} /> },
  NOT_GENERATED: { label: '尚未生成', tone: 'neutral', icon: <CircleDot size={12} /> },
  PENDING_SELECTION: { label: '待选择', tone: 'warning', icon: <AlertTriangle size={12} /> },
  TEXT_CHANGED: { label: '文字设定已变化', tone: 'warning', icon: <AlertTriangle size={12} /> },
  RE_REVIEW_REQUIRED: { label: '需要重新审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  GENERATION_FAILED: { label: '生成失败', tone: 'danger', icon: <X size={12} /> },
  DEGRADED: { label: '已降级', tone: 'warning', icon: <AlertTriangle size={12} /> },
  GENERATING_CANDIDATES: { label: '候选生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  IN_PROGRESS: { label: '进行中', tone: 'info', icon: <LoaderCircle size={12} /> },
  LOCKED: { label: '已锁定', tone: 'success', icon: <Check size={12} /> },
  PARSING: { label: '解析中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  PASSED_WITH_DEGRADATION: { label: '降级后通过', tone: 'warning', icon: <AlertTriangle size={12} /> },
  QC_PASSED: { label: '质量检查通过', tone: 'success', icon: <Check size={12} /> },
  READY_FOR_G5: { label: '可进入第 5 阶段', tone: 'success', icon: <Check size={12} /> },
  READY_FOR_REVIEW: { label: '待审核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  RESTRICTED_DEMO: { label: '仅限演示', tone: 'warning', icon: <AlertTriangle size={12} /> },
  SELECTED: { label: '已选择', tone: 'info', icon: <Check size={12} /> },
  SUPERSEDED: { label: '历史版本', tone: 'neutral', icon: <CircleDot size={12} /> },
  STORYBOARDING: { label: '分镜生成中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  SYNTHETIC_ALLOWED: { label: '允许合成', tone: 'success', icon: <Check size={12} /> },
  SYNTHETIC_OWNED: { label: '自有合成素材', tone: 'success', icon: <Check size={12} /> },
  UPLOADING: { label: '上传中', tone: 'info', icon: <LoaderCircle size={12} className="spin" /> },
  WAITING_FOR_GATE: { label: '等待审批', tone: 'warning', icon: <AlertTriangle size={12} /> },
  PASSED: { label: '角色一致', tone: 'success', icon: <Check size={12} /> },
  REVIEW_REQUIRED: { label: '角色需要复核', tone: 'warning', icon: <AlertTriangle size={12} /> },
  NOT_APPLICABLE: { label: '没有绑定角色', tone: 'neutral', icon: <CircleDot size={12} /> },
  PREVIEW_READY: { label: '小样就绪', tone: 'success', icon: <Check size={12} /> },
  EXPORTED: { label: '已导出', tone: 'success', icon: <Check size={12} /> },
}

export function getStatusLabel(status: string): string {
  return statusMeta[status]?.label ?? status
}

export function StatusBadge({
  status,
  label,
  description,
}: {
  status: string
  label?: string
  description?: string
}) {
  const meta = statusMeta[status] ?? statusMeta.DRAFT
  const displayLabel = label ?? meta.label
  return (
    <span
      aria-label={description}
      className={`status-badge status-badge--${meta.tone}`}
      data-tone={meta.tone}
      role={description ? 'status' : undefined}
      title={description ?? displayLabel}
    >
      {meta.icon}
      {displayLabel}
    </span>
  )
}

export function Toast({
  message,
  onDismiss,
  onUndo,
  tone = 'success',
  className = '',
}: {
  message: string
  onDismiss: () => void
  onUndo?: () => void
  tone?: 'success' | 'error' | 'info'
  className?: string
}) {
  const title = tone === 'success' ? '操作成功' : tone === 'error' ? '操作失败' : '提示'
  const icon = tone === 'success' ? <CheckCircle2 size={18} /> : tone === 'error' ? <AlertCircle size={18} /> : <Info size={18} />

  return (
    <div
      aria-atomic="true"
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
      className={`toast toast--${tone} ${className}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <span className="toast__icon" aria-hidden="true">{icon}</span>
      <div className="toast__content">
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {onUndo ? (
        <button className="toast__undo" onClick={onUndo} type="button">撤销</button>
      ) : null}
      <IconButton className="toast__close" label="关闭通知" onClick={onDismiss} size="sm" variant="ghost">
        <X size={15} />
      </IconButton>
    </div>
  )
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const safeValue = Math.max(0, Math.min(100, value))
  return (
    <div className="progress-wrap">
      {label ? (
        <div className="progress-label">
          <span>{label}</span>
          <strong>{safeValue}%</strong>
        </div>
      ) : null}
      <div
        className="progress-track"
        aria-label={label ?? '进度'}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={safeValue}
        role="progressbar"
      >
        <span style={{ width: `${safeValue}%` }} />
      </div>
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  titleMeta,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  titleMeta?: ReactNode
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <Stack gap="xs">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <div className="page-header__title-row">
          <h1>{title}</h1>
          {titleMeta}
        </div>
        {description ? <p className="page-header__description">{description}</p> : null}
      </Stack>
      {actions ? <Toolbar className="page-header__actions" label="页面操作">{actions}</Toolbar> : null}
    </header>
  )
}

export function Modal({
  open,
  title,
  description,
  className = '',
  onClose,
  children,
  footer,
}: {
  open: boolean
  title: string
  description?: string
  className?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const [closing, setClosing] = useState(false)
  const closeTimer = useRef<number | null>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open) {
      if (closeTimer.current !== null) {
        window.clearTimeout(closeTimer.current)
        closeTimer.current = null
      }
      setClosing(false)
      if (!dialog.open) dialog.showModal()
    } else if (dialog.open) {
      dialog.close()
    }
  }, [open])

  useEffect(() => () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
  }, [])

  const requestClose = () => {
    if (closing) return
    setClosing(true)
    const duration = Number.parseFloat(
      window.getComputedStyle(document.documentElement).getPropertyValue('--motion-duration-fast'),
    )
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null
      setClosing(false)
      onClose()
    }, Number.isFinite(duration) ? duration : 0)
  }

  return (
    <dialog
      aria-describedby={description ? descriptionId : undefined}
      aria-labelledby={titleId}
      className={`modal ${className} ${closing ? 'modal--closing' : ''}`}
      ref={ref}
      onCancel={(event) => {
        event.preventDefault()
        requestClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) requestClose()
      }}
      onClose={onClose}
    >
      <div className="modal__header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description ? <p id={descriptionId}>{description}</p> : null}
        </div>
        <IconButton label="关闭" onClick={requestClose} size="sm" variant="ghost">
          <X size={18} />
        </IconButton>
      </div>
      <div className="modal__body">{children}</div>
      {footer ? <div className="modal__footer">{footer}</div> : null}
    </dialog>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <Surface as="div" className="empty-state" padding="lg" radius="panel" role="status">
      <span className="empty-state__mark" aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </Surface>
  )
}
