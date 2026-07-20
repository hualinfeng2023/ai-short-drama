import { describe, expect, it } from 'vitest'
import {
  buildCandidateGenerationSlots,
  resolveBatchFirstOrdinal,
} from './candidateGenerationSlots'

describe('resolveBatchFirstOrdinal', () => {
  it('starts from 1 when the character has no previous candidates', () => {
    expect(resolveBatchFirstOrdinal([], 'batch-2')).toBe(1)
  })

  it('continues from the previous batch ordinals', () => {
    expect(resolveBatchFirstOrdinal([
      { ordinal: 1, batchId: 'batch-1' },
      { ordinal: 2, batchId: 'batch-1' },
      { ordinal: 3, batchId: 'batch-1' },
    ], 'batch-2')).toBe(4)
  })
})

describe('buildCandidateGenerationSlots', () => {
  it('keeps every placeholder before the first image finishes', () => {
    expect(buildCandidateGenerationSlots([], 3, true)).toEqual([null, null, null])
  })

  it('replaces only the slot whose image has finished', () => {
    const candidate = { id: 'candidate-1', ordinal: 1 }

    expect(buildCandidateGenerationSlots([candidate], 3, true)).toEqual([
      candidate,
      null,
      null,
    ])
  })

  it('preserves direction positions when images finish out of order', () => {
    const secondCandidate = { id: 'candidate-2', ordinal: 2 }

    expect(buildCandidateGenerationSlots([secondCandidate], 3, true)).toEqual([
      null,
      secondCandidate,
      null,
    ])
  })

  it('maps batch ordinals to fixed slots for later generations', () => {
    const secondCandidate = { id: 'candidate-5', ordinal: 5 }

    expect(buildCandidateGenerationSlots([secondCandidate], 3, true, 4)).toEqual([
      null,
      secondCandidate,
      null,
    ])
  })

  it('removes all placeholders only after every image finishes', () => {
    const candidates = [
      { id: 'candidate-1', ordinal: 1 },
      { id: 'candidate-2', ordinal: 2 },
      { id: 'candidate-3', ordinal: 3 },
    ]

    expect(buildCandidateGenerationSlots(candidates, 3, true)).toEqual(candidates)
  })
})
