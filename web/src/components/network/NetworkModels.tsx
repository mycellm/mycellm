import { useTranslation } from 'react-i18next'
import { Boxes } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useModels } from '@/hooks/useModels'
import { EmptyState } from '@/components/common/EmptyState'

const ownerStyles: Record<string, { icon: string; badge: string }> = {
  local: { icon: 'text-spore', badge: 'bg-spore/10 text-spore' },
}

function getOwnerStyle(ownedBy: string) {
  if (ownedBy === 'local') {
    return { icon: 'text-spore', badge: 'bg-spore/10 text-spore' }
  }
  if (ownedBy.startsWith('peer:')) {
    return { icon: 'text-relay', badge: 'bg-relay/10 text-relay' }
  }
  if (ownedBy.startsWith('fleet:')) {
    return { icon: 'text-ledger', badge: 'bg-ledger/10 text-ledger' }
  }
  return { icon: 'text-gray-500', badge: 'bg-white/5 text-gray-500' }
}

export function NetworkModels() {
  const { t } = useTranslation('network')
  const { models, isLoading } = useModels()

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
        {t('models.title', 'Models Across Network')}
      </h2>

      {models.length > 0 ? (
        <div className="space-y-2">
          {models.map((m) => {
            const style = getOwnerStyle(m.owned_by)
            return (
              <div
                key={m.id}
                className="flex items-center justify-between bg-black border border-white/5 p-3 rounded-lg text-sm"
              >
                <div className="flex items-center space-x-3 min-w-0">
                  <Boxes size={14} className={cn('shrink-0', style.icon)} />
                  <span className="font-mono truncate">{m.id}</span>
                </div>
                <span
                  className={cn(
                    'text-xs font-mono px-2 py-1 rounded shrink-0 ml-2',
                    style.badge
                  )}
                >
                  {m.owned_by}
                </span>
              </div>
            )
          })}
        </div>
      ) : (
        <EmptyState
          icon={Boxes}
          message={t('models.empty', 'No models available on the network.')}
        />
      )}
    </div>
  )
}
