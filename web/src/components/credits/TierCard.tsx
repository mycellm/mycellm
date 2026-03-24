import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useCreditsStore } from '@/stores/credits'

const tierStyles: Record<string, string> = {
  power: 'text-ledger bg-ledger/10 border-ledger/20',
  contributor: 'text-spore bg-spore/10 border-spore/20',
  free: 'text-gray-400 bg-white/5 border-white/10',
}

const tierAccess: Record<string, string> = {
  free: 'Tier 1 only',
  contributor: 'Tier 1 + 2',
  power: 'All tiers',
}

export function TierCard() {
  const { t } = useTranslation('credits')
  const tier = useCreditsStore((s) => s.tier)

  if (!tier) return null

  const tierName = tier.tier || 'free'
  const nextTierThreshold = tierName === 'free' ? 10 : tierName === 'contributor' ? 50 : null
  const progressPct = nextTierThreshold
    ? Math.min(100, (tier.balance / nextTierThreshold) * 100)
    : 100

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h3 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-3">
        {t('accessTier', 'Access Tier')}
      </h3>

      <div className="flex items-center space-x-3 mb-2">
        <span
          className={cn(
            'text-sm font-medium px-2.5 py-1 rounded border',
            tierStyles[tierName] || tierStyles.free
          )}
        >
          {tier.label}
        </span>
        <span className="text-xs text-gray-500">{tier.access}</span>
      </div>

      <div className="mt-3 text-xs text-gray-600 space-y-1">
        <div className="flex justify-between">
          <span>{t('tierFree', 'Free (<10 credits)')}</span>
          <span>{tierAccess.free}</span>
        </div>
        <div className="flex justify-between">
          <span>{t('tierContributor', 'Contributor (\u226510)')}</span>
          <span>{tierAccess.contributor}</span>
        </div>
        <div className="flex justify-between">
          <span>{t('tierPower', 'Power Seeder (\u226550)')}</span>
          <span>{tierAccess.power}</span>
        </div>
      </div>

      {nextTierThreshold && (
        <div className="mt-3">
          <div className="flex justify-between text-[10px] text-gray-600 mb-1">
            <span>
              {tier.balance} {t('credits', 'credits')}
            </span>
            <span>
              {nextTierThreshold} {t('forNextTier', 'for next tier')}
            </span>
          </div>
          <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-spore rounded-full transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
