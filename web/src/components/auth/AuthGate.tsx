import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth'

export function AuthGate() {
  const { t } = useTranslation('common')
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const setAppState = useAuthStore((s) => s.setAppState)

  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)

  const submit = async () => {
    if (!key.trim()) return
    setError('')
    setChecking(true)

    try {
      const response = await fetch(`${window.location.origin}/health`, {
        headers: { Authorization: `Bearer ${key}` },
      })

      if (!response.ok) {
        setError(t('auth.invalid', 'Invalid API key'))
        setChecking(false)
        return
      }

      setApiKey(key)
      setAppState('booting')
    } catch {
      setError(t('errors.networkError', 'Could not connect to node'))
      setChecking(false)
    }
  }

  return (
    <div className="min-h-screen bg-void text-console font-mono flex items-center justify-center p-6">
      <div className="max-w-sm w-full border border-white/10 bg-surface p-6 rounded-xl">
        <div className="mb-6">
          <img src="/brand/mycellm-h-R.svg" alt="mycellm" className="h-6" />
        </div>
        <p className="text-sm text-gray-400 mb-4">
          {t('auth.subtitle', 'This node requires an API key.')}
        </p>
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="MYCELLM_API_KEY"
          autoFocus
          className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none mb-3"
        />
        <button
          onClick={submit}
          disabled={checking || !key}
          className="w-full bg-spore text-black py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 transition-all"
        >
          {checking
            ? t('auth.checking', 'Checking...')
            : t('auth.submit', 'Authenticate')}
        </button>
        {error && <p className="text-xs text-compute mt-3">{error}</p>}
      </div>
    </div>
  )
}
