import { describe, expect, it } from 'vitest'
import {
  clampInspectorWidth,
  INSPECTOR_DEFAULT_WIDTH,
  INSPECTOR_MAX_WIDTH,
  INSPECTOR_MIN_WIDTH,
} from './uiStore'

describe('clampInspectorWidth', () => {
  it('keeps inspector widths inside the supported range', () => {
    expect(clampInspectorWidth(INSPECTOR_MIN_WIDTH - 1)).toBe(
      INSPECTOR_MIN_WIDTH,
    )
    expect(clampInspectorWidth(INSPECTOR_MAX_WIDTH + 1)).toBe(
      INSPECTOR_MAX_WIDTH,
    )
    expect(clampInspectorWidth(420)).toBe(420)
  })

  it('falls back to the default for non-finite widths', () => {
    expect(clampInspectorWidth(Number.NaN)).toBe(INSPECTOR_DEFAULT_WIDTH)
    expect(clampInspectorWidth(Number.POSITIVE_INFINITY)).toBe(
      INSPECTOR_DEFAULT_WIDTH,
    )
  })
})
