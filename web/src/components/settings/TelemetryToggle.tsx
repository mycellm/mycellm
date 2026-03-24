import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { ResultBanner } from '@/components/common/ResultBanner'

interface TelemetryToggleProps {
  enabled: boolean
  onToggle: (enabled: boolean) => void
}

export function TelemetryToggle({ enabled, onToggle }: TelemetryToggleProps) {
  const { t } = useTranslation('settings')
  const [error, setError] = useState<string | null>(null)

  const handleToggle = async () => {
    const next = !enabled
    try {
      const resp = await api.post<{ error?: string }>(API.node.telemetry, { enabled: next })
      if (resp?.error) {
        setError(resp.error)
        setTimeout(() => setError(null), 3000)
      } else {
        onToggle(next)
      }
    } catch (e) {
      setError(
        e instanceof Error
          ? `Toggle failed: ${e.message}`
          : t('toggleFailed', 'Toggle failed')
      )
      setTimeout(() => setError(null), 3000)
    }
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Activity size={16} className="text-spore" />
          <h3 className="text-console font-medium text-sm">
            {t('telemetry.title', 'Telemetry')}
          </h3>
        </div>
        <button
          onClick={handleToggle}
          className={cn(
            'relative w-10 h-5 rounded-full transition-colors',
            enabled ? 'bg-spore' : 'bg-white/10'
          )}
          aria-label={t('toggleTelemetry', 'Toggle telemetry')}
        >
          <div
            className={cn(
              'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
              enabled ? 'translate-x-5' : 'translate-x-0.5'
            )}
          />
        </button>
      </div>

      <p className="text-xs text-gray-500 mt-2">
        {t(
          'telemetryDescription',
          'Share anonymous usage stats (request counts, TPS, model names, uptime) with the network bootstrap. No prompts, IPs, or user data. Helps the public stats page show real network-wide activity.'
        )}
      </p>

      <p className="text-xs text-gray-600 mt-1">
        {t('status', 'Status')}:{' '}
        <span className={enabled ? 'text-spore' : 'text-gray-500'}>
          {enabled ? t('enabled', 'Enabled') : t('disabled', 'Disabled')}
        </span>
        {enabled && (
          <span className="text-gray-600">
            {' '}
            {t('telemetryInterval', '-- stats included in bootstrap announce every 60s')}
          </span>
        )}
      </p>

      {error && (
        <ResultBanner
          type="error"
          message={error}
          onDismiss={() => setError(null)}
          className="mt-2"
        />
      )}
    </div>
  )
}
