import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Lock } from 'lucide-react'
import { useAuthStore } from '@/stores/auth'

export function AuthGate() {
  const { t } = useTranslation('common')
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const setAppState = useAuthStore((s) => s.setAppState)

  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const response = await fetch(`${window.location.origin}/health`, {
        headers: {
          Authorization: `Bearer ${key}`,
        },
      })

      if (!response.ok) {
        setError(t('auth.invalidKey', 'Invalid API key'))
        setLoading(false)
        return
      }

      setApiKey(key)
      setAppState('booting')
    } catch (err) {
      setError(t('auth.connectionError', 'Could not connect to node'))
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-void">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-surface border border-white/10 rounded-xl p-8 space-y-6"
      >
        {/* Logo */}
        <div className="flex flex-col items-center space-y-4">
          <img
            src="/brand/mycellm-h-R.svg"
            alt="mycellm"
            className="h-10"
          />
          <p className="text-gray-500 text-sm">
            {t('auth.subtitle', 'Enter your API key to access the dashboard')}
          </p>
        </div>

        {/* API Key input */}
        <div className="space-y-2">
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={t('auth.placeholder', 'API key')}
              autoFocus
              className="w-full bg-void border border-white/10 rounded-lg pl-10 pr-4 py-2.5
                text-sm text-console placeholder:text-gray-600
                focus:outline-none focus:border-spore/50 focus:ring-1 focus:ring-spore/20
                transition-colors"
            />
          </div>
        </div>

        {/* Error */}
        {error && (
          <p className="text-compute text-sm text-center">{error}</p>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading || !key.trim()}
          className="w-full bg-spore text-black font-semibold rounded-lg py-2.5 text-sm
            hover:bg-spore/90 active:bg-spore/80
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-colors"
        >
          {loading
            ? t('auth.connecting', 'Connecting...')
            : t('auth.submit', 'Connect')}
        </button>
      </form>
    </div>
  )
}
