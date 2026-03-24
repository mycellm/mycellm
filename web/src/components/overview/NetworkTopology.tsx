import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Network, Crown, Monitor, Globe, Shuffle, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNodeStore } from '@/stores/node'
import { useFleetStore } from '@/stores/fleet'
import { StatusDot } from '@/components/common/StatusDot'

interface TopologyNode {
  id: string
  name: string
  role: string
  status: string
  models: string[]
  type: 'self' | 'quic' | 'fleet'
  system?: {
    cpu?: { name: string }
    memory?: { total_gb: number; used_pct: number }
    gpu?: { gpu: string }
    os?: { hostname: string }
  }
}

const roleIcons: Record<string, typeof Crown> = {
  bootstrap: Crown,
  seeder: Monitor,
  consumer: Globe,
  relay: Shuffle,
}

const statusToStatusDot = (
  status: string
): 'online' | 'offline' | 'pending' | 'degraded' => {
  switch (status) {
    case 'online':
    case 'routable':
      return 'online'
    case 'fleet':
    case 'discovered':
      return 'pending'
    case 'disconnected':
      return 'offline'
    default:
      return 'degraded'
  }
}

const borderColors: Record<string, string> = {
  online: 'border-spore',
  routable: 'border-spore',
  fleet: 'border-ledger/20',
  disconnected: 'border-compute',
  discovered: 'border-gray-600',
}

function NodeDetailModal({
  node,
  onClose,
}: {
  node: TopologyNode
  onClose: () => void
}) {
  const { t } = useTranslation('overview')
  const RoleIcon = roleIcons[node.role] || Monitor

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="bg-surface border border-white/10 rounded-xl p-6 max-w-lg w-full shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3 min-w-0">
            <RoleIcon size={20} className="text-gray-400 shrink-0" />
            <div className="min-w-0">
              <h3 className="text-white font-mono font-bold truncate">
                {node.name}
              </h3>
              <p className="text-xs text-gray-500 truncate">
                {node.id === 'self'
                  ? t('thisNode', 'This node')
                  : node.id}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">
                {t('role', 'Role')}
              </div>
              <div className="text-white mt-1">{node.role}</div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">
                {t('status', 'Status')}
              </div>
              <div
                className={cn(
                  'mt-1',
                  node.status === 'routable' || node.status === 'online'
                    ? 'text-spore'
                    : node.status === 'fleet'
                      ? 'text-ledger'
                      : 'text-compute'
                )}
              >
                {node.status}
              </div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">
                {t('transport', 'Transport')}
              </div>
              <div className="text-white mt-1">{node.type}</div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">
                {t('models', 'Models')}
              </div>
              <div className="text-white mt-1">{node.models?.length || 0}</div>
            </div>
          </div>

          {node.models && node.models.length > 0 && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">
                {t('availableModels', 'Available Models')}
              </div>
              <div className="flex flex-wrap gap-1">
                {node.models.map((m, i) => (
                  <span
                    key={i}
                    className="bg-white/5 text-xs text-gray-300 px-2 py-0.5 rounded font-mono"
                  >
                    {m}
                  </span>
                ))}
              </div>
            </div>
          )}

          {node.system && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">
                {t('hardware.cpu', 'Hardware')}
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-400">
                {node.system.cpu?.name && (
                  <div>CPU: {node.system.cpu.name}</div>
                )}
                {node.system.memory?.total_gb && (
                  <div>RAM: {node.system.memory.total_gb}GB</div>
                )}
                {node.system.gpu?.gpu &&
                  node.system.gpu.gpu !== 'CPU' && (
                    <div>GPU: {node.system.gpu.gpu}</div>
                  )}
                {node.system.os?.hostname && (
                  <div>Host: {node.system.os.hostname}</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function NetworkTopology() {
  const { t } = useTranslation('overview')
  const status = useNodeStore((s) => s.status)
  const fleetNodes = useFleetStore((s) => s.fleetNodes)
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null)

  const allNodes = useMemo<TopologyNode[]>(() => {
    const nodes: TopologyNode[] = [
      {
        id: 'self',
        name: status?.node_name || t('thisNode', 'This node'),
        role: status?.role || 'bootstrap',
        status: 'online',
        models: status?.models || [],
        type: 'self',
      },
    ]

    for (const p of status?.peers || []) {
      nodes.push({
        id: p.peer_id,
        name: (p.peer_id || '').slice(0, 12) + '...',
        role: 'seeder',
        status: 'routable',
        models: [],
        type: 'quic',
      })
    }

    for (const f of fleetNodes.filter((n) => n.status === 'approved')) {
      nodes.push({
        id: f.peer_id || f.node_name,
        name: f.node_name || (f.peer_id || '').slice(0, 12),
        role: f.capabilities?.role || 'seeder',
        status: 'fleet',
        models: (f.capabilities?.models || []).map((m) => m.name || String(m)),
        type: 'fleet',
        system: f.system as TopologyNode['system'],
      })
    }

    return nodes
  }, [status, fleetNodes, t])

  if (allNodes.length <= 1) return null

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4 flex items-center gap-2">
        <Network size={12} />
        <span>
          {t('topology.title', 'Topology')} ({allNodes.length}{' '}
          {t('nodes', 'nodes')})
        </span>
      </h2>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {allNodes.map((node) => {
          const RoleIcon = roleIcons[node.role] || Monitor

          return (
            <button
              key={node.id}
              onClick={() => setSelectedNode(node)}
              className={cn(
                'text-left bg-black border rounded-lg p-3 hover:bg-white/[0.03] transition-colors',
                node.type === 'self'
                  ? 'border-spore'
                  : borderColors[node.status] || 'border-white/10'
              )}
            >
              <div className="flex items-center gap-2 mb-2">
                <RoleIcon size={14} className="text-gray-400 shrink-0" />
                <span className="font-mono text-xs text-white truncate">
                  {node.name}
                </span>
                <span className="ml-auto shrink-0">
                  <StatusDot
                    status={statusToStatusDot(node.status)}
                    size="sm"
                  />
                </span>
              </div>
              <div className="text-xs text-gray-500 flex items-center gap-1">
                {node.models.length > 0 ? (
                  <span>
                    {node.models.length} model
                    {node.models.length !== 1 ? 's' : ''}
                  </span>
                ) : (
                  <span className="text-gray-600">
                    {t('noModels', 'no models')}
                  </span>
                )}
                <span>&middot;</span>
                <span
                  className={
                    node.type === 'self'
                      ? 'text-spore'
                      : node.type === 'fleet'
                        ? 'text-ledger'
                        : 'text-relay'
                  }
                >
                  {node.type === 'self'
                    ? t('thisNode', 'This node')
                    : node.type}
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {selectedNode && (
        <NodeDetailModal
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  )
}
