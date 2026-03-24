import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import type { CreditTier, Transaction } from '@/api/types'
import { useCreditsStore } from '@/stores/credits'
import { useCredits } from '@/hooks/useCredits'
import { CreditsSummary } from './CreditsSummary'
import { TierCard } from './TierCard'
import { ReceiptsCard } from './ReceiptsCard'
import { TransactionHistory } from './TransactionHistory'

interface TierResponse {
  tier: string
  label: string
  access: string
  balance: number
  next_tier_at: number
  receipts: { total: number; verified: number; fleet: number }
}

interface HistoryResponse {
  transactions: Transaction[]
}

export function CreditsTab() {
  const setTier = useCreditsStore((s) => s.setTier)
  const setHistory = useCreditsStore((s) => s.setHistory)
  const tier = useCreditsStore((s) => s.tier)

  // Poll credits balance (already exists as a hook)
  useCredits()

  // Poll credit tier every 5s
  const { data: tierData } = useQuery<TierResponse>({
    queryKey: ['node', 'credits', 'tier'],
    queryFn: () => api.get<TierResponse>(API.node.creditsTier),
    refetchInterval: 5000,
  })

  useEffect(() => {
    if (tierData) {
      setTier(tierData as CreditTier)
    }
  }, [tierData, setTier])

  // Poll transaction history every 5s
  const { data: historyData } = useQuery<HistoryResponse>({
    queryKey: ['node', 'credits', 'history'],
    queryFn: () => api.get<HistoryResponse>(`${API.node.creditsHistory}?limit=100`),
    refetchInterval: 5000,
  })

  useEffect(() => {
    if (historyData) {
      setHistory(historyData.transactions || [])
    }
  }, [historyData, setHistory])

  return (
    <div className="space-y-6">
      <CreditsSummary />

      {tier && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TierCard />
          <ReceiptsCard />
        </div>
      )}

      <TransactionHistory />
    </div>
  )
}

export default CreditsTab
