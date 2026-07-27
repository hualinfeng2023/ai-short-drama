import { describe, expect, it } from 'vitest'

import { shouldShowProjectWorkflow } from './AppShell'

describe('project workflow visibility', () => {
  it('removes the duplicated workflow navigation from the story workbench', () => {
    expect(shouldShowProjectWorkflow('/projects/project-1/story', 'project-1')).toBe(false)
  })

  it('keeps workflow progress on other project routes', () => {
    expect(shouldShowProjectWorkflow('/projects/project-1/characters', 'project-1')).toBe(true)
  })

  it('does not render workflow progress without an active project', () => {
    expect(shouldShowProjectWorkflow('/projects', null)).toBe(false)
    expect(shouldShowProjectWorkflow('/projects/new', 'new')).toBe(false)
  })
})
