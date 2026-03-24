import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Lock, Key, Copy, Trash2, Eye, EyeOff, KeyRound } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { ResultBanner } from '@/components/common/ResultBanner'
import { EmptyState } from '@/components/common/EmptyState'

interface SecretsManagerProps {
  secrets: string[]
  onSecretsChange: (secrets: string[]) => void
}

export function SecretsManager({ secrets, onSecretsChange }: SecretsManagerProps) {
  const { t } = useTranslation('settings')
  const [newSecret, setNewSecret] = useState({ name: '', value: '' })
  const [showValue, setShowValue] = useState(false)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const dismissResult = () => setResult(null)

  const addSecret = async () => {
    if (!newSecret.name || !newSecret.value) return
    try {
      await api.post(API.node.secrets, {
        name: newSecret.name,
        value: newSecret.value,
      })
      onSecretsChange([...secrets.filter((n) => n !== newSecret.name), newSecret.name])
      setNewSecret({ name: '', value: '' })
      setResult({
        type: 'success',
        message: t('secretStored', `Secret '{{name}}' stored`, { name: newSecret.name }),
      })
      setTimeout(dismissResult, 3000)
    } catch (e) {
      setResult({
        type: 'error',
        message: e instanceof Error ? e.message : t('secretStoreFailed', 'Failed to store secret'),
      })
    }
  }

  const removeSecret = async (name: string) => {
    try {
      await api.delete(API.node.secrets, { name })
      onSecretsChange(secrets.filter((n) => n !== name))
    } catch {
      // Silent fail on delete
    }
  }

  const copyReference = (name: string) => {
    navigator.clipboard.writeText(`secret:${name}`)
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center space-x-2 mb-4">
        <Lock size={16} className="text-poison" />
        <h3 className="text-console font-medium text-sm">
          {t('encryptedSecrets', 'Encrypted Secrets')}
        </h3>
        <span className="text-xs text-gray-600">
          {t('secretsSubtitle', 'API keys encrypted at rest with account key')}
        </span>
      </div>

      {/* Existing secrets */}
      {secrets.length > 0 ? (
        <div className="space-y-1.5 mb-4">
          {secrets.map((name) => (
            <div
              key={name}
              className="flex items-center justify-between bg-black/40 border border-white/5 rounded-lg px-3 py-2"
            >
              <div className="flex items-center space-x-2">
                <Key size={12} className="text-ledger" />
                <span className="font-mono text-sm text-console">{name}</span>
                <button
                  onClick={() => copyReference(name)}
                  title={t('copyReference', 'Copy secret reference')}
                  className="text-gray-600 hover:text-gray-400 transition-colors"
                >
                  <Copy size={11} />
                </button>
              </div>
              <div className="flex items-center space-x-2">
                <span className="text-xs text-gray-600 font-mono hidden sm:inline">
                  secret:{name}
                </span>
                <button
                  onClick={() => removeSecret(name)}
                  className="text-gray-600 hover:text-compute transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={KeyRound}
          message={t('noSecrets', 'No secrets stored yet.')}
          className="py-6 mb-4"
        />
      )}

      {/* Add secret form */}
      <div className="flex flex-col sm:flex-row items-end gap-2">
        <div className="flex-1 w-full">
          <label className="text-xs text-gray-500 block mb-1">{t('name', 'Name')}</label>
          <input
            value={newSecret.name}
            onChange={(e) => setNewSecret((s) => ({ ...s, name: e.target.value }))}
            placeholder="openrouter"
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
            className={cn(
              'w-full bg-void border border-white/10 rounded-lg px-3 py-2',
              'text-sm font-mono text-console',
              'focus:border-spore/50 focus:outline-none transition-colors'
            )}
          />
        </div>
        <div className="flex-1 w-full">
          <label className="text-xs text-gray-500 block mb-1">{t('value', 'Value')}</label>
          <input
            type={showValue ? 'text' : 'password'}
            value={newSecret.value}
            onChange={(e) => setNewSecret((s) => ({ ...s, value: e.target.value }))}
            placeholder="sk-or-..."
            onKeyDown={(e) => e.key === 'Enter' && addSecret()}
            autoComplete="new-password"
            data-1p-ignore
            data-lpignore="true"
            className={cn(
              'w-full bg-void border border-white/10 rounded-lg px-3 py-2',
              'text-sm font-mono text-console',
              'focus:border-spore/50 focus:outline-none transition-colors'
            )}
          />
        </div>
        <button
          onClick={() => setShowValue(!showValue)}
          className="text-gray-500 hover:text-gray-300 pb-2 transition-colors"
        >
          {showValue ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
        <button
          onClick={addSecret}
          disabled={!newSecret.name || !newSecret.value}
          className={cn(
            'bg-white/10 text-console px-4 py-2 rounded-lg text-sm font-medium',
            'hover:bg-white/20 disabled:opacity-40 whitespace-nowrap transition-colors'
          )}
        >
          {t('storeSecret', 'Store Secret')}
        </button>
      </div>

      <p className="text-xs text-gray-600 mt-3">
        {t('secretsHint', 'Use in model configs as')}{' '}
        <code className="text-gray-400">secret:name</code>{' '}
        {t('secretsHintSuffix', 'instead of raw API keys.')}
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
