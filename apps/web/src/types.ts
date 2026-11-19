export type PortType = 'TEXT' | 'DATA' | 'FILE' | 'MIXED'
export type EdgeType =
  | 'PIPE'
  | 'CONDITIONAL'
  | 'FEEDBACK'
  | 'LOOP'
  | 'FAN_OUT'
  | 'AGGREGATE'
  | 'HUMAN_GATE'
export type RunStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface Port {
  name: string
  type: PortType
  required?: boolean
  schema_hint?: Record<string, unknown> | null
}

export interface WorkflowInput extends Port {
  required: boolean
}

export interface WorkflowOutput {
  name: string
  agent: string
  port: string
}

export interface GlobalContext {
  text: string
  injection: 'prefix' | 'suffix'
  applies_to: 'all' | string[]
}

export interface WorkflowMeta {
  id: string
  name: string
  global_context: GlobalContext | null
  inputs: WorkflowInput[]
  outputs: WorkflowOutput[]
  execution: {
    feedback_limit_default: number
    loop_limit_default: number
    max_steps: number
    max_total_tokens: number | null
    max_parallel: number
  }
}

export interface ApiProfile {
  id: string
  library_id?: string | null
  name?: string | null
  provider: 'openai_compatible'
  base_url: string
  model: string
  request: {
    timeout_seconds: number
    max_retries: number
    temperature: number
  }
  capabilities: {
    function_calling: boolean
    vision: boolean
    long_context: boolean
    structured_output: boolean
  }
  note?: string | null
}

export interface AgentJsonOutput {
  enabled: boolean
  template: Record<string, unknown>
}

export interface Agent {
  id: string
  name: string
  api_profile: string
  system_prompt: string
  inputs: Port[]
  outputs: Port[]
  limits: {
    max_tokens: number | null
    max_total_tokens: number | null
  }
  json_output: AgentJsonOutput
  output_dir: string | null
}

export interface ConditionNode {
  op: string
  args?: ConditionNode[]
  left?: string
  right?: unknown
}

export interface EdgeMapping {
  from: string
  to: string
}

export interface AggregateConfig {
  mode: 'all' | 'any'
}

export interface HumanGateConfig {
  prompt: string
  timeout_seconds: number
  on_reject: 'fail' | 'skip'
  on_timeout: 'fail' | 'approve' | 'reject'
}

export interface WorkflowEdge {
  id: string
  from: string
  to: string
  type: EdgeType
  mappings: EdgeMapping[]
  condition?: ConditionNode | null
  guard?: {
    max_traversals?: number | null
  }
  aggregate?: AggregateConfig | null
  human_gate?: HumanGateConfig | null
}

export interface SDL {
  sdl_version: '0.1' | '0.2'
  workflow: WorkflowMeta
  start: string[]
  api_profiles: ApiProfile[]
  agents: Agent[]
  edges: WorkflowEdge[]
}

export interface CanvasPoint {
  x: number
  y: number
}

export interface CanvasNode {
  id: string
  x: number
  y: number
  width: number
  height: number
}

export interface CanvasEdge {
  id: string
  bend_points: CanvasPoint[]
}

export interface CanvasLayout {
  canvas_version: '0.1'
  viewport: {
    x: number
    y: number
    zoom: number
  }
  nodes: CanvasNode[]
  edges: CanvasEdge[]
}

export interface ProjectSummary {
  id: string
  name: string
  revision: number
  created_at: string
  updated_at: string
  last_build_hash: string | null
}

export interface ProjectDetail extends ProjectSummary {
  sdl: SDL
  canvas: CanvasLayout
}

export interface ValidationIssue {
  code: string
  message: string
  path: string
  severity: 'error' | 'warning'
  node_id?: string | null
  edge_id?: string | null
}

export interface ValidationResponse {
  valid: boolean
  issues: ValidationIssue[]
  normalized_sdl: SDL | null
}

export interface RunEvent {
  seq: number
  run_id: string
  ts: string
  type: string
  node_id?: string | null
  edge_id?: string | null
  data: Record<string, unknown>
}

export interface RunSummary {
  run_id: string
  project_id: string
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface PendingHumanGate {
  gate_id: string
  edge_id: string
  prompt: string
  requested_at: string
  timeout_seconds: number | null
}

export interface RunDetail extends RunSummary {
  result: Record<string, unknown> | null
  events: RunEvent[]
  pending_gates: PendingHumanGate[]
}

export interface GateDecisionResponse {
  run_id: string
  gate_id: string
  decision: 'approve' | 'reject'
  accepted_at: string
}

export interface SecretStatus {
  profile_id: string
  configured: boolean
  origin: string | null
  scope?: 'project' | 'library' | null
}

export interface GenerateResponse {
  compiler_key: string
  generated_path: string
  cached: boolean
  files: string[]
}

export interface RunStartRequest {
  inputs: Record<string, unknown>
}
