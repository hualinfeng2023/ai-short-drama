import { describe, expect, it } from 'vitest'
import { buildCandidateGenerationSlots } from './candidateGenerationSlots'

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

  it('removes all placeholders only after every image finishes', () => {
    const candidates = [
      { id: 'candidate-1', ordinal: 1 },
      { id: 'candidate-2', ordinal: 2 },
      { id: 'candidate-3', ordinal: 3 },
    ]

    expect(buildCandidateGenerationSlots(candidates, 3, true)).toEqual(candidates)
  })
})
