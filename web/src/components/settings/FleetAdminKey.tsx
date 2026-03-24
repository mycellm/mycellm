import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settings'
import { ResultBanner } from '@/components/common/ResultBanner'

export function FleetAdminKey() {
  const { t } = useTranslation('settings')
  const fleetAdminKey = useSettingsStore((s) => s.fleetAdminKey)
  const setFleetAdminKey = useSettingsStore((s) => s.setFleetAdminKey)
  const [localKey, setLocalKey] = useState(fleetAdminKey)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const dismissResult = () => setResult(null)

  const handleSave = useCallback(() => {
    setFleetAdminKey(localKey)
    setResult({
      type: 'success',
      message: localKey
        ? t('fleetKeySaved', 'Fleet admin key saved')
        : t('fleetKeyCleared', 'Fleet admin key cleared'),
    })
    setTimeout(dismissResult, 3000)
  }, [localKey, setFleetAdminKey, t])

  const handleClear = useCallback(() => {
    setLocalKey('')
    setFleetAdminKey('')
    setResult({
      type: 'success',
      message: t('fleetKeyCleared', 'Fleet admin key cleared'),
    })
    setTimeout(dismissResult, 3000)
  }, [setFleetAdminKey, t])

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center space-x-2 mb-4">
        <Shield size={16} className="text-ledger" />
        <h3 className="text-console font-medium text-sm">
          {t('fleetAdminKey', 'Fleet Admin Key')}
        </h3>
        <span className="text-xs text-gray-600">
          {t('fleetAdminKeySubtitle', 'Manage remote fleet nodes via QUIC relay')}
        </span>
      </div>

      <div className="flex flex-col sm:flex-row items-end gap-2">
        <div className="flex-1 w-full">
          <input
            type="password"
            value={localKey}
            onChange={(e) => setLocalKey(e.target.value)}
            placeholder={t('enterFleetKey', 'Enter fleet admin key...')}
            className={cn(
              'w-full bg-void border border-white/10 rounded-lg px-3 py-2',
              'text-sm font-mono text-console',
              'focus:border-ledger/50 focus:outline-none transition-colors'
            )}
          />
        </div>
        <button
          onClick={handleSave}
          className={cn(
            'bg-white/10 text-console px-4 py-2 rounded-lg text-sm font-medium',
            'hover:bg-white/20 transition-colors whitespace-nowrap'
          )}
        >
          {t('save', 'Save')}
        </button>
        <button
          onClick={handleClear}
          className="text-gray-500 hover:text-gray-300 pb-2 transition-colors"
          title={t('clearFleetKey', 'Clear fleet admin key')}
        >
          <X size={14} />
        </button>
      </div>

      <p className="text-xs text-gray-600 mt-3">
        {t(
          'fleetAdminKeyNote',
          "This key is never sent to the bootstrap -- it's included in fleet command payloads and verified on each node."
        )}{' '}
        {t('fleetAdminKeyNote2', 'Nodes must have')}{' '}
        <code className="text-gray-400">MYCELLM_FLEET_ADMIN_KEY</code>{' '}
        {t('fleetAdminKeyNote3', 'set to accept commands.')}
      </p>

      {result && (
        <ResultBanner
          type={result.type}
          message={result.message}
          onDismiss={dismissResult}
          className="mt-2"
        />
      )}
    </div>
  )
}
