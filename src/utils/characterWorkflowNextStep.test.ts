import { describe, expect, it } from 'vitest'

import { resolveCharacterWorkflowNextStep } from './characterWorkflowNextStep'

describe('resolveCharacterWorkflowNextStep', () => {
  it('guides the user to the next unlocked character', () => {
    expect(resolveCharacterWorkflowNextStep({
      projectStatus: 'CHARACTER_VISUAL_READY',
      lockedCount: 1,
      totalCount: 4,
      nextCharacterName: '丈夫',
    })).toMatchObject({
      title: '还需确认 3 位角色形象',
      actionLabel: '前往确认丈夫',
      destination: 'CHARACTER',
    })
  })

  it('links to generation progress after all identities are locked', () => {
    expect(resolveCharacterWorkflowNextStep({
      projectStatus: 'SCRIPT_PACKAGE_RUNNING',
      lockedCount: 4,
      totalCount: 4,
    }).destination).toBe('TASKS')
  })

  it('links to story review when the script package is ready', () => {
    expect(resolveCharacterWorkflowNextStep({
      projectStatus: 'SCRIPT_READY',
      lockedCount: 4,
      totalCount: 4,
    })).toMatchObject({
      title: '下一步：审核故事与剧本',
      actionLabel: '前往剧本审核',
      destination: 'STORY',
    })
  })
})
