import { useTranslation } from 'react-i18next'
import { Settings, Check, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NodeConfig as NodeConfigType } from '@/api/types'

interface NodeConfigProps {
  config: NodeConfigType | null
}

/**
 * One label/value line.
 *
 * ⚠️ THE VALUE MUST BE ABLE TO SHRINK AND WRAP, AND IT COULD DO NEITHER.
 * This was `flex justify-between` with an unconstrained value, which is fine
 * for "Configured" or "SQLite" and wrong for anything long: a flex item's
 * default `min-width: auto` refuses to shrink below its content, so the value
 * grew past the row and printed over the label and the adjacent grid column.
 * Measured on a live node — between roughly 768px and 900px viewport (narrow,
 * but still in the two-column `md:` grid) the bootstrap peers value spilled up
 * to 44px past its row. Under 768px the grid collapses to one column, which is
 * why it looked intermittent.
 *
 * `min-w-0` restores shrinking; `break-all` lets an address break rather than
 * overflow; `shrink-0` keeps the label from being crushed instead.
 */
function ConfigRow({
  label,
  value,
  values,
  configured,
  warning,
}: {
  label: string
  value?: string
  /** Multi-valued fields render one per line — see `bootstrapPeers`. */
  values?: string[]
  configured?: boolean
  warning?: string
}) {
  const tone = cn(
    configured === true && 'text-spore',
    configured === false && (warning ? 'text-ledger' : 'text-gray-600'),
    configured === undefined && 'text-gray-300'
  )

  return (
    <div className="flex items-start justify-between gap-3">
      <span className="shrink-0 text-gray-500">{label}</span>
      <span className="flex min-w-0 items-start gap-1.5 font-mono">
        {configured !== undefined && (
          <span className="mt-0.5 shrink-0">
            {configured ? (
              <Check size={12} className="text-spore" />
            ) : (
              <AlertTriangle size={12} className="text-ledger" />
            )}
          </span>
        )}
        {values ? (
          // A list, not a comma-joined string. These are inherently several
          // addresses; concatenating them produced one long unbreakable-looking
          // run that no amount of row width was going to accommodate.
          <ul className={cn('min-w-0 space-y-0.5 text-right', tone)}>
            {values.map((v) => (
              <li key={v} className="break-all">
                {v}
              </li>
            ))}
          </ul>
        ) : (
          <span className={cn('min-w-0 break-all text-right', tone)}>
            {value}
            {warning && <span className="ml-1 text-xs text-gray-600">({warning})</span>}
          </span>
        )}
      </span>
    </div>
  )
}

export function NodeConfig({ config }: NodeConfigProps) {
  const { t } = useTranslation('settings')

  if (!config) return null

  // The node sends either a list or a comma-separated string, so normalise to
  // a list here rather than at the render site.
  const bootstrapPeers = (
    Array.isArray(config.bootstrap_peers)
      ? config.bootstrap_peers
      : String(config.bootstrap_peers || '').split(',')
  )
    .map((p) => p.trim())
    .filter(Boolean)

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
            {...(bootstrapPeers.length
              ? { values: bootstrapPeers }
              : { value: t('none', 'None') })}
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
