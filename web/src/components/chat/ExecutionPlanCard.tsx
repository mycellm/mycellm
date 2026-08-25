import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, ShieldX, Workflow } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { ExecutionMeta } from '@/api/types'

/**
 * What actually ran, under a swarm answer.
 *
 * A swarm bills for N proposers plus a synthesis pass, so "it worked" is not
 * an adequate report. Three things have to be visible without expanding:
 *
 *  - **degradation**, because a swarm that quietly became one call while
 *    still labelled "swarm" is unfalsifiable
 *  - **refusals**, because a target blocked by egress policy is otherwise
 *    indistinguishable from one that was never there
 *  - **spend**, because the token budget is a ceiling the caller set
 *
 * Everything else is behind the disclosure.
 */
export function ExecutionPlanCard({ plan }: { plan: ExecutionMeta }) {
  const { t } = useTranslation('chat')
  const [open, setOpen] = useState(false)

  const proposers = plan.units?.filter((u) => u.role === 'proposer') ?? []
  const refused = plan.rejected ?? []
  const degraded = Boolean(plan.degraded)

  return (
    <div
      className={cn(
        'mt-2 rounded-lg border bg-black/30 text-xs',
        degraded ? 'border-ledger/30' : 'border-white/10'
      )}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-gray-400 transition-colors hover:text-gray-200"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Workflow size={12} className="text-relay" />
        <span className="font-medium uppercase tracking-wide">{plan.strategy}</span>
        {proposers.length > 0 && (
          <span className="text-gray-500">
            {t('plan.proposers', { count: proposers.length })}
          </span>
        )}
        {typeof plan.units_failed === 'number' && plan.units_failed > 0 && (
          <span className="text-compute">
            {t('plan.failed', { count: plan.units_failed })}
          </span>
        )}
        {refused.length > 0 && (
          <span className="flex items-center gap-1 text-poison">
            <ShieldX size={11} />
            {t('plan.refused', { count: refused.length })}
          </span>
        )}
        {degraded && (
          <span className="flex items-center gap-1 text-ledger">
            <AlertTriangle size={11} />
            {t('plan.degraded')}
          </span>
        )}
        {typeof plan.elapsed_s === 'number' && (
          <span className="ml-auto font-mono text-gray-600">{plan.elapsed_s}s</span>
        )}
      </button>

      {open && (
        <div className="space-y-3 border-t border-white/10 px-3 py-2.5">
          {plan.degradation && (
            <div className="rounded border border-ledger/20 bg-ledger/5 px-2 py-1.5 text-ledger">
              {plan.degradation}
            </div>
          )}

          {plan.units?.length > 0 && (
            <div>
              <div className="mb-1 text-gray-500">{t('plan.units')}</div>
              <ul className="space-y-0.5 font-mono text-[11px] text-gray-400">
                {plan.units.map((u) => (
                  <li key={u.unit_id} className="flex gap-2">
                    <span className="w-20 shrink-0 text-relay">{u.role}</span>
                    <span className="truncate">{u.target}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {refused.length > 0 && (
            <div>
              <div className="mb-1 flex items-center gap-1 text-poison">
                <ShieldX size={11} />
                {t('plan.refusedTitle')}
              </div>
              <ul className="space-y-0.5 text-[11px] text-gray-400">
                {refused.map((r) => (
                  <li key={r.target} className="flex gap-2">
                    <span className="shrink-0 font-mono text-gray-500">{r.target}</span>
                    <span>— {r.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {plan.failures && plan.failures.length > 0 && (
            <div>
              <div className="mb-1 text-compute">{t('plan.failuresTitle')}</div>
              <ul className="space-y-0.5 text-[11px] text-gray-400">
                {plan.failures.map((f) => (
                  <li key={f.target} className="flex gap-2">
                    <span className="shrink-0 font-mono text-gray-500">{f.target}</span>
                    <span>— {f.error}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {plan.reasons?.length > 0 && (
            <div>
              <div className="mb-1 text-gray-500">{t('plan.reasons')}</div>
              <ul className="space-y-0.5 text-[11px] text-gray-400">
                {plan.reasons.map((r, i) => (
                  <li key={i}>· {r}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-white/5 pt-2 text-[11px] text-gray-500">
            {plan.synthesized_by && (
              <span>
                {t('plan.synthesizedBy')}:{' '}
                <span className="font-mono text-gray-400">{plan.synthesized_by}</span>
              </span>
            )}
            {plan.served_by && (
              <span>
                {t('plan.servedBy')}:{' '}
                <span className="font-mono text-gray-400">{plan.served_by}</span>
              </span>
            )}
            {typeof plan.completion_tokens_spent === 'number' && (
              <span>
                {t('plan.spent')}:{' '}
                <span className="font-mono text-gray-400">
                  {plan.completion_tokens_spent}
                  {plan.token_budget > 0 ? ` / ${plan.token_budget}` : ''}
                </span>
              </span>
            )}
            {typeof plan.cancelled_for_budget === 'number' && plan.cancelled_for_budget > 0 && (
              <span className="text-ledger">
                {t('plan.cancelledForBudget', { count: plan.cancelled_for_budget })}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
