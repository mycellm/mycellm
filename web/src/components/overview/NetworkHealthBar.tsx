import { useTranslation } from 'react-i18next'
import { Heart } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNodeStore } from '@/stores/node'
import { useFleetStore } from '@/stores/fleet'

export function NetworkHealthBar() {
  const { t } = useTranslation('overview')
  const connections = useNodeStore((s) => s.connections)
  const status = useNodeStore((s) => s.status)
  const fleetNodes = useFleetStore((s) => s.fleetNodes)

  const peers = status?.peers || []

  const routableConns = connections.filter((c) => c.state === 'routable').length
  const totalConns = connections.length
  const approvedFleet = fleetNodes.filter((n) => n.status === 'approved').length
  const totalFleet = fleetNodes.length
  const totalPeers = peers.length

  const hasConnections = totalConns > 0
  const hasFleet = totalFleet > 0
  const hasPeers = totalPeers > 0

  let score = 0
  let weights = 0

  if (hasConnections) {
    score += (routableConns / totalConns) * 50
    weights += 50
  }
  if (hasFleet) {
    score += (approvedFleet / Math.max(totalFleet, 1)) * 40
    weights += 40
  }
  if (hasPeers) {
    score += Math.min(totalPeers, 3) / 3 * 30
    weights += 30
  }

  if (weights === 0) {
    score = 20
    weights = 100
  } else {
    score = Math.round((score / weights) * 100)
  }

  score = Math.min(score, 100)

  const barColor = score >= 70 ? 'bg-spore' : score >= 40 ? 'bg-ledger' : 'bg-compute'
  const label =
    score >= 70
      ? t('healthy', 'Healthy')
      : score >= 40
        ? t('degraded', 'Degraded')
        : score > 0
          ? t('limited', 'Limited')
          : t('offline', 'Offline')
  const labelColor =
    score >= 70 ? 'text-spore' : score >= 40 ? 'text-ledger' : 'text-compute'

  const parts: string[] = []
  if (hasConnections) parts.push(`${routableConns}/${totalConns} QUIC`)
  if (hasFleet) parts.push(`${approvedFleet}/${totalFleet} fleet`)
  if (hasPeers) parts.push(`${totalPeers} peers`)
  if (parts.length === 0) parts.push(t('noConnections', 'No connections'))

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest flex items-center gap-2">
          <Heart size={12} />
          <span>{t('networkHealth.title', 'Network Health')}</span>
        </h2>
        <div className="flex items-center gap-2">
          <span className={cn('font-mono text-2xl font-bold', labelColor)}>
            {score}
          </span>
          <span className={cn('text-xs', labelColor)}>{label}</span>
        </div>
      </div>

      <div className="w-full bg-void rounded-full h-2 overflow-hidden border border-white/5">
        <div
          className={cn('h-full transition-all duration-500', barColor)}
          style={{ width: `${score}%` }}
        />
      </div>

      <div className="flex flex-wrap justify-between mt-3 text-xs text-gray-500 gap-2">
        {parts.map((p, i) => (
          <span key={i}>{p}</span>
        ))}
      </div>
    </div>
  )
}
