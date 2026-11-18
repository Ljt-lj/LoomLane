import { create } from 'zustand'

export type DrawerTab = 'sdl' | 'issues' | 'events' | 'result'
export type InspectorTab = 'selection' | 'workflow' | 'api'

export const INSPECTOR_MIN_WIDTH = 300
export const INSPECTOR_MAX_WIDTH = 560
export const INSPECTOR_DEFAULT_WIDTH = 360
export const LEFT_PANEL_MIN_WIDTH = 220
export const LEFT_PANEL_MAX_WIDTH = 420
export const LEFT_PANEL_DEFAULT_WIDTH = 270
export const DRAWER_MIN_HEIGHT = 180
export const DRAWER_MAX_HEIGHT = 520
export const DRAWER_DEFAULT_HEIGHT = 260

const INSPECTOR_OPEN_KEY = 'loomlane.inspector.open'
const INSPECTOR_WIDTH_KEY = 'loomlane.inspector.width'
const LEFT_PANEL_OPEN_KEY = 'loomlane.left-panel.open'
const LEFT_PANEL_WIDTH_KEY = 'loomlane.left-panel.width'
const DRAWER_OPEN_KEY = 'loomlane.drawer.open'
const DRAWER_HEIGHT_KEY = 'loomlane.drawer.height'

function clamp(value: number, minimum: number, maximum: number, fallback: number) {
  if (!Number.isFinite(value)) return fallback
  return Math.min(
    maximum,
    Math.max(minimum, Math.round(value)),
  )
}

export function clampInspectorWidth(width: number) {
  return clamp(
    width,
    INSPECTOR_MIN_WIDTH,
    INSPECTOR_MAX_WIDTH,
    INSPECTOR_DEFAULT_WIDTH,
  )
}

export function clampLeftPanelWidth(width: number) {
  return clamp(
    width,
    LEFT_PANEL_MIN_WIDTH,
    LEFT_PANEL_MAX_WIDTH,
    LEFT_PANEL_DEFAULT_WIDTH,
  )
}

export function clampDrawerHeight(height: number) {
  return clamp(
    height,
    DRAWER_MIN_HEIGHT,
    DRAWER_MAX_HEIGHT,
    DRAWER_DEFAULT_HEIGHT,
  )
}

function readOpenPreference(key: string) {
  if (typeof window === 'undefined') return true
  try {
    return window.localStorage.getItem(key) !== 'false'
  } catch {
    return true
  }
}

function readNumberPreference(
  key: string,
  fallback: number,
  clampValue: (value: number) => number,
) {
  if (typeof window === 'undefined') return fallback
  try {
    const storedValue = window.localStorage.getItem(key)
    if (storedValue === null) return fallback
    return clampValue(Number(storedValue))
  } catch {
    return fallback
  }
}

function persistPreference(key: string, value: boolean | number) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // UI preferences are optional when storage is unavailable.
  }
}

interface UiState {
  drawerTab: DrawerTab
  inspectorTab: InspectorTab
  drawerOpen: boolean
  drawerHeight: number
  leftPanelOpen: boolean
  leftPanelWidth: number
  inspectorOpen: boolean
  inspectorWidth: number
  setDrawerTab: (tab: DrawerTab) => void
  setInspectorTab: (tab: InspectorTab) => void
  setDrawerOpen: (open: boolean) => void
  setDrawerHeight: (height: number) => void
  setLeftPanelOpen: (open: boolean) => void
  setLeftPanelWidth: (width: number) => void
  setInspectorOpen: (open: boolean) => void
  setInspectorWidth: (width: number) => void
}

export const useUiStore = create<UiState>((set) => ({
  drawerTab: 'sdl',
  inspectorTab: 'selection',
  drawerOpen: readOpenPreference(DRAWER_OPEN_KEY),
  drawerHeight: readNumberPreference(
    DRAWER_HEIGHT_KEY,
    DRAWER_DEFAULT_HEIGHT,
    clampDrawerHeight,
  ),
  leftPanelOpen: readOpenPreference(LEFT_PANEL_OPEN_KEY),
  leftPanelWidth: readNumberPreference(
    LEFT_PANEL_WIDTH_KEY,
    LEFT_PANEL_DEFAULT_WIDTH,
    clampLeftPanelWidth,
  ),
  inspectorOpen: readOpenPreference(INSPECTOR_OPEN_KEY),
  inspectorWidth: readNumberPreference(
    INSPECTOR_WIDTH_KEY,
    INSPECTOR_DEFAULT_WIDTH,
    clampInspectorWidth,
  ),
  setDrawerTab: (drawerTab) => set({ drawerTab, drawerOpen: true }),
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
  setDrawerOpen: (drawerOpen) => {
    persistPreference(DRAWER_OPEN_KEY, drawerOpen)
    set({ drawerOpen })
  },
  setDrawerHeight: (height) => {
    const drawerHeight = clampDrawerHeight(height)
    persistPreference(DRAWER_HEIGHT_KEY, drawerHeight)
    set({ drawerHeight })
  },
  setLeftPanelOpen: (leftPanelOpen) => {
    persistPreference(LEFT_PANEL_OPEN_KEY, leftPanelOpen)
    set({ leftPanelOpen })
  },
  setLeftPanelWidth: (width) => {
    const leftPanelWidth = clampLeftPanelWidth(width)
    persistPreference(LEFT_PANEL_WIDTH_KEY, leftPanelWidth)
    set({ leftPanelWidth })
  },
  setInspectorOpen: (inspectorOpen) => {
    persistPreference(INSPECTOR_OPEN_KEY, inspectorOpen)
    set({ inspectorOpen })
  },
  setInspectorWidth: (width) => {
    const inspectorWidth = clampInspectorWidth(width)
    persistPreference(INSPECTOR_WIDTH_KEY, inspectorWidth)
    set({ inspectorWidth })
  },
}))
