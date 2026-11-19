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

export function cloneAgent(
  agent: Agent,
  existingIds: Iterable<string>,
): Agent {
  return {
    ...structuredClone(agent),
    id: uniqueId(`${agent.id}-copy`, existingIds),
    name: `${agent.name} 副本`,
    output_dir: null,
  }
}

export type ApiProfileRemovalResult =
  | {
      kind: 'removed'
      sdl: SDL
      referencedAgentIds: string[]
      replacementProfileId: string | null
    }
  | {
      kind: 'blocked'
      reason: 'not-found' | 'only-referenced-profile'
      referencedAgentIds: string[]
    }

export function removeApiProfile(
  sdl: SDL,
  profileId: string,
): ApiProfileRemovalResult {
  if (!sdl.api_profiles.some((profile) => profile.id === profileId)) {
    return {
      kind: 'blocked',
      reason: 'not-found',
      referencedAgentIds: [],
    }
  }

  const referencedAgentIds = sdl.agents
    .filter((agent) => agent.api_profile === profileId)
    .map((agent) => agent.id)
  const remainingProfiles = sdl.api_profiles.filter(
    (profile) => profile.id !== profileId,
  )

  if (referencedAgentIds.length > 0 && remainingProfiles.length === 0) {
    return {
      kind: 'blocked',
      reason: 'only-referenced-profile',
      referencedAgentIds,
    }
  }

  const replacementProfileId =
    referencedAgentIds.length > 0 ? remainingProfiles[0].id : null

  return {
    kind: 'removed',
    referencedAgentIds,
    replacementProfileId,
    sdl: {
      ...sdl,
      api_profiles: remainingProfiles,
      agents: replacementProfileId
        ? sdl.agents.map((agent) =>
            agent.api_profile === profileId
              ? { ...agent, api_profile: replacementProfileId }
              : agent,
          )
        : sdl.agents,
    },
  }
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

export function appendConditionPredicate(
  condition: ConditionNode,
  predicate: ConditionNode,
): ConditionNode {
  if (condition.op === 'and' || condition.op === 'or') {
    return {
      op: condition.op,
      args: [...(condition.args ?? []), predicate],
    }
  }
  return {
    op: 'and',
    args: [condition, predicate],
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
