import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { Bot, CircleAlert, CircleCheck, LoaderCircle } from 'lucide-react'

export type NodeRunStatus =
  | 'idle'
  | 'waiting'
  | 'ready'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export interface AgentNodeData extends Record<string, unknown> {
  label: string
  agentId: string
  profile: string
  inputs: string[]
  outputs: string[]
  status: NodeRunStatus
}

export type AgentFlowNode = Node<AgentNodeData, 'agent'>

function StatusGlyph({ status }: { status: NodeRunStatus }) {
  if (status === 'running') {
    return <LoaderCircle size={14} className="spin" />
  }
  if (status === 'failed') {
    return <CircleAlert size={14} />
  }
  if (status === 'succeeded') {
    return <CircleCheck size={14} />
  }
  return <span className="status-dot" />
}

export function AgentNode({ data, selected }: NodeProps<AgentFlowNode>) {
  return (
    <div
      className={[
        'agent-node',
        `is-${data.status}`,
        selected ? 'is-selected' : '',
      ].join(' ')}
    >
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="agent-node-top">
        <span className="agent-icon">
          <Bot size={16} strokeWidth={1.8} />
        </span>
        <div className="agent-title-wrap">
          <strong>{data.label}</strong>
          <span>{data.agentId}</span>
        </div>
        <span className={`node-state state-${data.status}`}>
          <StatusGlyph status={data.status} />
        </span>
      </div>
      <div className="agent-node-meta">
        <span>{data.profile}</span>
        <span>
          {data.inputs.length} in / {data.outputs.length} out
        </span>
      </div>
      <div className="agent-port-preview">
        <div>
          {data.inputs.slice(0, 2).map((port) => (
            <span key={port}>{port}</span>
          ))}
        </div>
        <div>
          {data.outputs.slice(0, 2).map((port) => (
            <span key={port}>{port}</span>
          ))}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  )
}
