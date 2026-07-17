import type {
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
