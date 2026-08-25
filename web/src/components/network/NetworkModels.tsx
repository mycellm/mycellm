import { useTranslation } from 'react-i18next'
import { Boxes } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useModels } from '@/hooks/useModels'
import { locationStyle } from '@/lib/conventions'
import { EmptyState } from '@/components/common/EmptyState'

// ⚠️ THIS USED TO GIVE PEERS BLUE AND FLEET NODES GOLD — three colours for one
// question ("where does this run") while `blue` also meant MLX and `gold` also
// meant credits. Location is now one axis with two answers, matching iOS.
function getOwnerStyle(ownedBy: string) {
  const loc = locationStyle(ownedBy)
  return { icon: loc.text, badge: loc.badge }
}

export function NetworkModels() {
  const { t } = useTranslation('network')
  const { models } = useModels()

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
