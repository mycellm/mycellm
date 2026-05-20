import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, X, Radio } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useFleetStore } from '@/stores/fleet'
import { useApproveNode, useRemoveNode } from '@/hooks/useFleetNodes'
import { StatusDot } from '@/components/common/StatusDot'
import { EmptyState } from '@/components/common/EmptyState'
import { NodeDetailPanel } from './NodeDetailPanel'
import type { FleetNode } from '@/api/types'

const statusBadgeStyles: Record<string, string> = {
  approved: 'bg-spore/10 text-spore',
  pending: 'bg-ledger/10 text-ledger',
  rejected: 'bg-compute/10 text-compute',
}

const statusToDot: Record<string, 'online' | 'pending' | 'offline'> = {
  approved: 'online',
  pending: 'pending',
  rejected: 'offline',
}

const borderStyles: Record<string, string> = {
  pending: 'border-ledger/30 bg-ledger/5',
  approved: 'border-white/10 bg-black',
  rejected: 'border-compute/20 bg-compute/5',
}

export function FleetRegistry() {
  const { t } = useTranslation('network')
  const fleetNodes = useFleetStore((s) => s.fleetNodes)
  const approveMutation = useApproveNode()
  const removeMutation = useRemoveNode()
  const [selectedNode, setSelectedNode] = useState<FleetNode | null>(null)

  const handleApprove = (peerId: string) => {
    approveMutation.mutate(peerId)
  }

  const handleRemove = (peerId: string) => {
    removeMutation.mutate(peerId)
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
        {t('fleet.title', 'Fleet')} ({fleetNodes.length}{' '}
        {t('fleet.nodeCount', { count: fleetNodes.length, defaultValue: 'node{{s}}' }).replace('{{s}}', fleetNodes.length !== 1 ? 's' : '')})
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        {t(
          'fleet.description',
          'Nodes announce themselves when they start with this node as bootstrap. Approve to enable management from this dashboard.'
        )}
      </p>

      <div className="space-y-3">
        {fleetNodes.map((node) => {
          const isPending = node.status === 'pending'
          const caps = node.capabilities || { role: '', models: [] }
          const models = caps.models || []

          return (
            <div
              key={node.peer_id}
              className={cn(
                'border rounded-xl p-4 cursor-pointer transition-colors',
                borderStyles[node.status] || 'border-white/10 bg-black'
              )}
              onClick={() => setSelectedNode(node)}
            >
              {/* Header row */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center space-x-3">
                  <StatusDot status={statusToDot[node.status] || 'offline'} size="sm" />
                  <span className="font-mono text-sm font-medium text-console">
                    {node.node_name || t('fleet.unnamed', 'unnamed')}
                  </span>
                  <span className="font-mono text-xs text-gray-500 hidden md:inline">
                    {node.api_addr}
                  </span>
                  <span className="font-mono text-xs text-gray-600 hidden md:inline">
                    {node.peer_id?.slice(0, 8)}...
                  </span>
                </div>

                <div className="flex items-center space-x-2">
                  {isPending && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleApprove(node.peer_id)
                      }}
                      disabled={approveMutation.isPending}
                      className="flex items-center space-x-1 bg-spore text-black px-2.5 py-1 rounded text-xs font-medium hover:bg-spore/90 transition-all disabled:opacity-50"
                    >
                      <Check size={12} />
                      <span>{t('fleet.approve', 'Approve')}</span>
                    </button>
                  )}
                  <span
                    className={cn(
                      'text-xs font-mono px-2 py-0.5 rounded',
                      statusBadgeStyles[node.status] || 'bg-white/5 text-gray-500'
                    )}
                  >
                    {node.status}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleRemove(node.peer_id)
                    }}
                    disabled={removeMutation.isPending}
                    className="text-gray-600 hover:text-compute transition-colors p-1"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>

              {/* Mobile: show address on separate line */}
              <div className="flex items-center space-x-2 text-xs text-gray-600 md:hidden mb-2">
                <span className="font-mono">{node.api_addr}</span>
                <span className="font-mono">{node.peer_id?.slice(0, 8)}...</span>
              </div>

              {/* System info (compact) */}
              {node.system?.memory && (
                <div className="mt-3 pt-3 border-t border-white/5">
                  <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                    <div className="overflow-hidden">
                      <div className="text-gray-500">{t('fleet.ram', 'RAM')}</div>
                      <div className="text-console truncate">
                        {node.system.memory.total_gb?.toFixed(1)} GB ({node.system.memory.used_pct}%)
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Models */}
              {models.length > 0 && (
                <div className="mt-3 pt-3 border-t border-white/5 flex flex-wrap gap-1.5">
                  {models.map((m, i) => (
                    <span
                      key={i}
                      className="text-xs font-mono bg-spore/10 text-spore px-2 py-0.5 rounded"
                    >
                      {m.name || String(m)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}

        {fleetNodes.length === 0 && (
          <EmptyState
            icon={Radio}
            message={t(
              'fleet.empty',
              'No nodes have announced yet. Start remote nodes with MYCELLM_BOOTSTRAP_PEERS pointing here.'
            )}
          />
        )}
      </div>

      {selectedNode && (
        <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
    </div>
  )
}
