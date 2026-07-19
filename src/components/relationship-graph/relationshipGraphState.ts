import type {
  RelationshipBeatRecord,
  RelationshipGraphPayloadRecord,
  RelationshipGraphVersionRecord,
} from '../../api/client'

export type RelationshipSaveStatus = 'saved' | 'dirty' | 'saving' | 'failed' | 'conflict'

export interface RelationshipDraftState {
  graphId: string
  serverLockVersion: number
  serverSnapshot: RelationshipGraphPayloadRecord
  localDraft: RelationshipGraphPayloadRecord
  dirty: boolean
  remoteUpdateAvailable: boolean
  saveStatus: RelationshipSaveStatus
}

function cloneGraph(graph: RelationshipGraphPayloadRecord): RelationshipGraphPayloadRecord {
  return structuredClone(graph)
}

export function createRelationshipDraftState(
  graph: RelationshipGraphVersionRecord,
): RelationshipDraftState {
  return {
    graphId: graph.id,
    serverLockVersion: graph.lockVersion,
    serverSnapshot: cloneGraph(graph.graph),
    localDraft: cloneGraph(graph.graph),
    dirty: false,
    remoteUpdateAvailable: false,
    saveStatus: 'saved',
  }
}

export function syncRelationshipDraftState(
  current: RelationshipDraftState,
  graph: RelationshipGraphVersionRecord,
): RelationshipDraftState {
  if (current.graphId === graph.id && current.dirty) {
    return {
      ...current,
      remoteUpdateAvailable: graph.lockVersion !== current.serverLockVersion,
    }
  }
  return createRelationshipDraftState(graph)
}

export function updateRelationshipDraft(
  current: RelationshipDraftState,
  updater: (graph: RelationshipGraphPayloadRecord) => RelationshipGraphPayloadRecord,
): RelationshipDraftState {
  return {
    ...current,
    localDraft: updater(cloneGraph(current.localDraft)),
    dirty: true,
    saveStatus: 'dirty',
  }
}

export function removeRelationshipBeat(
  graph: RelationshipGraphPayloadRecord,
  relationshipKey: string,
  beatOrdinal: number,
): RelationshipGraphPayloadRecord {
  const next = cloneGraph(graph)
  const target = next.beats.find((beat) => (
    beat.relationshipKey === relationshipKey
    && beat.ordinal === beatOrdinal
  ))
  if (!target) return next

  const groupBeforeRemoval = next.beats
    .filter((beat) => (
      beat.relationshipKey === relationshipKey
      && beat.episodeOrdinal === target.episodeOrdinal
    ))
    .sort((left, right) => left.sequence - right.sequence)
  const removedIndex = groupBeforeRemoval.findIndex((beat) => beat.ordinal === beatOrdinal)
  next.beats = next.beats.filter((beat) => !(
    beat.relationshipKey === relationshipKey
    && beat.ordinal === beatOrdinal
  ))

  const remainingGroup = next.beats
    .filter((beat) => (
      beat.relationshipKey === relationshipKey
      && beat.episodeOrdinal === target.episodeOrdinal
    ))
    .sort((left, right) => left.sequence - right.sequence)
  remainingGroup.forEach((beat: RelationshipBeatRecord, index) => {
    beat.sequence = index + 1
    if (index === 0 && removedIndex === 0) {
      beat.beforeState = structuredClone(target.beforeState)
    } else if (index > 0) {
      beat.beforeState = structuredClone(remainingGroup[index - 1].afterState)
    }
  })
  return next
}

export function removeRelationshipBeatFromDraft(
  current: RelationshipDraftState,
  relationshipKey: string,
  beatOrdinal: number,
): RelationshipDraftState {
  const localDraft = removeRelationshipBeat(
    current.localDraft,
    relationshipKey,
    beatOrdinal,
  )
  const dirty = JSON.stringify(localDraft) !== JSON.stringify(current.serverSnapshot)
  return {
    ...current,
    localDraft,
    dirty,
    saveStatus: dirty ? 'dirty' : 'saved',
  }
}
