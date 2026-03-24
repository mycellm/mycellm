import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { FleetNode } from '@/api/types'

interface NodeDetailPanelProps {
  node: FleetNode | null
  onClose: () => void
}

const statusColors: Record<string, string> = {
  approved: 'text-spore',
  pending: 'text-ledger',
  rejected: 'text-compute',
}

export function NodeDetailPanel({ node, onClose }: NodeDetailPanelProps) {
  const { t } = useTranslation('network')

  if (!node) return null

  const caps = node.capabilities || { role: '', models: [] }
  const models = caps.models || []

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="bg-surface border border-white/10 rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-white font-mono font-bold">
              {node.node_name || t('detail.unnamed', 'Unnamed Node')}
            </h3>
            <p className="text-xs text-gray-500 font-mono">{node.peer_id}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Info grid */}
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <InfoCell
              label={t('detail.role', 'Role')}
              value={caps.role || 'unknown'}
            />
            <InfoCell
              label={t('detail.status', 'Status')}
              value={node.status}
              valueClassName={statusColors[node.status]}
            />
            <InfoCell
              label={t('detail.address', 'Address')}
              value={node.api_addr || 'N/A'}
            />
            <InfoCell
              label={t('detail.models', 'Models')}
              value={String(models.length)}
            />
          </div>

          {/* Online status */}
          <InfoCell
            label={t('detail.lastSeen', 'Last Seen')}
            value={node.last_seen || 'N/A'}
          />

          {/* Model list */}
          {models.length > 0 && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">
                {t('detail.availableModels', 'Available Models')}
              </div>
              <div className="flex flex-wrap gap-1">
                {models.map((m, i) => (
                  <span
                    key={i}
                    className="bg-white/5 text-xs text-gray-300 px-2 py-0.5 rounded font-mono"
                  >
                    {m.name || String(m)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* System info */}
          {node.system?.memory && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">
                {t('detail.hardware', 'Hardware')}
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-400">
                <div>
                  {t('detail.ram', 'RAM')}: {node.system.memory.total_gb?.toFixed(1)} GB
                </div>
                <div>
                  {t('detail.ramUsed', 'Used')}: {node.system.memory.used_pct}%
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function InfoCell({
  label,
  value,
  valueClassName,
}: {
  label: string
  value: string
  valueClassName?: string
}) {
  return (
    <div className="bg-black rounded-lg p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={cn('mt-1 text-white', valueClassName)}>{value}</div>
    </div>
  )
}
