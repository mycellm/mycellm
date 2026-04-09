import { useEffect, useCallback, useSyncExternalStore } from 'react'
import { useSettingsStore } from '../stores/settings'

type Theme = 'dark' | 'light' | 'system'
type ResolvedTheme = 'dark' | 'light'

const MEDIA_QUERY = '(prefers-color-scheme: dark)'

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark'
  return window.matchMedia(MEDIA_QUERY).matches ? 'dark' : 'light'
}

function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === 'system') return getSystemTheme()
  return theme
}

function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement
  if (resolved === 'light') {
    root.classList.add('light')
  } else {
    root.classList.remove('light')
  }
}

/**
 * Theme management hook.
 *
 * Reads the theme preference from useSettingsStore ('dark' | 'light' | 'system'),
 * resolves 'system' using matchMedia, applies/removes the 'light' class on
 * document.documentElement, and listens for OS preference changes.
 */
export function useTheme() {
  const theme = useSettingsStore((s) => s.theme)
  const setThemeStore = useSettingsStore((s) => s.setTheme)

  // Subscribe to system preference changes for real-time updates
  const systemTheme = useSyncExternalStore(
    (callback) => {
      const mql = window.matchMedia(MEDIA_QUERY)
      mql.addEventListener('change', callback)
      return () => mql.removeEventListener('change', callback)
    },
    () => getSystemTheme(),
    () => 'dark' as ResolvedTheme, // server fallback
  )

  // The actual resolved theme accounts for system preference
  const resolvedTheme: ResolvedTheme =
    theme === 'system' ? systemTheme : theme

  // Apply theme class whenever it changes
  useEffect(() => {
    applyTheme(resolvedTheme)
  }, [resolvedTheme])

  const setTheme = useCallback(
    (t: Theme) => {
      setThemeStore(t)
      // Eagerly apply so there's no flash
      applyTheme(resolveTheme(t))
    },
    [setThemeStore],
  )

  return { theme, resolvedTheme, setTheme } as const
}
