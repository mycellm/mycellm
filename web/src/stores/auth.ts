import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type AppState = 'checking' | 'auth' | 'booting' | 'dashboard'

interface AuthState {
  apiKey: string
  appState: AppState
  setApiKey: (key: string) => void
  setAppState: (state: AppState) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: '',
      appState: 'checking' as AppState,
      setApiKey: (key: string) => set({ apiKey: key }),
      setAppState: (state: AppState) => set({ appState: state }),
      logout: () => set({ apiKey: '', appState: 'auth' }),
    }),
    {
      name: 'mycellm_api_key',
      partialize: (state) => ({ apiKey: state.apiKey }),
    }
  )
)
