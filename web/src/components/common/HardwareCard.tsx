import type { HardwareNode } from '@/api/types'
import { cn } from '@/lib/utils'
import { StatusDot } from './StatusDot'

interface HardwareCardProps {
  node: HardwareNode
  compact?: boolean
  className?: string
}

export function HardwareCard({ node, compact, className }: HardwareCardProps) {
  const borderClass = node.online ? 'border-spore/20' : 'border-white/5'

  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center gap-3 bg-surface border rounded-lg px-3 py-2 font-mono text-sm w-full',
          borderClass,
          className
        )}
      >
        <StatusDot status={node.online ? 'online' : 'offline'} size="sm" />
        <span className="text-console truncate">{node.name}</span>
        <span className="text-gray-500 truncate hidden sm:inline">{node.gpu || 'No GPU'}</span>
        <span className="text-gray-600 ml-auto shrink-0">
          {node.models.length} model{node.models.length !== 1 ? 's' : ''}
        </span>
      </div>
    )
  }

  const ramPct = node.ram_used_pct ?? 0

  return (
    <div
      className={cn(
        'bg-surface border rounded-xl p-3 md:p-4 font-mono text-sm w-full space-y-2',
        borderClass,
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <StatusDot status={node.online ? 'online' : 'offline'} size="md" />
        <span className="text-console font-semibold truncate">{node.name}</span>
        {node.type === 'fleet' && (
          <span className="text-xs text-relay/60 border border-relay/20 rounded px-1.5 py-0.5 ml-auto shrink-0">
            fleet
          </span>
        )}
      </div>

      {/* GPU + Backend */}
      <div className="text-gray-500 text-xs truncate">
        {node.gpu || 'No GPU'}
        {node.backend && <span className="text-gray-600"> / {node.backend}</span>}
      </div>

      {/* RAM bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>RAM</span>
          <span>{ramPct}% of {node.ram_gb.toFixed(1)} GB</span>
        </div>
        <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              ramPct > 90 ? 'bg-compute' : ramPct > 70 ? 'bg-ledger' : 'bg-spore'
            )}
            style={{ width: `${Math.min(ramPct, 100)}%` }}
          />
        </div>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-4 text-xs text-gray-500">
        {node.tps > 0 && (
          <span>
            <span className="text-console">{node.tps.toFixed(1)}</span> tok/s
          </span>
        )}
        <span>
          <span className="text-console">{node.models.length}</span> model{node.models.length !== 1 ? 's' : ''}
        </span>
        {node.vram_gb > 0 && (
          <span>
            <span className="text-console">{node.vram_gb.toFixed(1)}</span> GB VRAM
          </span>
        )}
      </div>
    </div>
  )
}
