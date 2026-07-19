import { describe, expect, it } from 'vitest'
import type {
  RelationshipBeatRecord,
  RelationshipGraphVersionRecord,
  RelationshipStateRecord,
} from '../../api/client'
import {
  createRelationshipDraftState,
  removeRelationshipBeat,
  removeRelationshipBeatFromDraft,
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

function relationshipState(
  surfaceRelationship: string,
  trustLevel: number,
): RelationshipStateRecord {
  return {
    surfaceRelationship,
    trueRelationship: `${surfaceRelationship}的真实关系`,
    trustLevel,
    emotionalTemperature: trustLevel,
    powerBalance: 0,
    conflictIntensity: Math.max(0, 3 - trustLevel),
  }
}

function relationshipBeat(
  sequence: number,
  ordinal: number,
  beforeState: RelationshipStateRecord,
  afterState: RelationshipStateRecord,
): RelationshipBeatRecord {
  return {
    relationshipKey: 'lead-rival',
    episodeOrdinal: 3,
    sequence,
    sceneOrdinal: null,
    triggerType: 'STORY_EVENT',
    triggerRef: null,
    beforeState,
    afterState,
    evidence: `证据 ${sequence}`,
    emotionalConsequence: `后果 ${sequence}`,
    audienceVisibility: 'PARTIAL',
    ordinal,
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

  it('renumbers a group and repairs state continuity after removing a beat', () => {
    const version = graphVersion()
    const opening = relationshipState('互相怀疑', -1)
    const cooperating = relationshipState('被迫合作', 0)
    const trusting = relationshipState('开始信任', 1)
    const allied = relationshipState('正式结盟', 2)
    version.graph.beats = [
      relationshipBeat(1, 1, opening, cooperating),
      relationshipBeat(2, 2, cooperating, trusting),
      relationshipBeat(3, 3, trusting, allied),
    ]

    const graph = removeRelationshipBeat(version.graph, 'lead-rival', 2)

    expect(graph.beats.map((beat) => beat.sequence)).toEqual([1, 2])
    expect(graph.beats[1].beforeState).toEqual(cooperating)
    expect(graph.beats[1].afterState).toEqual(allied)
    expect(version.graph.beats).toHaveLength(3)
  })

  it('uses the deleted first beat opening state for the new first beat', () => {
    const version = graphVersion()
    const opening = relationshipState('互相怀疑', -1)
    const cooperating = relationshipState('被迫合作', 0)
    const trusting = relationshipState('开始信任', 1)
    version.graph.beats = [
      relationshipBeat(1, 7, opening, cooperating),
      relationshipBeat(2, 8, cooperating, trusting),
    ]

    const graph = removeRelationshipBeat(version.graph, 'lead-rival', 7)

    expect(graph.beats[0].sequence).toBe(1)
    expect(graph.beats[0].beforeState).toEqual(opening)
  })

  it('returns to saved state when the only local addition is deleted', () => {
    const initial = createRelationshipDraftState(graphVersion())
    const opening = relationshipState('互相怀疑', -1)
    const cooperating = relationshipState('被迫合作', 0)
    const dirty = updateRelationshipDraft(initial, (graph) => {
      graph.beats.push(relationshipBeat(1, 9, opening, cooperating))
      return graph
    })

    const restored = removeRelationshipBeatFromDraft(dirty, 'lead-rival', 9)

    expect(dirty.dirty).toBe(true)
    expect(restored.localDraft.beats).toHaveLength(0)
    expect(restored.dirty).toBe(false)
    expect(restored.saveStatus).toBe('saved')
  })
})
