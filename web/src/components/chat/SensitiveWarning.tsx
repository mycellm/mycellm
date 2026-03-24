import { ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface SensitiveWarningProps {
  types: string[]
  onCancel: () => void
  onConfirm: () => void
}

export function SensitiveWarning({ types, onCancel, onConfirm }: SensitiveWarningProps) {
  const { t } = useTranslation('chat')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        className={cn(
          'mx-4 w-full max-w-md rounded-2xl border border-white/10 bg-void p-6',
          'shadow-2xl shadow-compute/10'
        )}
      >
        <div className="mb-4 flex items-center space-x-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-compute/10">
            <ShieldAlert size={20} className="text-compute" />
          </div>
          <h3 className="text-lg font-semibold text-white">
            {t('sensitive.title')}
          </h3>
        </div>

        <p className="mb-2 text-sm text-gray-300">
          {t('sensitive.message', { types: types.join(', ') })}
        </p>

        <div className="mt-2 rounded-lg border border-compute/20 bg-compute/5 px-3 py-2">
          <ul className="space-y-1">
            {types.map((type) => (
              <li key={type} className="flex items-center space-x-2 text-xs text-compute">
                <span className="h-1.5 w-1.5 rounded-full bg-compute" />
                <span>{type}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="mt-6 flex space-x-3">
          <button
            onClick={onCancel}
            className={cn(
              'flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5',
              'text-sm font-medium text-gray-300',
              'transition-colors hover:bg-white/10'
            )}
          >
            {t('sensitive.cancel')}
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              'flex-1 rounded-xl border border-compute/30 bg-compute/20 px-4 py-2.5',
              'text-sm font-medium text-compute',
              'transition-colors hover:bg-compute/30'
            )}
          >
            {t('sensitive.sendAnyway')}
          </button>
        </div>
      </div>
    </div>
  )
}
