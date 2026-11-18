import type {
  Agent,
  ConditionNode,
  EdgeMapping,
  PortType,
  SDL,
} from './types'

export function uniqueId(base: string, values: Iterable<string>) {
  const taken = new Set(values)
  if (!taken.has(base)) return base
  let index = 2
  while (taken.has(`${base}-${index}`)) index += 1
  return `${base}-${index}`
}

export function isCompatible(source: PortType, target: PortType) {
  return source === target || source === 'MIXED' || target === 'MIXED'
}

export function firstCompatibleMapping(
  source: Agent,
  target: Agent,
): EdgeMapping[] {
  for (const output of source.outputs) {
    for (const input of target.inputs) {
      if (isCompatible(output.type, input.type)) {
        return [{ from: output.name, to: input.name }]
      }
    }
  }
  return []
}

export function defaultCondition(sdl: SDL): ConditionNode {
  const firstOutput = sdl.agents.flatMap((agent) =>
    agent.outputs.map((port) => `$.agents.${agent.id}.outputs.${port.name}`),
  )[0]
  return {
    op: 'eq',
    left: firstOutput ?? '$.run.step',
    right: true,
  }
}

export function conditionPaths(sdl: SDL) {
  return [
    ...sdl.workflow.inputs.map((input) => ({
      value: `$.workflow.inputs.${input.name}`,
      label: `workflow.${input.name}`,
    })),
    ...sdl.agents.flatMap((agent) =>
      agent.outputs.map((port) => ({
        value: `$.agents.${agent.id}.outputs.${port.name}`,
        label: `${agent.id}.${port.name}`,
      })),
    ),
    ...sdl.edges.map((edge) => ({
      value: `$.edges.${edge.id}.traversals`,
      label: `${edge.id}.traversals`,
    })),
    { value: '$.run.step', label: 'run.step' },
  ]
}
