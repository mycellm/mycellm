import { useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Activity,
  Cpu,
  GitBranch,
  Link,
  Users,
  Radio,
  AlertTriangle,
  ArrowRight,
  X as XIcon,
  Plus,
  Minus,
  ArrowUp,
  ArrowDown,
  Globe,
  HeartPulse,
  Power,
  Share2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useActivityStore } from '@/stores/activity'
import type { ActivityEvent } from '@/api/types'

const typeColors: Record<string, string> = {
  inference_complete: 'text-compute',
  inference_start: 'text-compute/50',
  inference_failed: 'text-red-400',
  route_decision: 'text-relay',
  peer_connected: 'text-spore',
  peer_disconnected: 'text-gray-500',
  model_loaded: 'text-spore',
  model_unloaded: 'text-gray-500',
  credit_earned: 'text-ledger',
  credit_spent: 'text-ledger/70',
  announce_ok: 'text-spore/50',
  announce_failed: 'text-poison',
  fleet_node_joined: 'text-ledger',
  peer_exchange_received: 'text-relay',
  nat_discovered: 'text-relay/70',
  connection_health: 'text-yellow-500',
  node_started: 'text-spore',
  node_error: 'text-red-400',
}

const typeIcons: Record<string, typeof Cpu> = {
  inference_complete: Cpu,
  inference_start: ArrowRight,
  inference_failed: XIcon,
  route_decision: GitBranch,
  peer_connected: Link,
  peer_disconnected: Link,
  model_loaded: Plus,
  model_unloaded: Minus,
  credit_earned: ArrowUp,
  credit_spent: ArrowDown,
  announce_ok: Radio,
  announce_failed: AlertTriangle,
  fleet_node_joined: Users,
  peer_exchange_received: Share2,
  nat_discovered: Globe,
  connection_health: HeartPulse,
  node_started: Power,
  node_error: AlertTriangle,
}

function eventLabel(e: ActivityEvent): string {
  switch (e.type) {
    case 'inference_complete':
      return `${e.model || '?'} > ${e.source || '?'} (${e.tokens || 0} tok, ${e.latency_ms ? e.latency_ms + 'ms' : '?'})`
    case 'inference_failed':
      return `${e.model || '?'} failed`
    case 'route_decision':
      return `Routed ${e.model || '?'} > ${(e.routed_to || '').slice(0, 12) || '?'}`
    case 'peer_connected':
      return `Peer ${(e.peer_id || '').slice(0, 12)}... connected`
    case 'peer_disconnected':
      return `Peer ${(e.peer_id || '').slice(0, 12)}... disconnected`
    case 'model_loaded':
      return `Loaded ${e.model || '?'}`
    case 'model_unloaded':
      return `Unloaded ${e.model || '?'}`
    case 'credit_earned':
      return `+${(e.amount || 0).toFixed(4)} from ${(e.peer_id || '').slice(0, 12) || 'local'}`
    case 'credit_spent':
      return `-${(e.amount || 0).toFixed(4)} to ${(e.peer_id || '').slice(0, 12) || 'network'}`
    case 'announce_ok':
      return `Announced to bootstrap`
    case 'announce_failed':
      return `Announce failed`
    case 'fleet_node_joined':
      return `${e.node_name || 'node'} joined fleet`
    case 'peer_exchange_received':
      return `Discovered ${e.peers_discovered || 0} peer(s) via exchange`
    case 'nat_discovered':
      return `NAT: ${e.nat_type || '?'} (${e.public_ip || '?'}, hole-punch=${e.hole_punch || '?'})`
    case 'connection_health':
      return `Peer ${(e.peer_id || '').slice(0, 12)}... ${e.status || '?'} (health=${e.health || '?'})`
    case 'node_started':
      return `Node started (${e.node_name || e.peer_id || '?'})`
    case 'node_error':
      return `${e.message || 'Error occurred'}`
    default:
      return e.type.replace(/_/g, ' ')
  }
}

export function ActivityFeed() {
  const { t } = useTranslation('overview')
  const events = useActivityStore((s) => s.events)
  const liveEvents = useActivityStore((s) => s.liveEvents)
  const stats = useActivityStore((s) => s.stats)
  const containerRef = useRef<HTMLDivElement>(null)

  const allEvents = useMemo(
    () => [...(events || []), ...liveEvents].slice(-80),
    [events, liveEvents]
  )

  useEffect(() => {
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [allEvents])

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-3 flex items-center gap-2">
        <Activity size={12} />
        <span>{t('activity.title', 'Activity')}</span>
        {(stats?.requests_1m ?? 0) > 0 && (
          <span className="text-compute animate-pulse">
            &bull; {stats?.requests_1m}/min
          </span>
        )}
      </h2>

      <div
        ref={containerRef}
        className="space-y-1 max-h-64 overflow-y-auto custom-scrollbar text-xs font-mono"
      >
        {allEvents.length === 0 && (
          <div className="text-gray-600 text-center py-4">
            {t(
              'noActivity',
              'No activity yet. Send an inference request to see events.'
            )}
          </div>
        )}
        {allEvents.map((e, i) => {
          const Icon = typeIcons[e.type] || Activity
          const color = typeColors[e.type] || 'text-gray-500'

          return (
            <div
              key={i}
              className="flex items-center gap-2 py-1 hover:bg-white/[0.02] rounded px-1"
            >
              <span className="text-gray-600 w-14 shrink-0">
                {e.time || ''}
              </span>
              <Icon size={12} className={cn('shrink-0', color)} />
              <span className={cn('truncate', color)}>{eventLabel(e)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
