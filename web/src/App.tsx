import { useEffect, Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'
import { useAuthStore } from '@/stores/auth'
import { useNodeStatus } from '@/hooks/useNodeStatus'
import { useCredits } from '@/hooks/useCredits'
import { useTabRouter } from '@/hooks/useTabRouter'
import { useTheme } from '@/hooks/useTheme'
import { lazy, Suspense } from 'react'
import { AuthGate } from '@/components/auth/AuthGate'
import { BootScreen } from '@/components/auth/BootScreen'
import { Header } from '@/components/layout/Header'
import { TabNav } from '@/components/layout/TabNav'
import { Footer } from '@/components/layout/Footer'
import NetworkCanvas from '@/components/canvas/NetworkCanvas'

const OverviewTab = lazy(() => import('@/components/overview/OverviewTab').then(m => ({ default: m.OverviewTab })))
const NetworkTab = lazy(() => import('@/components/network/NetworkTab').then(m => ({ default: m.default ?? m.NetworkTab })))
const ModelsTab = lazy(() => import('@/components/models/ModelsTab').then(m => ({ default: m.default ?? m.ModelsTab })))
const ChatTab = lazy(() => import('@/components/chat/ChatTab').then(m => ({ default: m.default ?? m.ChatTab })))
const CreditsTab = lazy(() => import('@/components/credits/CreditsTab').then(m => ({ default: m.default ?? m.CreditsTab })))
const LogsTab = lazy(() => import('@/components/logs/LogsTab').then(m => ({ default: m.default ?? m.LogsTab })))
const SettingsTab = lazy(() => import('@/components/settings/SettingsTab').then(m => ({ default: m.default ?? m.SettingsTab })))

function DashboardLayout() {
  const { tab, setTab } = useTabRouter()

  useNodeStatus()
  useCredits()

  return (
    <div className="min-h-screen flex flex-col bg-void">
      <NetworkCanvas />
      <div className="relative z-10 min-h-screen flex flex-col">
        <Header />
        <TabNav tab={tab} onTabChange={setTab} />

        <main className="flex-1 overflow-y-auto custom-scrollbar p-4">
          <ErrorBoundary>
            <TabContent tab={tab} />
          </ErrorBoundary>
        </main>

        <Footer />
      </div>
    </div>
  )
}

function TabContent({ tab }: { tab: string }) {
  const fallback = (
    <div className="flex items-center justify-center min-h-[200px]">
      <div className="w-2 h-2 bg-spore rounded-full animate-ping" />
    </div>
  )

  const placeholder = (name: string) => (
    <div className="flex items-center justify-center min-h-[200px] border border-dashed border-white/10 rounded-lg">
      <span className="text-gray-500 font-mono text-sm capitalize">{name}</span>
    </div>
  )

  return (
    <div className="max-w-6xl mx-auto w-full">
      <Suspense fallback={fallback}>
        {tab === 'overview' && <OverviewTab />}
        {tab === 'network' && <NetworkTab />}
        {tab === 'models' && <ModelsTab />}
        {tab === 'chat' && <ChatTab />}
        {tab === 'credits' && <CreditsTab />}
        {tab === 'logs' && <LogsTab />}
        {tab === 'settings' && <SettingsTab />}
      </Suspense>
    </div>
  )
}

function CheckingState() {
  const setAppState = useAuthStore((s) => s.setAppState)
  const apiKey = useAuthStore((s) => s.apiKey)

  useEffect(() => {
    let cancelled = false

    async function checkHealth() {
      try {
        const headers: Record<string, string> = {}
        if (apiKey) {
          headers['Authorization'] = `Bearer ${apiKey}`
        }

        const response = await fetch(`${window.location.origin}/health`, {
          headers,
        })

        if (cancelled) return

        if (response.ok) {
          const data = await response.json()

          // If no auth required, go straight to dashboard
          if (data.auth_required === false) {
            setAppState('dashboard')
            return
          }

          // Auth is required — validate stored key if we have one
          if (apiKey) {
            const testResp = await fetch(`${window.location.origin}/v1/node/status`, {
              headers: { Authorization: `Bearer ${apiKey}` },
            })
            if (cancelled) return
            if (testResp.ok) {
              setAppState('booting')
              return
            }
            // Stored key is invalid — clear it and show auth
            useAuthStore.getState().setApiKey('')
          }
        }

        // Need auth
        setAppState('auth')
      } catch {
        // Can't reach server - if we have a key, try dashboard anyway
        // Otherwise show auth
        if (!cancelled) {
          setAppState(apiKey ? 'booting' : 'auth')
        }
      }
    }

    checkHealth()

    return () => {
      cancelled = true
    }
  }, [apiKey, setAppState])

  return (
    <div className="min-h-screen bg-void flex items-center justify-center">
      <div className="flex items-center gap-3">
        <div className="w-2 h-2 bg-spore rounded-full animate-ping" />
        <span className="font-mono text-gray-500 text-sm">Connecting...</span>
      </div>
    </div>
  )
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Tab render error:', error, info)
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[200px] gap-3">
          <p className="text-compute font-mono text-sm">Component error: {this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="text-xs text-spore border border-spore/30 px-3 py-1 rounded hover:bg-spore/10"
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const appState = useAuthStore((s) => s.appState)
  useTheme()

  return (
    <>
      {appState === 'checking' && <CheckingState />}
      {appState === 'auth' && <AuthGate />}
      {appState === 'booting' && <BootScreen />}
      {appState === 'dashboard' && <DashboardLayout />}
    </>
  )
}
