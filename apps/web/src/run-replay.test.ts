import { describe, expect, it } from 'vitest'
import {
  applyRunEvent,
  createEmptyRunReplayState,
  nextReplaySeq,
  previousReplaySeq,
  replayRunEvents,
} from './run-replay'
import type { RunEvent } from './types'

function event(
  seq: number,
  type: string,
  data: Record<string, unknown> = {},
  nodeId: string | null = null,
  edgeId: string | null = null,
): RunEvent {
  return {
    seq,
    run_id: 'run-1',
    ts: `2026-11-19T10:00:${String(seq).padStart(2, '0')}Z`,
    type,
    node_id: nodeId,
    edge_id: edgeId,
    data,
  }
}

describe('run replay', () => {
  it('deduplicates events by sequence and applies them in order', () => {
    const events = [
      event(3, 'node_exit', { status: 'succeeded' }, 'writer'),
      event(1, 'run_started'),
      event(2, 'node_enter', {}, 'writer'),
      event(2, 'node_enter', {}, 'duplicate'),
    ]

    const state = replayRunEvents(events)

    expect(state.events.map((item) => item.seq)).toEqual([1, 2, 3])
    expect(state.nodeStatuses.writer).toBe('succeeded')
    expect(state.nodeStatuses.duplicate).toBeUndefined()
    expect(state.status).toBe('running')
  })

  it('accumulates tokens, traversals and artifacts', () => {
    const state = replayRunEvents([
      event(1, 'run_started'),
      event(2, 'token_usage', { total_tokens: 11 }, 'writer'),
      event(3, 'edge_traversed', {}, null, 'draft-to-review'),
      event(4, 'edge_traversed', {}, null, 'draft-to-review'),
      event(
        5,
        'artifact_written',
        {
          port: 'draft',
          path: 'writer/draft.txt',
          type: 'TEXT',
          step: 1,
          batch_id: 'batch-start',
          bytes: 42,
        },
        'writer',
      ),
      event(6, 'token_usage', { total_tokens: 7 }, 'reviewer'),
    ])

    expect(state.tokenUsage).toBe(18)
    expect(state.tokenUsageByAgent).toEqual({ writer: 11, reviewer: 7 })
    expect(state.edgeTraversals['draft-to-review']).toBe(2)
    expect(state.artifacts).toEqual([
      {
        seq: 5,
        agentId: 'writer',
        port: 'draft',
        path: 'writer/draft.txt',
        type: 'TEXT',
        step: 1,
        batchId: 'batch-start',
        bytes: 42,
      },
    ])
  })

  it('tracks human gates and terminal failures', () => {
    const state = replayRunEvents([
      event(1, 'run_started'),
      event(
        2,
        'human_gate_waiting',
        {
          gate_id: 'gate-review-1',
          prompt: 'Approve publication',
          timeout_seconds: 60,
        },
        null,
        'review-gate',
      ),
      event(
        3,
        'human_gate_resolved',
        {
          gate_id: 'gate-review-1',
          decision: 'reject',
          comment: 'Needs work',
        },
        null,
        'review-gate',
      ),
      event(4, 'error', { message: 'Gate rejected' }, 'reviewer'),
      event(5, 'run_finished', {
        status: 'failed',
        error: 'Gate rejected',
      }),
    ])

    expect(state.gates['gate-review-1']).toMatchObject({
      edgeId: 'review-gate',
      status: 'resolved',
      decision: 'reject',
      comment: 'Needs work',
    })
    expect(state.nodeStatuses.reviewer).toBe('failed')
    expect(state.status).toBe('failed')
    expect(state.error).toBe('Gate rejected')
  })

  it('replays only up to the requested sequence', () => {
    const events = [
      event(1, 'run_started'),
      event(2, 'node_enter', {}, 'writer'),
      event(3, 'node_exit', { status: 'succeeded' }, 'writer'),
    ]

    const state = replayRunEvents(events, 2)

    expect(state.events).toHaveLength(2)
    expect(state.nodeStatuses.writer).toBe('running')
    expect(state.lastSeq).toBe(2)
  })

  it('finds adjacent event sequences for single-step replay', () => {
    const events = [
      event(1, 'run_started'),
      event(3, 'token_usage'),
      event(7, 'run_finished', { status: 'succeeded' }),
    ]

    expect(nextReplaySeq(events, 0)).toBe(1)
    expect(nextReplaySeq(events, 3)).toBe(7)
    expect(nextReplaySeq(events, 7)).toBeNull()
    expect(previousReplaySeq(events, 7)).toBe(3)
    expect(previousReplaySeq(events, 1)).toBe(0)
  })

  it('does not mutate a previous replay snapshot', () => {
    const initial = applyRunEvent(
      createEmptyRunReplayState(),
      event(1, 'node_enter', {}, 'writer'),
    )

    applyRunEvent(initial, event(2, 'node_exit', {}, 'writer'))

    expect(initial.nodeStatuses.writer).toBe('running')
    expect(initial.events).toHaveLength(1)
  })
})
