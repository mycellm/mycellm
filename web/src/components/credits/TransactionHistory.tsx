import { useTranslation } from 'react-i18next'
import { Receipt } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCreditsStore } from '@/stores/credits'
import { EmptyState } from '@/components/common/EmptyState'

function formatTimestamp(ts: string | number): string {
  const date = new Date(typeof ts === 'number' ? ts * 1000 : ts)
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatReason(reason: string): string {
  return reason.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

export function TransactionHistory() {
  const { t } = useTranslation('credits')
  const history = useCreditsStore((s) => s.history)

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
        {t('transactionHistory', 'Transaction History')}
      </h2>

      {history.length > 0 ? (
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto custom-scrollbar">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface z-10">
              <tr className="text-xs text-gray-500 font-mono uppercase">
                <th className="text-left py-2 pr-4 w-8" />
                <th className="text-left py-2 pr-4">{t('time', 'Time')}</th>
                <th className="text-left py-2 pr-4">{t('type', 'Type')}</th>
                <th className="text-left py-2 pr-4 hidden md:table-cell">
                  {t('counterparty', 'Counterparty')}
                </th>
                <th className="text-right py-2">{t('amount', 'Amount')}</th>
              </tr>
            </thead>
            <tbody>
              {history.map((tx, i) => {
                const isCredit = tx.direction === 'credit'
                const ts = tx.timestamp ? formatTimestamp(tx.timestamp) : ''
                const reason = formatReason(tx.reason || '')
                const counterparty = tx.counterparty_id
                  ? `${tx.counterparty_id.slice(0, 8)}...`
                  : ''

                return (
                  <tr
                    key={i}
                    className="border-t border-white/5 hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="py-2 pr-2">
                      <div
                        className={cn(
                          'w-1.5 h-1.5 rounded-full',
                          isCredit ? 'bg-spore' : 'bg-compute'
                        )}
                      />
                    </td>
                    <td className="py-2 pr-4 text-gray-500 font-mono text-xs whitespace-nowrap">
                      {ts}
                    </td>
                    <td className="py-2 pr-4 text-gray-300 whitespace-nowrap">{reason}</td>
                    <td className="py-2 pr-4 text-gray-500 font-mono text-xs hidden md:table-cell">
                      {counterparty}
                    </td>
                    <td
                      className={cn(
                        'py-2 text-right font-mono whitespace-nowrap',
                        isCredit ? 'text-spore' : 'text-compute'
                      )}
                    >
                      {isCredit ? '+' : '-'}
                      {tx.amount?.toFixed(4)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon={Receipt} message={t('noTransactions', 'No transactions yet.')} />
      )}
    </div>
  )
}
