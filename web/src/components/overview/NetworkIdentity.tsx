import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Crown, Monitor, Globe, Shuffle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNodeStore } from '@/stores/node'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import type { FederationInfo } from '@/api/types'

const modeConfig: Record<string, { color: string; bg: string; icon: typeof Crown }> = {
  seeder: { color: 'text-spore', bg: 'bg-spore/10', icon: Monitor },
  consumer: { color: 'text-relay', bg: 'bg-relay/10', icon: Globe },
  root: { color: 'text-ledger', bg: 'bg-ledger/10', icon: Crown },
  gateway: { color: 'text-ledger', bg: 'bg-ledger/10', icon: Globe },
  relay: { color: 'text-relay', bg: 'bg-relay/10', icon: Shuffle },
  federated: { color: 'text-relay', bg: 'bg-relay/10', icon: Globe },
  standalone: { color: 'text-dimmed', bg: 'bg-white/5', icon: Monitor },
}

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return '0s'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function NetworkIdentity() {
  const { t } = useTranslation('overview')
  const status = useNodeStore((s) => s.status)
  const [federation, setFederation] = useState<FederationInfo | null>(null)

  useEffect(() => {
    api.get<FederationInfo>(API.node.federation).then(setFederation).catch(() => {})
  }, [])

  const nodeName = status?.node_name || 'mycellm-node'
  const mode = status?.mode || 'standalone'
  const peerId = status?.peer_id || ''
  const peers = status?.peers || []
  const models = status?.models || []
  const uptime = status?.uptime_seconds || 0

  const cfg = modeConfig[mode] || modeConfig.standalone

  return (
    <div className="bg-surface border border-white/10 rounded-xl p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
      <div className="flex items-center gap-4 min-w-0">
        <div
          className={cn(
            'w-10 h-10 rounded-lg border flex items-center justify-center shrink-0',
            cfg.bg,
            cfg.color.replace('text-', 'border-') + '/20'
          )}
        >
          <cfg.icon size={20} className={cfg.color} />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-console font-mono font-bold text-sm truncate">
              {nodeName}
            </h2>
            <span
              className={cn(
                'text-xs px-1.5 py-0.5 rounded font-mono shrink-0',
                cfg.bg,
                cfg.color
              )}
            >
              {mode}
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5 flex-wrap">
            <span>{federation?.network_name || t('standalone', 'Standalone')}</span>
            {federation?.network_id && (
              <span className="font-mono">
                {federation.network_id.slice(0, 8)}...
              </span>
            )}
            {federation?.public && (
              <span className="text-spore">{t('public', 'public')}</span>
            )}
            <span>&middot;</span>
            <span>
              {formatUptime(uptime)} {t('uptime', 'uptime')}
            </span>
          </div>
          {peerId && (
            <div className="text-xs text-gray-500 font-mono mt-0.5">
              {peerId.slice(0, 8)}...
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-4 sm:gap-6 text-xs shrink-0">
        <div className="text-center">
          <div className="text-xl font-mono text-white">{peers.length}</div>
          <div className="text-gray-500">{t('peers', 'peers')}</div>
        </div>
        <div className="text-center">
          <div className="text-xl font-mono text-spore">{models.length}</div>
          <div className="text-gray-500">{t('models', 'models')}</div>
        </div>
      </div>
    </div>
  )
}
