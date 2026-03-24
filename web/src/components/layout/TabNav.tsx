import { useTranslation } from 'react-i18next'
import {
  LayoutDashboard,
  Network,
  Box,
  MessageSquare,
  Coins,
  ScrollText,
  Settings,
} from 'lucide-react'
import { cn } from '@/lib/utils'
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

  return (
    <nav className="border-b border-white/10 px-2">
      <div className="flex overflow-x-auto scrollbar-none">
        {tabs.map(({ id, icon: Icon, labelKey }) => {
          const isActive = tab === id

          return (
            <button
              key={id}
              onClick={() => onTabChange(id)}
              className={cn(
                'flex items-center gap-2 px-3 py-2.5 text-sm font-medium transition-colors whitespace-nowrap shrink-0',
                'border-b-2 -mb-px',
                isActive
                  ? 'border-spore text-spore'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              )}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="hidden md:inline">
                {t(labelKey, id)}
              </span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
