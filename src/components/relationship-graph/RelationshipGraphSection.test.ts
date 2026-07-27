import { describe, expect, it } from 'vitest'

import { relationshipGraphFooterCopy } from './RelationshipGraphSection'

describe('relationshipGraphFooterCopy', () => {
  it('only offers approval for draft and reviewable versions', () => {
    expect(relationshipGraphFooterCopy('DRAFT').showApprovalAction).toBe(true)
    expect(relationshipGraphFooterCopy('READY_FOR_REVIEW').showApprovalAction).toBe(true)
  })

  it('shows a completed state after the relationship graph is approved', () => {
    expect(relationshipGraphFooterCopy('APPROVED')).toEqual({
      title: '关系基线已确认。',
      description: '结构化角色视觉档案已准备；角色形象由你在角色工作区单独确认和锁定。',
      showApprovalAction: false,
    })
  })

  it('does not offer approval for historical versions', () => {
    expect(relationshipGraphFooterCopy('SUPERSEDED').showApprovalAction).toBe(false)
  })
})
