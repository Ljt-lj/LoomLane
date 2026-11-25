import { describe, expect, it } from 'vitest'
import { buildTransformExpression, transformMode } from './transform-utils'

describe('transform utilities', () => {
  it('keeps legacy expression transforms readable', () => {
    expect(
      transformMode({
        expression: '{"brief": inputs.brief}',
      }),
    ).toBe('expression')
  })

  it('detects field transforms', () => {
    expect(
      transformMode({
        mode: 'fields',
        fields: [{ path: 'brief', value: 'inputs.brief' }],
      }),
    ).toBe('fields')
  })

  it('builds nested object expressions from field paths', () => {
    expect(
      buildTransformExpression([
        { path: 'brief', value: 'inputs.brief' },
        { path: 'outline.sections', value: 'raw_outputs.sections' },
        { path: 'outline.title', value: '"Draft"' },
      ]),
    ).toBe(
      '{"brief": inputs.brief, "outline": {"sections": raw_outputs.sections, "title": "Draft"}}',
    )
  })

  it('treats field path keys as data rather than object prototypes', () => {
    expect(
      buildTransformExpression([
        {
          path: 'constructor.prototype.polluted',
          value: 'True',
        },
      ]),
    ).toBe('{"constructor": {"prototype": {"polluted": True}}}')
    expect(
      ({} as { polluted?: boolean }).polluted,
    ).toBeUndefined()
  })
})
