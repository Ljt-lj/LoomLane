import { describe, expect, it } from 'vitest'
import type { Agent, SDL } from './types'
import {
  conditionPaths,
  defaultCondition,
  firstCompatibleMapping,
  isCompatible,
  uniqueId,
} from './workflow-utils'

function agent(
  id: string,
  inputs: Agent['inputs'],
  outputs: Agent['outputs'],
): Agent {
  return {
    id,
    name: id,
    api_profile: 'openai-main',
    system_prompt: '',
    inputs,
    outputs,
    limits: { max_tokens: null },
    output_dir: null,
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
})
