import { useEffect, Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'
import { useAuthStore } from '@/stores/auth'
import { useNodeStore } from '@/stores/node'
import { useNodeStatus } from '@/hooks/useNodeStatus'
import { useCredits } from '@/hooks/useCredits'
import { useTabRouter } from '@/hooks/useTabRouter'
import { useTheme } from '@/hooks/useTheme'
import { AuthGate } from '@/components/auth/AuthGate'
import { BootScreen } from '@/components/auth/BootScreen'
import { Header } from '@/components/layout/Header'
import { TabNav } from '@/components/layout/TabNav'
import { Footer } from '@/components/layout/Footer'
import NetworkCanvas from '@/components/canvas/NetworkCanvas'

import { OverviewTab } from '@/components/overview/OverviewTab'
import { NetworkTab } from '@/components/network/NetworkTab'
import { ModelsTab } from '@/components/models/ModelsTab'
import { ChatTab } from '@/components/chat/ChatTab'
import { CreditsTab } from '@/components/credits/CreditsTab'
import { LogsTab } from '@/components/logs/LogsTab'
import { SettingsTab } from '@/components/settings/SettingsTab'

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

        <main className="flex-1 overflow-y-auto custom-scrollbar p-4 bg-void">
          <ErrorBoundary key={tab}>
            <TabContent tab={tab} />
          </ErrorBoundary>
        </main>

        <Footer />
      </div>
    </div>
  )
}

function TabContent({ tab }: { tab: string }) {
  return (
    <div className="max-w-7xl mx-auto w-full">
      {tab === 'overview' && <OverviewTab />}
      {tab === 'network' && <NetworkTab />}
      {tab === 'models' && <ModelsTab />}
      {tab === 'chat' && <ChatTab />}
      {tab === 'credits' && <CreditsTab />}
      {tab === 'logs' && <LogsTab />}
      {tab === 'settings' && <SettingsTab />}
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

          // Save version from health endpoint
          if (data.version) {
            useNodeStore.getState().setVersionInfo({
              current: data.version,
              update_available: false,
            })
          }

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
