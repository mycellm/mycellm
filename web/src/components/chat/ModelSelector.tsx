import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
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
