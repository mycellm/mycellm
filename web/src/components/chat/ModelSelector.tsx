import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { TIERS, countAtTier, tierValue } from '@/lib/selection'
import type { Model } from '@/api/types'

interface ModelSelectorProps {
  models: Model[]
  selected: string
  onSelect: (model: string) => void
}

export function ModelSelector({ models, selected, onSelect }: ModelSelectorProps) {
  const { t } = useTranslation('chat')

  const localModels = models.filter((m) => m.owned_by === 'local')
  const fleetModels = models.filter((m) => m.owned_by?.startsWith('fleet:'))
  const peerModels = models.filter((m) => m.owned_by?.startsWith('peer:'))
  // Strategy models (`owned_by: 'mycellm'`) select an execution strategy
  // rather than a model. `auto` is already the empty option below, so only
  // the rest are listed. Without this group the node advertised
  // `mycellm/swarm` on /v1/models and the dashboard had no way to pick it —
  // the same advertised-but-unreachable shape 0.8 exists to remove.
  const strategyModels = models.filter((m) => m.owned_by === 'mycellm' && m.id !== 'auto')

  // Real models only: `auto` and `mycellm/swarm` have no parameter count, so
  // counting them toward a tier would claim a Frontier route that does not
  // exist.
  const concrete = models.filter((m) => m.owned_by !== 'mycellm')

  return (
    <select
      value={selected}
      onChange={(e) => onSelect(e.target.value)}
      className={cn(
        'min-w-[180px] rounded-lg border border-white/10 bg-black px-2.5 py-1.5',
        'font-mono text-sm text-white',
        'focus:border-spore/40 focus:outline-none',
        'transition-colors'
      )}
    >
      <option value="">{t('model.auto')}</option>

      {/* Quality floor. Still auto-selection — the node picks, but not below
          this tier. Sits above the named models because it is the choice most
          people actually want: "better than that" rather than "that one". */}
      <optgroup label={t('model.tier')}>
        {TIERS.map((tier) => {
          const n = countAtTier(concrete, tier)
          return (
            <option key={tier} value={tierValue(tier)}>
              {t(`tiers.${tier}`)}
              {/* An empty tier is offered but labelled, not hidden: the whole
                  point of a floor is to say what you want even when nothing
                  currently meets it. Hiding it would silently downgrade the
                  request to whatever happens to be online. */}
              {n === 0 ? ` — ${t('model.tierEmpty')}` : ` (${n})`}
            </option>
          )
        })}
      </optgroup>

      {strategyModels.length > 0 && (
        <optgroup label={t('model.strategies')}>
          {strategyModels.map((m) => (
            <option key={m.id} value={m.id} title={m.description}>
              {m.id}
            </option>
          ))}
        </optgroup>
      )}

      {localModels.length > 0 && (
        <optgroup label={`${t('model.local')} (${localModels.length})`}>
          {localModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.id}
            </option>
          ))}
        </optgroup>
      )}

      {fleetModels.length > 0 && (
        <optgroup label={`${t('model.fleet')} (${fleetModels.length})`}>
          {fleetModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} ({m.owned_by.replace('fleet:', '')})
            </option>
          ))}
        </optgroup>
      )}

      {peerModels.length > 0 && (
        <optgroup label={`${t('model.peers')} (${peerModels.length})`}>
          {peerModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} ({m.owned_by.replace('peer:', '')})
            </option>
          ))}
        </optgroup>
      )}
    </select>
  )
}
