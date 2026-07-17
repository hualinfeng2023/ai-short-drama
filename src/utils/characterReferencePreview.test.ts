import { describe, expect, it } from 'vitest'
import type { Shot, ShotCharacterBinding } from '../types'
import { resolveCharacterReferencePreview } from './characterReferencePreview'

const binding: ShotCharacterBinding = {
  id: 'character-1',
  name: '林悦',
  role: 'PROTAGONIST',
  visualBrief: '稳定角色参考',
  lookVersion: 'Look V1',
  lockedCandidateId: 'candidate-1',
  referenceAssetId: 'asset-placeholder',
  referenceAssetUrl: '/assets/striped-placeholder.png',
}

function shot(patch: Partial<Shot>): Shot {
  return {
    id: 'shot-1',
    sceneId: 'scene-1',
    code: 'S01',
    ordinal: 1,
    title: '镜头',
    description: '',
    dialogue: '',
    durationSec: 8,
    status: 'APPROVED',
    shotSize: 'MS',
    cameraMovement: 'STATIC',
    currentTake: 1,
    continuity: 'CLEAR',
    location: '室内',
    timeOfDay: '夜',
    characterBindings: [binding],
    ...patch,
  }
}

describe('resolveCharacterReferencePreview', () => {
  it('prefers the closest approved project frame for the bound character', () => {
    const result = resolveCharacterReferencePreview([
      shot({ id: 'wide', shotSize: 'WS', currentImageUrl: '/assets/wide.jpg' }),
      shot({ id: 'close', shotSize: 'CU', currentImageUrl: '/assets/close.jpg' }),
    ], binding)

    expect(result).toBe('/assets/close.jpg')
  })

  it('keeps the locked asset as a fallback when no project frame exists', () => {
    expect(resolveCharacterReferencePreview([], binding)).toBe(binding.referenceAssetUrl)
  })
})
