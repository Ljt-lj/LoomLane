import { describe, expect, it } from 'vitest'
import type { Agent, ApiProfile, SDL } from './types'
import {
  appendConditionPredicate,
  conditionPaths,
  defaultCondition,
  firstCompatibleMapping,
  isCompatible,
  removeApiProfile,
  uniqueId,
} from './workflow-utils'

function agent(
  id: string,
  inputs: Agent['inputs'],
  outputs: Agent['outputs'],
  apiProfile = 'openai-main',
): Agent {
  return {
    id,
    name: id,
    api_profile: apiProfile,
    system_prompt: '',
    inputs,
    outputs,
    limits: { max_tokens: null, max_total_tokens: null },
    json_output: { enabled: false, template: {} },
    output_dir: null,
  }
}

function apiProfile(id: string): ApiProfile {
  return {
    id,
    name: id,
    provider: 'openai_compatible',
    base_url: 'https://api.example.com/v1',
    model: 'example',
    request: {
      timeout_seconds: 60,
      max_retries: 2,
      temperature: 0.2,
    },
    capabilities: {
      function_calling: false,
      vision: false,
      long_context: false,
      structured_output: true,
    },
  }
}

describe('workflow utilities', () => {
  it('creates stable unique identifiers', () => {
    expect(uniqueId('agent', [])).toBe('agent')
    expect(uniqueId('agent', ['agent'])).toBe('agent-2')
    expect(uniqueId('agent', ['agent', 'agent-2', 'agent-3'])).toBe('agent-4')
  })

  it('allows exact and mixed port matches only', () => {
    expect(isCompatible('TEXT', 'TEXT')).toBe(true)
    expect(isCompatible('MIXED', 'FILE')).toBe(true)
    expect(isCompatible('DATA', 'MIXED')).toBe(true)
    expect(isCompatible('TEXT', 'DATA')).toBe(false)
  })

  it('selects the first compatible output-to-input mapping', () => {
    const source = agent(
      'source',
      [],
      [
        { name: 'data', type: 'DATA' },
        { name: 'draft', type: 'TEXT' },
      ],
    )
    const target = agent(
      'target',
      [
        { name: 'document', type: 'TEXT', required: true },
        { name: 'payload', type: 'DATA' },
      ],
      [],
    )

    expect(firstCompatibleMapping(source, target)).toEqual([
      { from: 'data', to: 'payload' },
    ])
  })

  it('builds condition paths from the current SDL', () => {
    const sdl = {
      workflow: {
        inputs: [{ name: 'requirement', type: 'TEXT', required: true }],
      },
      agents: [
        agent(
          'drafter',
          [],
          [{ name: 'draft', type: 'TEXT' }],
        ),
      ],
      edges: [{ id: 'draft-review' }],
    } as SDL

    expect(conditionPaths(sdl)).toEqual([
      {
        value: '$.workflow.inputs.requirement',
        label: 'workflow.requirement',
      },
      {
        value: '$.agents.drafter.outputs.draft',
        label: 'drafter.draft',
      },
      {
        value: '$.edges.draft-review.traversals',
        label: 'draft-review.traversals',
      },
      { value: '$.run.step', label: 'run.step' },
    ])
  })

  it('defaults conditions to the first agent output', () => {
    const sdl = {
      agents: [
        agent('drafter', [], [{ name: 'draft', type: 'TEXT' }]),
      ],
    } as SDL

    expect(defaultCondition(sdl)).toEqual({
      op: 'eq',
      left: '$.agents.drafter.outputs.draft',
      right: true,
    })
    expect(defaultCondition({ agents: [] } as unknown as SDL).left).toBe(
      '$.run.step',
    )
  })

  it('promotes a predicate into a multi-predicate AND group', () => {
    const first = {
      op: 'lt',
      left: '$.agents.reviewer.outputs.score',
      right: 0.8,
    }
    const second = {
      op: 'lt',
      left: '$.edges.review-feedback.traversals',
      right: 3,
    }

    expect(appendConditionPredicate(first, second)).toEqual({
      op: 'and',
      args: [first, second],
    })
  })

  it('appends predicates without changing an existing group operator', () => {
    const first = {
      op: 'eq',
      left: '$.run.step',
      right: 1,
    }
    const second = {
      op: 'exists',
      left: '$.workflow.inputs.requirement',
    }
    const group = { op: 'or', args: [first] }

    expect(appendConditionPredicate(group, second)).toEqual({
      op: 'or',
      args: [first, second],
    })
  })
})

describe('removeApiProfile', () => {
  it('removes an unused profile', () => {
    const sdl = {
      api_profiles: [apiProfile('first'), apiProfile('second')],
      agents: [agent('drafter', [], [], 'second')],
    } as SDL

    const result = removeApiProfile(sdl, 'first')

    expect(result.kind).toBe('removed')
    if (result.kind !== 'removed') return
    expect(result.sdl.api_profiles.map((profile) => profile.id)).toEqual([
      'second',
    ])
    expect(result.sdl.agents[0].api_profile).toBe('second')
    expect(result.referencedAgentIds).toEqual([])
  })

  it('switches referenced agents to the first remaining profile', () => {
    const sdl = {
      api_profiles: [
        apiProfile('first'),
        apiProfile('second'),
        apiProfile('third'),
      ],
      agents: [
        agent('drafter', [], [], 'first'),
        agent('reviewer', [], [], 'third'),
        agent('publisher', [], [], 'first'),
      ],
    } as SDL

    const result = removeApiProfile(sdl, 'first')

    expect(result.kind).toBe('removed')
    if (result.kind !== 'removed') return
    expect(result.referencedAgentIds).toEqual(['drafter', 'publisher'])
    expect(result.replacementProfileId).toBe('second')
    expect(result.sdl.agents.map((item) => item.api_profile)).toEqual([
      'second',
      'third',
      'second',
    ])
    expect(sdl.agents[0].api_profile).toBe('first')
  })

  it('blocks deleting the only profile while it is still referenced', () => {
    const sdl = {
      api_profiles: [apiProfile('only')],
      agents: [agent('drafter', [], [], 'only')],
    } as SDL

    expect(removeApiProfile(sdl, 'only')).toEqual({
      kind: 'blocked',
      reason: 'only-referenced-profile',
      referencedAgentIds: ['drafter'],
    })
  })

  it('allows deleting the final profile when no agent references it', () => {
    const sdl = {
      api_profiles: [apiProfile('only')],
      agents: [agent('drafter', [], [])],
    } as SDL

    const result = removeApiProfile(sdl, 'only')

    expect(result.kind).toBe('removed')
    if (result.kind !== 'removed') return
    expect(result.sdl.api_profiles).toEqual([])
  })
})
