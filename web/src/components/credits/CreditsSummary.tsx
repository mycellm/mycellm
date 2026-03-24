import { Coins, TrendingUp, TrendingDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useCreditsStore } from '@/stores/credits'
import { StatCard } from '@/components/common/StatCard'

export function CreditsSummary() {
  const { t } = useTranslation('credits')
  const balance = useCreditsStore((s) => s.balance)
  const earned = useCreditsStore((s) => s.earned)
  const spent = useCreditsStore((s) => s.spent)

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <StatCard
        label={t('balance', 'Balance')}
        value={balance.toFixed(2)}
        icon={Coins}
        color="ledger"
        highlight
      />
      <StatCard
        label={t('totalEarned', 'Total Earned')}
        value={`+${earned.toFixed(2)}`}
        icon={TrendingUp}
        color="spore"
        highlight
      />
      <StatCard
        label={t('totalSpent', 'Total Spent')}
        value={`-${spent.toFixed(2)}`}
        icon={TrendingDown}
        color="compute"
        highlight
      />
    </div>
  )
}
