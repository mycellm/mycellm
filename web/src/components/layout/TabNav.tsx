import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  LayoutDashboard,
  Network,
  Box,
  MessageSquare,
  Coins,
  ScrollText,
  Settings,
  Menu,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { LanguageSelector } from '@/components/layout/LanguageSelector'
import type { Tab } from '@/hooks/useTabRouter'

interface TabNavProps {
  tab: Tab
  onTabChange: (tab: Tab) => void
}

const tabs: { id: Tab; icon: typeof LayoutDashboard; labelKey: string }[] = [
  { id: 'overview', icon: LayoutDashboard, labelKey: 'nav.overview' },
  { id: 'network', icon: Network, labelKey: 'nav.network' },
  { id: 'models', icon: Box, labelKey: 'nav.models' },
  { id: 'chat', icon: MessageSquare, labelKey: 'nav.chat' },
  { id: 'credits', icon: Coins, labelKey: 'nav.credits' },
  { id: 'logs', icon: ScrollText, labelKey: 'nav.logs' },
  { id: 'settings', icon: Settings, labelKey: 'nav.settings' },
]

export function TabNav({ tab, onTabChange }: TabNavProps) {
  const { t } = useTranslation('common')
  const [drawerOpen, setDrawerOpen] = useState(false)

  const handleSelect = (id: Tab) => {
    onTabChange(id)
    setDrawerOpen(false)
  }

  const activeTab = tabs.find((tb) => tb.id === tab)

  return (
    <>
      {/* Desktop: horizontal tab bar */}
      <nav className="hidden md:block border-b border-white/10 px-2">
        <div className="max-w-7xl mx-auto flex">
          {tabs.map(({ id, icon: Icon, labelKey }) => {
            const isActive = tab === id
            return (
              <button
                key={id}
                onClick={() => onTabChange(id)}
                className={cn(
                  'flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors whitespace-nowrap',
                  'border-b-2 -mb-px',
                  isActive
                    ? 'border-spore text-spore'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                )}
              >
                <Icon className="w-4 h-4" />
                <span>{t(labelKey, id)}</span>
              </button>
            )
          })}
        </div>
      </nav>

      {/* Mobile: current tab + hamburger */}
      <div className="md:hidden border-b border-white/10 px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-gray-300">
          {activeTab && <activeTab.icon className="w-4 h-4 text-spore" />}
          <span className="font-mono text-sm">
            {t(activeTab?.labelKey ?? 'nav.overview', tab)}
          </span>
        </div>
        <button
          onClick={() => setDrawerOpen(true)}
          className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors"
          aria-label="Open menu"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      {/* Mobile drawer overlay */}
      {drawerOpen && (
        <div className="fixed inset-0 z-[100] md:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/70"
            onClick={() => setDrawerOpen(false)}
          />
          {/* Drawer panel */}
          <div className="absolute right-0 top-0 bottom-0 w-64 bg-void border-l border-white/10 flex flex-col">
            {/* Drawer header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
              <img
                src="/brand/mycellm-h-R.svg"
                alt="mycellm"
                className="h-5"
              />
              <button
                onClick={() => setDrawerOpen(false)}
                className="p-1 text-gray-500 hover:text-white"
                aria-label="Close menu"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Nav items */}
            <nav className="flex-1 py-2 overflow-y-auto">
              {tabs.map(({ id, icon: Icon, labelKey }) => {
                const isActive = tab === id
                return (
                  <button
                    key={id}
                    onClick={() => handleSelect(id)}
                    className={cn(
                      'w-full flex items-center gap-3 px-5 py-3 text-sm font-medium transition-colors',
                      isActive
                        ? 'text-spore bg-spore/5 border-r-2 border-spore'
                        : 'text-gray-400 hover:text-white hover:bg-white/5'
                    )}
                  >
                    <Icon className="w-4 h-4" />
                    <span>{t(labelKey, id)}</span>
                  </button>
                )
              })}
            </nav>

            {/* Drawer footer: language selector */}
            <div className="border-t border-white/10 px-5 py-3">
              <LanguageSelector />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
