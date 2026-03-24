import { useTranslation } from 'react-i18next'
import { Zap, Copy, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNodeStore } from '@/stores/node'
import { useClipboard } from '@/hooks/useClipboard'

export function ApiEndpointCard() {
  const { t } = useTranslation('overview')
  const status = useNodeStore((s) => s.status)
  const { copy, copied } = useClipboard()

  const port = status?.api_port || 8420
  const baseUrl = `${window.location.protocol}//${window.location.hostname}:${port}`
  const endpointUrl = `${baseUrl}/v1/chat/completions`

  return (
    <div className="bg-surface border border-white/10 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-ledger" />
          <h3 className="text-sm font-medium text-white">
            {t('apiEndpoint', 'API Endpoint')}
          </h3>
          <span className="text-xs px-1.5 py-0.5 rounded bg-spore/10 text-spore">
            OpenAI-compatible
          </span>
        </div>
        <button
          onClick={() => copy(endpointUrl)}
          className={cn(
            'text-xs flex items-center gap-1 transition-colors',
            copied
              ? 'text-spore'
              : 'text-gray-500 hover:text-gray-300'
          )}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          <span>{copied ? t('copied', 'Copied') : t('copy', 'Copy')}</span>
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm font-mono">
        <div className="bg-black/50 rounded-lg p-3 space-y-1.5">
          <div className="text-xs text-gray-500">
            {t('baseUrl', 'Base URL')}
          </div>
          <div className="text-spore break-all">{baseUrl}/v1</div>
        </div>
        <div className="bg-black/50 rounded-lg p-3 space-y-1.5">
          <div className="text-xs text-gray-500">
            {t('endpoint', 'Endpoint')}
          </div>
          <div className="text-white break-all">/v1/chat/completions</div>
        </div>
      </div>

      <p className="text-xs text-gray-600 mt-3">
        {t(
          'apiHint',
          'Drop-in replacement for OpenAI SDK. Set base_url to the URL above.'
        )}
      </p>
    </div>
  )
}
