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

  const hostname = window.location.hostname

  const submit = async () => {
    if (!key.trim()) return
    setError('')
    setChecking(true)

    try {
      const response = await fetch(`${window.location.origin}/health`, {
        headers: { Authorization: `Bearer ${key}` },
      })

      if (response.status === 401) {
        setError('Invalid API key')
        setChecking(false)
        return
      }

      if (response.status === 429) {
        const data = await response.json().catch(() => null)
        setError(data?.message ?? 'Too many attempts. Try again later.')
        setChecking(false)
        return
      }

      if (!response.ok) {
        setError(`Server error (${response.status})`)
        setChecking(false)
        return
      }

      // Verify the key actually works on a protected endpoint
      const testResp = await fetch(`${window.location.origin}/v1/node/status`, {
        headers: { Authorization: `Bearer ${key}` },
      })

      if (testResp.status === 401) {
        setError('Invalid API key')
        setChecking(false)
        return
      }

      setApiKey(key)
      setAppState('booting')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      setError(`Cannot reach node: ${msg}`)
      setChecking(false)
    }
  }

  return (
    <div className="min-h-screen bg-void text-console font-mono flex items-center justify-center p-6">
      <div className="max-w-sm w-full border border-white/10 bg-surface p-6 rounded-xl">
        <div className="mb-6">
          <img src="/brand/mycellm-h-R.svg" alt="mycellm" className="h-6" />
        </div>
        <p className="text-sm text-gray-400 mb-1">
          {t('auth.subtitle', 'This node requires an API key.')}
        </p>
        <p className="text-xs text-gray-600 mb-4 font-mono">
          {hostname}
        </p>
        <input
          type="password"
          value={key}
          onChange={(e) => { setKey(e.target.value); setError('') }}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="MYCELLM_API_KEY"
          autoFocus
          className={`w-full bg-black border rounded-lg px-3 py-2 text-sm font-mono text-white focus:outline-none mb-3 transition-colors ${
            error
              ? 'border-compute/50 focus:border-compute/70'
              : 'border-white/10 focus:border-spore/50'
          }`}
        />
        <button
          onClick={submit}
          disabled={checking || !key}
          className="w-full bg-spore text-black py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 transition-all"
        >
          {checking ? 'Checking...' : 'Authenticate'}
        </button>
        {error && (
          <div className="mt-3 px-3 py-2 rounded-lg bg-compute/5 border border-compute/20">
            <p className="text-xs text-compute">{error}</p>
          </div>
        )}
      </div>
    </div>
  )
}
