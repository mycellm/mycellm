import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type FleetView = 'grid' | 'list'
type Theme = 'dark' | 'light' | 'system'

interface SettingsState {
  fleetView: FleetView
  fleetSort: string
  fleetAdminKey: string
  theme: Theme
  setFleetView: (view: FleetView) => void
  setFleetSort: (sort: string) => void
  setFleetAdminKey: (key: string) => void
  setTheme: (theme: Theme) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      fleetView: 'grid' as FleetView,
      fleetSort: 'name',
      fleetAdminKey: '',
      theme: 'dark' as Theme,
      setFleetView: (fleetView) => set({ fleetView }),
      setFleetSort: (fleetSort) => set({ fleetSort }),
      setFleetAdminKey: (fleetAdminKey) => set({ fleetAdminKey }),
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: 'mycellm_settings',
    }
  )
)
