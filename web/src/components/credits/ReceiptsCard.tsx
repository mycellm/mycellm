import { useTranslation } from 'react-i18next'
import { useCreditsStore } from '@/stores/credits'

export function ReceiptsCard() {
  const { t } = useTranslation('credits')
  const tier = useCreditsStore((s) => s.tier)

  if (!tier) return null

  const receipts = tier.receipts

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h3 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-3">
        {t('receipts.title', 'Receipts')}
      </h3>

      <div className="grid grid-cols-3 gap-3 text-center">
        <div>
          <div className="text-xl font-mono text-console">{receipts?.total ?? 0}</div>
          <div className="text-xs text-gray-500">{t('total', 'Total')}</div>
        </div>
        <div>
          <div className="text-xl font-mono text-spore">{receipts?.verified ?? 0}</div>
          <div className="text-xs text-gray-500">{t('verified', 'Verified')}</div>
        </div>
        <div>
          <div className="text-xl font-mono text-relay">{receipts?.fleet ?? 0}</div>
          <div className="text-xs text-gray-500">{t('fleet', 'Fleet')}</div>
        </div>
      </div>

      <p className="text-[10px] text-gray-600 mt-3">
        {t(
          'receiptsDescription',
          'Verified receipts are Ed25519-signed by seeder nodes. Fleet receipts are generated via HTTP proxy.'
        )}
      </p>
    </div>
  )
}
