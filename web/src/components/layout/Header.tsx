import { useTranslation } from 'react-i18next'
import { Activity, Radio, Key, Shield } from 'lucide-react'
import { useNodeStore } from '@/stores/node'
import { useCreditsStore } from '@/stores/credits'
import { useFleetStore } from '@/stores/fleet'
import { LanguageSelector } from '@/components/layout/LanguageSelector'

export function Header() {
  const { t } = useTranslation('common')
  const status = useNodeStore((s) => s.status)
  const balance = useCreditsStore((s) => s.balance)
  const fleetNodes = useFleetStore((s) => s.fleetNodes)

  const peerCount = status?.peers?.length ?? 0
  const fleetCount = Array.isArray(fleetNodes) ? fleetNodes.filter((n) => n.online).length : 0
  const nodeName = status?.node_name ?? ''
  const peerId = status?.peer_id ?? ''
  const isOnline = !!status

  return (
    <header className="border-b border-white/10 bg-void/80 backdrop-blur-md sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 h-12 sm:h-14 flex items-center justify-between">
        {/* Left: Lockup logo + node name */}
        <div className="flex items-center space-x-2 sm:space-x-3 min-w-0">
          <img
            src="/brand/mycellm-h-R.svg"
            alt="mycellm"
            className="h-5 sm:h-6 shrink-0"
          />
          {/* Node name + peer ID — desktop only */}
          <div className="hidden sm:flex items-center space-x-2 min-w-0">
            <div className="h-4 w-px bg-white/20" />
            <span className="font-mono text-xs bg-white/10 text-gray-300 px-2 py-1 rounded truncate max-w-[200px]">
              {nodeName}
            </span>
            {peerId && (
              <span className="font-mono text-xs text-gray-600 hidden lg:inline">
                {peerId.slice(0, 12)}...
              </span>
            )}
          </div>
        </div>

        {/* Right: Stats — always visible, compact on mobile */}
        <div className="flex items-center space-x-2 sm:space-x-5 font-mono text-xs sm:text-sm shrink-0">
          <div className="flex items-center space-x-1 sm:space-x-1.5 text-relay">
            <Activity size={12} />
            <span className="hidden sm:inline">{peerCount} {t('header.peers', 'peers')}</span>
            <span className="sm:hidden">{peerCount}</span>
          </div>
          <div className="hidden md:flex items-center space-x-1.5 text-relay">
            <Radio size={12} />
            <span>{fleetCount} fleet</span>
          </div>
          <div className="flex items-center space-x-1 sm:space-x-1.5 text-ledger">
            <Key size={12} />
            <span>{balance.toFixed(0)}</span>
          </div>
          <div
            className={`flex items-center space-x-1 ${isOnline ? 'text-spore' : 'text-gray-600'}`}
          >
            <Shield size={12} />
            <span className="hidden sm:inline">
              {isOnline ? t('status.online', 'Online') : t('status.offline', 'Offline')}
            </span>
          </div>

          {/* Language selector — desktop only (mobile in drawer) */}
          <div className="hidden md:block">
            <LanguageSelector />
          </div>
        </div>
      </div>
    </header>
  )
}
