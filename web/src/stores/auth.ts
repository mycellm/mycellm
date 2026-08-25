import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type AppState = 'checking' | 'auth' | 'booting' | 'dashboard'

interface AuthState {
  apiKey: string
  appState: AppState
  /// Has the boot splash already played in this browser?
  ///
  /// ⚠️ THE SPLASH COST ~1.85s ON EVERY PAGE LOAD. It is a fixed animation —
  /// five lines at 250ms plus a 600ms pause — and it ran every single time the
  /// dashboard was opened or refreshed on any node with an API key set, which
  /// is every real node. Loading `/network` directly felt like the Network tab
  /// was slow; it was the app shell, on every page equally.
  hasBooted: boolean
  setApiKey: (key: string) => void
  setAppState: (state: AppState) => void
  setHasBooted: (v: boolean) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: '',
      appState: 'checking' as AppState,
      hasBooted: false,
      setApiKey: (key: string) => set({ apiKey: key }),
      setAppState: (state: AppState) => set({ appState: state }),
      setHasBooted: (v: boolean) => set({ hasBooted: v }),
      // Logging out resets the splash: signing in again is the one moment it
      // is welcome rather than in the way.
      logout: () => set({ apiKey: '', appState: 'auth', hasBooted: false }),
    }),
    {
      name: 'mycellm_api_key',
      partialize: (state) => ({ apiKey: state.apiKey, hasBooted: state.hasBooted }),
    }
  )
)
