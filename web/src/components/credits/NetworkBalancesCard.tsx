import { useQuery } from '@tanstack/react-query'
import { Network } from 'lucide-react'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'

interface NetBalance {
  network_id: string
  label: string
  balance: number
  earned: number
  spent: number
  tracked: boolean
}

interface NetworksResponse {
  peer_id: string
  networks: NetBalance[]
  aggregate_balance: number
}

/**
 * Per-network authoritative balances from the tracker (the network's
 * source-of-truth node). Distinct from the local balance card above, which is
 * this node's own view; these are reconciled against the tracker per network.
 */
export function NetworkBalancesCard() {
  const { data } = useQuery<NetworksResponse>({
    queryKey: ['node', 'credits', 'networks'],
    queryFn: () => api.get<NetworksResponse>(API.node.creditsNetworks),
    refetchInterval: 5000,
  })

  const nets = (data?.networks || []).filter((n) => n.tracked)

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-sm text-neutral-300">
          <Network className="w-4 h-4" style={{ color: '#3B82F6' }} />
          <span>Per-Network Balances</span>
          <span className="text-[10px] text-neutral-500 font-mono">authoritative · tracker</span>
        </div>
        {data && (
          <div className="text-right">
            <div className="text-lg font-semibold" style={{ color: '#FACC15' }}>
              {data.aggregate_balance.toFixed(2)}
            </div>
            <div className="text-[10px] text-neutral-500">aggregate</div>
          </div>
        )}
      </div>

      {nets.length === 0 ? (
        <div className="text-xs text-neutral-500 py-2">
          No tracked network balances yet — serve or consume inference to appear on a network's ledger.
        </div>
      ) : (
        <div className="space-y-1">
          {nets.map((n) => (
            <div
              key={n.network_id || 'default'}
              className="flex items-center justify-between py-1.5 border-b border-white/5 last:border-0"
            >
              <span className="text-xs font-mono text-neutral-300">{n.label}</span>
              <div className="flex items-center gap-4 text-xs">
                <span style={{ color: '#22C55E' }}>+{n.earned.toFixed(3)}</span>
                {n.spent > 0 && <span style={{ color: '#EF4444' }}>-{n.spent.toFixed(3)}</span>}
                <span className="font-semibold w-16 text-right" style={{ color: '#FACC15' }}>
                  {n.balance.toFixed(2)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default NetworkBalancesCard
