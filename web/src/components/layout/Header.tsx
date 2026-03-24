import { useTranslation } from 'react-i18next'
import { useNodeStore } from '@/stores/node'
import { useCreditsStore } from '@/stores/credits'
import { StatusDot } from '@/components/common/StatusDot'
import { LanguageSelector } from '@/components/layout/LanguageSelector'

export function Header() {
  const { t } = useTranslation('common')
  const status = useNodeStore((s) => s.status)
  const versionInfo = useNodeStore((s) => s.versionInfo)
  const balance = useCreditsStore((s) => s.balance)

  const peerCount = status?.peers?.length ?? 0
  const modelCount = status?.models?.length ?? 0
  const nodeName = status?.node_name ?? ''
  const isOnline = !!status

  return (
    <header className="border-b border-white/10 px-4 py-3">
      {/* Desktop layout */}
      <div className="flex items-center justify-between gap-4">
        {/* Left: Lockup logo + node name */}
        <div className="flex items-center gap-3 min-w-0">
          <img
            src="/brand/mycellm-h-R.svg"
            alt="mycellm"
            className="h-6 sm:h-7 shrink-0"
          />
          {nodeName && (
            <span className="text-gray-500 text-sm truncate hidden sm:inline max-w-[200px]">
              {nodeName}
            </span>
          )}
        </div>

        {/* Right: Badges + status */}
        <div className="flex items-center gap-3">
          {/* Badges - hidden on mobile, shown in second row */}
          <div className="hidden sm:flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-xs font-mono px-2 py-0.5 rounded-full border border-relay/30 text-relay">
              {peerCount} {t('nav.peers', 'peers')}
            </span>
            <span className="inline-flex items-center gap-1.5 text-xs font-mono px-2 py-0.5 rounded-full border border-white/10 text-gray-400">
              {modelCount} {t('nav.models', 'models')}
            </span>
            <span className="text-xs font-mono text-ledger">
              {balance.toLocaleString()} {t('nav.credits', 'cr')}
            </span>
          </div>

          <LanguageSelector />

          <StatusDot
            status={isOnline ? 'online' : 'offline'}
            size="md"
          />
        </div>
      </div>

      {/* Mobile second row: badges */}
      <div className="flex sm:hidden items-center gap-2 mt-2 overflow-x-auto scrollbar-none">
        <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-full border border-relay/30 text-relay shrink-0">
          {peerCount} {t('nav.peers', 'peers')}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-full border border-white/10 text-gray-400 shrink-0">
          {modelCount} {t('nav.models', 'models')}
        </span>
        <span className="text-xs font-mono text-ledger shrink-0">
          {balance.toLocaleString()} {t('nav.credits', 'cr')}
        </span>
      </div>
    </header>
  )
}
