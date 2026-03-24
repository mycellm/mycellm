import { useTranslation } from 'react-i18next'
import { Settings, Check, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NodeConfig as NodeConfigType } from '@/api/types'

interface NodeConfigProps {
  config: NodeConfigType | null
}

function ConfigRow({
  label,
  value,
  configured,
  warning,
}: {
  label: string
  value: string
  configured?: boolean
  warning?: string
}) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-gray-500">{label}</span>
      <span className="flex items-center gap-1.5 font-mono">
        {configured !== undefined &&
          (configured ? (
            <Check size={12} className="text-spore" />
          ) : (
            <AlertTriangle size={12} className="text-ledger" />
          ))}
        <span
          className={cn(
            configured === true && 'text-spore',
            configured === false && (warning ? 'text-ledger' : 'text-gray-600'),
            configured === undefined && 'text-gray-300'
          )}
        >
          {value}
          {warning && <span className="text-gray-600 text-xs ml-1">({warning})</span>}
        </span>
      </span>
    </div>
  )
}

export function NodeConfig({ config }: NodeConfigProps) {
  const { t } = useTranslation('settings')

  if (!config) return null

  const bootstrapPeers = Array.isArray(config.bootstrap_peers)
    ? config.bootstrap_peers.join(', ') || 'None'
    : String(config.bootstrap_peers || 'None')

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center space-x-2 mb-4">
        <Settings size={16} className="text-relay" />
        <h3 className="text-console font-medium text-sm">
          {t('nodeConfiguration', 'Node Configuration')}
        </h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        <div className="space-y-2.5">
          <ConfigRow
            label={t('apiKey', 'API Key')}
            value={config.api_key_set ? t('configured', 'Configured') : t('notSet', 'Not set')}
            configured={config.api_key_set}
          />
          <ConfigRow
            label={t('bootstrapPeers', 'Bootstrap Peers')}
            value={bootstrapPeers}
          />
          <ConfigRow
            label={t('bootstrapActive', 'Bootstrap Active')}
            value={
              config.announce_task_alive
                ? t('announcing', 'Announcing')
                : t('inactive', 'Inactive')
            }
            configured={config.announce_task_alive}
          />
        </div>
        <div className="space-y-2.5">
          <ConfigRow
            label={t('hfToken', 'HuggingFace Token')}
            value={
              config.hf_token_set
                ? t('configured', 'Configured')
                : t('notSet', 'Not set')
            }
            configured={config.hf_token_set}
            warning={config.hf_token_set ? undefined : t('rateLimited', 'rate limited')}
          />
          <ConfigRow
            label={t('database', 'Database')}
            value={config.db_backend || 'SQLite'}
          />
          <ConfigRow
            label={t('logLevel', 'Log Level')}
            value={config.log_level || 'INFO'}
          />
        </div>
      </div>
    </div>
  )
}
