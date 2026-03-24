import { useEffect } from 'react'
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
    <div className="min-h-screen flex flex-col bg-void relative">
      <NetworkCanvas />
      <Header />
      <TabNav tab={tab} onTabChange={setTab} />

      <main className="flex-1 overflow-y-auto custom-scrollbar p-4">
        <TabContent tab={tab} />
      </main>

      <Footer />
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

          // If we have a stored key and it worked, boot
          if (apiKey) {
            setAppState('booting')
            return
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
