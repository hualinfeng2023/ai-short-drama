import { describe, expect, it } from 'vitest'
import type { RelationshipGraphVersionRecord } from '../../api/client'
import {
  createRelationshipDraftState,
  syncRelationshipDraftState,
  updateRelationshipDraft,
} from './relationshipGraphState'

function graphVersion(lockVersion = 1, surfaceRelationship = '互相怀疑'): RelationshipGraphVersionRecord {
  return {
    id: 'graph-id',
    projectId: 'project-id',
    storyBibleVersionId: 'bible-id',
    version: 1,
    parentVersionId: null,
    status: 'DRAFT',
    schemaVersion: 'relationship-graph-v1',
    configVersion: 'relationship-graph-v1',
    provider: 'manual',
    model: 'manual',
    contentHash: `hash-${lockVersion}`,
    lockVersion,
    projectLockVersion: lockVersion + 1,
    approvedAt: null,
    approvedBy: null,
    createdAt: '2026-07-16T00:00:00Z',
    graph: {
      schemaVersion: 'relationship-graph-v1',
      edges: [{
        relationshipKey: 'lead-rival',
        sourceCharacterKey: 'lead',
        targetCharacterKey: 'rival',
        directionality: 'BIDIRECTIONAL',
        relationshipTypes: ['RIVAL'],
        surfaceRelationship,
        trueRelationship: '被旧案绑在一起的知情者',
        sourceView: { perceivedRelationship: '对手', belief: '对方在隐瞒' },
        targetView: { perceivedRelationship: '嫌疑人', belief: '对方在操控证据' },
        trustLevel: -2,
        emotionalTemperature: -1,
        powerBalance: 0,
        conflictIntensity: 3,
        storyFunction: '制造误判与认证后的关系重排',
        secret: null,
        isCore: true,
        locked: false,
        ordinal: 1,
      }],
      beats: [],
      coreRelationshipKeys: ['lead-rival'],
      generationNotes: [],
    },
    validationIssues: [],
    editability: {
      semanticEditable: true,
      layoutEditable: true,
      canSubmit: true,
      canApprove: true,
      canCreateRevision: false,
      activeJob: false,
      reasonCode: null,
      reasonMessage: null,
      requiresImpactConfirmation: false,
    },
  }
}

describe('relationship graph local draft protection', () => {
  it('keeps unsaved local edits when a polling snapshot changes', () => {
    const initial = createRelationshipDraftState(graphVersion())
    const dirty = updateRelationshipDraft(initial, (graph) => {
      graph.edges[0].surfaceRelationship = '被迫合作'
      return graph
    })

    const synced = syncRelationshipDraftState(dirty, graphVersion(2, '服务端的新关系'))

    expect(synced.localDraft.edges[0].surfaceRelationship).toBe('被迫合作')
    expect(synced.dirty).toBe(true)
    expect(synced.remoteUpdateAvailable).toBe(true)
  })

  it('refreshes a clean draft from the newest server snapshot', () => {
    const synced = syncRelationshipDraftState(
      createRelationshipDraftState(graphVersion()),
      graphVersion(2, '服务端的新关系'),
    )

    expect(synced.localDraft.edges[0].surfaceRelationship).toBe('服务端的新关系')
    expect(synced.serverLockVersion).toBe(2)
    expect(synced.dirty).toBe(false)
  })
})
