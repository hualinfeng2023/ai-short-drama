import { describe, expect, it } from 'vitest'
import type { CanvasProjection } from '../api/client'
import {
  canvasNodeId,
  createCanvasViewState,
  parseCanvasViewState,
  projectCanvasGraph,
} from './filmCanvasProjection'

const projection: CanvasProjection = {
  schemaVersion: 'film-canvas-projection-v1',
  projectId: 'project-1',
  projectLockVersion: 7,
  sourceProjection: 'film-ir-projection-v1',
  nodes: [
    {
      ref: { type: 'Project', id: 'project-1', versionId: 'project-v7' },
      canonicalKind: 'CANONICAL',
      canonicalStatus: 'ACTIVE',
      approvalStatus: 'APPROVED',
      label: '雨停以后',
      groupKey: 'project',
      detailRoute: '/projects/project-1',
      readOnly: true,
    },
    {
      ref: { type: 'Scene', id: 'scene-1', versionId: 'scene-v2' },
      canonicalKind: 'CANONICAL',
      canonicalStatus: 'ACTIVE',
      approvalStatus: 'DRAFT',
      label: '便利店停电',
      groupKey: 'episode:1',
      detailRoute: '/projects/project-1/episodes/episode-1',
      readOnly: true,
    },
  ],
  edges: [{
    source: { type: 'Project', id: 'project-1', versionId: 'project-v7' },
    target: { type: 'Scene', id: 'scene-1', versionId: 'scene-v2' },
    relation: 'CONTAINS',
    inferred: false,
  }],
  viewStateContract: {
    schemaVersion: 'film-canvas-view-state-v1',
    persistence: 'CLIENT_LOCAL',
    allowedFields: ['x', 'y', 'width', 'height', 'selected', 'viewport.x', 'viewport.y', 'viewport.zoom'],
    forbiddenBusinessFields: ['approval_status', 'version_id', 'domain_payload'],
  },
}

describe('Film Canvas projection adapter', () => {
  it('projects canonical references without copying business payloads into nodes', () => {
    const graph = projectCanvasGraph(projection, null)

    expect(graph.nodes).toHaveLength(2)
    expect(graph.edges).toHaveLength(1)
    expect(graph.nodes[0]?.data.projection).toBe(projection.nodes[0])
    expect(graph.edges[0]).toMatchObject({
      source: 'Project:project-1',
      target: 'Scene:scene-1',
      label: 'CONTAINS',
      data: { inferred: false },
    })
  })

  it('keeps stale layout by stable id while taking canonical data from the latest projection', () => {
    const saved = parseCanvasViewState(JSON.stringify({
      schema_version: 'film-canvas-view-state-v1',
      project_id: 'project-1',
      projection_lock_version: 6,
      viewport: { x: 12, y: 24, zoom: 0.8 },
      nodes: {
        'Scene:scene-1': {
          x: 480,
          y: 320,
          width: 232,
          height: 108,
          group: 'episode:1',
          z_index: 3,
          collapsed: false,
          approval_status: 'APPROVED',
          domain_payload: { dialogue: '不应进入视图状态' },
        },
      },
      selected: [{ type: 'Scene', id: 'scene-1', version_id: 'scene-v1' }],
    }), 'project-1')

    const graph = projectCanvasGraph(projection, saved)
    const scene = graph.nodes.find((node) => node.id === 'Scene:scene-1')

    expect(scene?.position).toEqual({ x: 480, y: 320 })
    expect(scene?.data.projection.ref.versionId).toBe('scene-v2')
    expect(saved?.nodes['Scene:scene-1']).not.toHaveProperty('approval_status')
    expect(saved?.nodes['Scene:scene-1']).not.toHaveProperty('domain_payload')
  })

  it('serializes only the server-declared view-state fields', () => {
    const graph = projectCanvasGraph(projection, null)
    const sceneRef = projection.nodes[1]!.ref
    const viewState = createCanvasViewState(
      projection,
      graph.nodes.map((node) => (
        node.id === canvasNodeId(sceneRef)
          ? { ...node, position: { x: 600, y: 420 } }
          : node
      )),
      { x: -100, y: 30, zoom: 1.2 },
      [sceneRef],
    )
    const serialized = JSON.stringify(viewState)

    expect(viewState.projection_lock_version).toBe(7)
    expect(viewState.nodes['Scene:scene-1']?.x).toBe(600)
    expect(viewState.selected).toEqual([{
      type: sceneRef.type,
      id: sceneRef.id,
      version_id: sceneRef.versionId,
    }])
    expect(serialized).not.toContain('canonicalStatus')
    expect(serialized).not.toContain('approvalStatus')
    expect(serialized).not.toContain('detailRoute')
    expect(serialized).toContain('version_id')
    expect(serialized).not.toContain('versionId')
  })

  it('rejects view state for another project', () => {
    expect(parseCanvasViewState(JSON.stringify({
      schema_version: 'film-canvas-view-state-v1',
      project_id: 'project-2',
      projection_lock_version: 1,
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: {},
      selected: [],
    }), 'project-1')).toBeNull()
  })
})
