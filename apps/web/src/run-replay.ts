import type { NodeRunStatus } from './AgentNode'
import type { RunEvent, RunStatus } from './types'

export interface ArtifactReplayRecord {
  seq: number
  agentId: string | null
  port: string
  path: string
  type: string
  step: number | null
  batchId: string | null
  bytes: number | null
}

export interface GateReplayRecord {
  gateId: string
  edgeId: string | null
  prompt: string
  timeoutSeconds: number | null
  requestedAt: string | null
  status: 'waiting' | 'resolved'
  decision: string | null
  comment: string | null
  timedOut: boolean
}

export interface RunReplayState {
  events: RunEvent[]
  nodeStatuses: Record<string, NodeRunStatus>
  edgeTraversals: Record<string, number>
  tokenUsage: number
  tokenUsageByAgent: Record<string, number>
  artifacts: ArtifactReplayRecord[]
  gates: Record<string, GateReplayRecord>
  status: RunStatus
  terminalStatus: RunStatus | null
  error: string | null
  lastSeq: number
}

const TERMINAL_STATUSES = new Set<RunStatus>([
  'succeeded',
  'failed',
  'cancelled',
])

function dataObject(event: RunEvent): Record<string, unknown> {
  return event.data && typeof event.data === 'object'
    ? event.data
    : {}
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function nodeExitStatus(value: unknown): NodeRunStatus {
  if (
    value === 'failed' ||
    value === 'cancelled' ||
    value === 'skipped'
  ) {
    return value
  }
  return 'succeeded'
}

function runStatus(value: unknown): RunStatus | null {
  if (
    value === 'queued' ||
    value === 'running' ||
    value === 'waiting' ||
    value === 'succeeded' ||
    value === 'failed' ||
    value === 'cancelled'
  ) {
    return value
  }
  return null
}

function statusFromGates(state: RunReplayState): RunStatus {
  if (state.terminalStatus) return state.terminalStatus
  const gates = Object.values(state.gates)
  if (gates.some((gate) => gate.status === 'waiting')) return 'waiting'
  return state.events.length ? 'running' : 'queued'
}

export function createEmptyRunReplayState(): RunReplayState {
  return {
    events: [],
    nodeStatuses: {},
    edgeTraversals: {},
    tokenUsage: 0,
    tokenUsageByAgent: {},
    artifacts: [],
    gates: {},
    status: 'queued',
    terminalStatus: null,
    error: null,
    lastSeq: 0,
  }
}

export function dedupeAndSortRunEvents(events: RunEvent[]): RunEvent[] {
  const bySequence = new Map<number, RunEvent>()
  for (const event of events) {
    if (!bySequence.has(event.seq)) bySequence.set(event.seq, event)
  }
  return [...bySequence.values()].sort((left, right) => left.seq - right.seq)
}

export function applyRunEvent(
  current: RunReplayState,
  event: RunEvent,
): RunReplayState {
  const data = dataObject(event)
  let nodeStatuses = current.nodeStatuses
  let edgeTraversals = current.edgeTraversals
  let tokenUsage = current.tokenUsage
  let tokenUsageByAgent = current.tokenUsageByAgent
  let artifacts = current.artifacts
  let gates = current.gates
  let terminalStatus = current.terminalStatus
  let error = current.error
  const state: RunReplayState = {
    ...current,
    events: [...current.events, event],
    lastSeq: Math.max(current.lastSeq, event.seq),
  }
  state.status = current.terminalStatus ?? 'running'

  if (event.type === 'run_started') {
    state.status = 'running'
  } else if (event.type === 'node_enter' && event.node_id) {
    nodeStatuses = { ...nodeStatuses, [event.node_id]: 'running' }
  } else if (event.type === 'node_exit' && event.node_id) {
    nodeStatuses = {
      ...nodeStatuses,
      [event.node_id]: nodeExitStatus(data.status),
    }
  } else if (event.type === 'edge_traversed' && event.edge_id) {
    edgeTraversals = {
      ...edgeTraversals,
      [event.edge_id]: (edgeTraversals[event.edge_id] ?? 0) + 1,
    }
  } else if (event.type === 'token_usage') {
    const total = numberValue(data.total_tokens) ?? 0
    tokenUsage += total
    if (event.node_id) {
      tokenUsageByAgent = {
        ...tokenUsageByAgent,
        [event.node_id]: (tokenUsageByAgent[event.node_id] ?? 0) + total,
      }
    }
  } else if (event.type === 'artifact_written') {
    const path = stringValue(data.path)
    if (path) {
      artifacts = [
        ...artifacts,
        {
          seq: event.seq,
          agentId: event.node_id ?? null,
          port: stringValue(data.port) ?? '',
          path,
          type: stringValue(data.type) ?? 'FILE',
          step: numberValue(data.step),
          batchId: stringValue(data.batch_id),
          bytes: numberValue(data.bytes),
        },
      ]
    }
  } else if (event.type === 'human_gate_waiting') {
    const gateId = stringValue(data.gate_id)
    if (gateId) {
      gates = {
        ...gates,
        [gateId]: {
          gateId,
          edgeId: event.edge_id ?? null,
          prompt: stringValue(data.prompt) ?? 'Approve this workflow step.',
          timeoutSeconds: numberValue(data.timeout_seconds),
          requestedAt: event.ts,
          status: 'waiting',
          decision: null,
          comment: null,
          timedOut: false,
        },
      }
    }
  } else if (event.type === 'human_gate_resolved') {
    const gateId = stringValue(data.gate_id)
    if (gateId) {
      const previous = gates[gateId]
      gates = {
        ...gates,
        [gateId]: {
          gateId,
          edgeId: event.edge_id ?? previous?.edgeId ?? null,
          prompt: previous?.prompt ?? 'Approve this workflow step.',
          timeoutSeconds: previous?.timeoutSeconds ?? null,
          requestedAt: previous?.requestedAt ?? null,
          status: 'resolved',
          decision: stringValue(data.decision),
          comment: stringValue(data.comment),
          timedOut: Boolean(data.timed_out),
        },
      }
    }
  } else if (event.type === 'error') {
    if (event.node_id) {
      nodeStatuses = { ...nodeStatuses, [event.node_id]: 'failed' }
    }
    error = stringValue(data.message) ?? 'Workflow run failed'
  } else if (event.type === 'run_finished') {
    const finishedStatus = runStatus(data.status)
    terminalStatus =
      finishedStatus && TERMINAL_STATUSES.has(finishedStatus)
        ? finishedStatus
        : 'failed'
    state.status = terminalStatus
    error =
      stringValue(data.error) ??
      (terminalStatus === 'failed'
        ? error ?? 'Workflow run failed'
        : null)
  }

  state.nodeStatuses = nodeStatuses
  state.edgeTraversals = edgeTraversals
  state.tokenUsage = tokenUsage
  state.tokenUsageByAgent = tokenUsageByAgent
  state.artifacts = artifacts
  state.gates = gates
  state.terminalStatus = terminalStatus
  state.error = error
  if (!state.terminalStatus) state.status = statusFromGates(state)
  return state
}

export function replayRunEvents(
  events: RunEvent[],
  untilSeq = Number.POSITIVE_INFINITY,
): RunReplayState {
  let state = createEmptyRunReplayState()
  for (const event of dedupeAndSortRunEvents(events)) {
    if (event.seq > untilSeq) break
    state = applyRunEvent(state, event)
  }
  return state
}

export function nextReplaySeq(events: RunEvent[], currentSeq: number) {
  const sequence = dedupeAndSortRunEvents(events)
    .map((event) => event.seq)
    .find((value) => value > currentSeq)
  return sequence ?? null
}

export function previousReplaySeq(events: RunEvent[], currentSeq: number) {
  const sequence = dedupeAndSortRunEvents(events)
    .map((event) => event.seq)
    .filter((value) => value < currentSeq)
    .at(-1)
  return sequence ?? 0
}
