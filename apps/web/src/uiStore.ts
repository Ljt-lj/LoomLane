import { create } from 'zustand'

export type DrawerTab = 'sdl' | 'issues' | 'events' | 'result'
export type InspectorTab = 'selection' | 'workflow' | 'api'

interface UiState {
  drawerTab: DrawerTab
  inspectorTab: InspectorTab
  drawerOpen: boolean
  setDrawerTab: (tab: DrawerTab) => void
  setInspectorTab: (tab: InspectorTab) => void
  setDrawerOpen: (open: boolean) => void
}

export const useUiStore = create<UiState>((set) => ({
  drawerTab: 'sdl',
  inspectorTab: 'selection',
  drawerOpen: true,
  setDrawerTab: (drawerTab) => set({ drawerTab, drawerOpen: true }),
  setInspectorTab: (inspectorTab) => set({ inspectorTab }),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
}))
