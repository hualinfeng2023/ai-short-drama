import { describe, expect, it } from 'vitest'

import { breadcrumb, shouldShowProjectWorkflow } from './AppShell'

describe('breadcrumbs', () => {
  it('shows the project setting page as the current path', () => {
    expect(breadcrumb('/projects/project-1', '测试项目', '/projects/project-1')).toEqual([
      { label: '短剧库', to: '/projects' },
      { label: '测试项目', to: '/projects/project-1' },
      { label: '故事设定' },
    ])
  })
})

describe('project workflow visibility', () => {
  it('keeps workflow progress on the story workbench', () => {
    expect(shouldShowProjectWorkflow('/projects/project-1/story', 'project-1')).toBe(true)
  })

  it('keeps workflow progress on other project routes', () => {
    expect(shouldShowProjectWorkflow('/projects/project-1/characters', 'project-1')).toBe(true)
  })

  it('does not render workflow progress without an active project', () => {
    expect(shouldShowProjectWorkflow('/projects', null)).toBe(false)
    expect(shouldShowProjectWorkflow('/projects/new', 'new')).toBe(false)
  })
})
