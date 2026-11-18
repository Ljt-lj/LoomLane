import { describe, expect, it } from 'vitest'
import {
  clampDrawerHeight,
  clampInspectorWidth,
  clampLeftPanelWidth,
  DRAWER_DEFAULT_HEIGHT,
  DRAWER_MAX_HEIGHT,
  DRAWER_MIN_HEIGHT,
  INSPECTOR_DEFAULT_WIDTH,
  INSPECTOR_MAX_WIDTH,
  INSPECTOR_MIN_WIDTH,
  LEFT_PANEL_DEFAULT_WIDTH,
  LEFT_PANEL_MAX_WIDTH,
  LEFT_PANEL_MIN_WIDTH,
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

describe('clampLeftPanelWidth', () => {
  it('keeps the navigation width inside the supported range', () => {
    expect(clampLeftPanelWidth(LEFT_PANEL_MIN_WIDTH - 1)).toBe(
      LEFT_PANEL_MIN_WIDTH,
    )
    expect(clampLeftPanelWidth(LEFT_PANEL_MAX_WIDTH + 1)).toBe(
      LEFT_PANEL_MAX_WIDTH,
    )
    expect(clampLeftPanelWidth(310)).toBe(310)
  })

  it('falls back to the default for invalid values', () => {
    expect(clampLeftPanelWidth(Number.NaN)).toBe(LEFT_PANEL_DEFAULT_WIDTH)
  })
})

describe('clampDrawerHeight', () => {
  it('keeps the drawer height inside the supported range', () => {
    expect(clampDrawerHeight(DRAWER_MIN_HEIGHT - 1)).toBe(DRAWER_MIN_HEIGHT)
    expect(clampDrawerHeight(DRAWER_MAX_HEIGHT + 1)).toBe(DRAWER_MAX_HEIGHT)
    expect(clampDrawerHeight(320)).toBe(320)
  })

  it('falls back to the default for invalid values', () => {
    expect(clampDrawerHeight(Number.POSITIVE_INFINITY)).toBe(
      DRAWER_DEFAULT_HEIGHT,
    )
  })
})
