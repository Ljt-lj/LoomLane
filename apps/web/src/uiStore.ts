import { create } from 'zustand'

export type DrawerTab = 'sdl' | 'issues' | 'events' | 'result'
export type InspectorTab = 'selection' | 'workflow' | 'api'

export const INSPECTOR_MIN_WIDTH = 300
export const INSPECTOR_MAX_WIDTH = 560
export const INSPECTOR_DEFAULT_WIDTH = 360

const INSPECTOR_OPEN_KEY = 'loomlane.inspector.open'
const INSPECTOR_WIDTH_KEY = 'loomlane.inspector.width'

export function clampInspectorWidth(width: number) {
  if (!Number.isFinite(width)) return INSPECTOR_DEFAULT_WIDTH
  return Math.min(
    INSPECTOR_MAX_WIDTH,
    Math.max(INSPECTOR_MIN_WIDTH, Math.round(width)),
  )
}

function readInspectorOpen() {
  if (typeof window === 'undefined') return true
  try {
    return window.localStorage.getItem(INSPECTOR_OPEN_KEY) !== 'false'
  } catch {
    return true
  }
}

function readInspectorWidth() {
  if (typeof window === 'undefined') return INSPECTOR_DEFAULT_WIDTH
  try {
    const storedWidth = window.localStorage.getItem(INSPECTOR_WIDTH_KEY)
    if (storedWidth === null) return INSPECTOR_DEFAULT_WIDTH
    return clampInspectorWidth(Number(storedWidth))
  } catch {
    return INSPECTOR_DEFAULT_WIDTH
  }
}

function persistInspectorState(open?: boolean, width?: number) {
  if (typeof window === 'undefined') return
  try {
    if (open !== undefined) {
      window.localStorage.setItem(INSPECTOR_OPEN_KEY, String(open))
    }
    if (width !== undefined) {
      window.localStorage.setItem(INSPECTOR_WIDTH_KEY, String(width))
    }
  } catch {
    // UI preferences are optional when storage is unavailable.
  }
}

interface UiState {
  drawerTab: DrawerTab
  inspectorTab: InspectorTab
  drawerOpen: boolean
  inspectorOpen: boolean
  inspectorWidth: number
  setDrawerTab: (tab: DrawerTab) => void
  setInspectorTab: (tab: InspectorTab) => void
  setDrawerOpen: (open: boolean) => void
  setInspectorOpen: (open: boolean) => void
  setInspectorWidth: (width: number) => void
}

export const useUiStore = create<UiState>((set) => ({
  drawerTab: 'sdl',
  inspectorTab: 'selection',
  drawerOpen: true,
  inspectorOpen: readInspectorOpen(),
  inspectorWidth: readInspectorWidth(),
  setDrawerTab: (drawerTab) => set({ drawerTab, drawerOpen: true }),
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setInspectorOpen: (inspectorOpen) => {
    persistInspectorState(inspectorOpen)
    set({ inspectorOpen })
  },
  setInspectorWidth: (width) => {
    const inspectorWidth = clampInspectorWidth(width)
    persistInspectorState(undefined, inspectorWidth)
    set({ inspectorWidth })
  },
}))
