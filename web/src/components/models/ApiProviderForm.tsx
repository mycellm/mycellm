import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Eye, EyeOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { ResultBanner } from '@/components/common/ResultBanner'

interface ApiProviderFormProps {
  selectedDevice: string
}

interface Preset {
  label: string
  backend: string
  api_base: string
  placeholder_model: string
}

const PRESETS: Preset[] = [
  {
    label: 'OpenAI',
    backend: 'openai',
    api_base: 'https://api.openai.com/v1',
    placeholder_model: 'gpt-4o',
  },
  {
    label: 'Anthropic',
    backend: 'openai',
    api_base: 'https://api.anthropic.com/v1',
    placeholder_model: 'claude-sonnet-4-20250514',
  },
  {
    label: 'OpenRouter',
    backend: 'openai',
    api_base: 'https://openrouter.ai/api/v1',
    placeholder_model: 'anthropic/claude-sonnet-4',
  },
  {
    label: 'Ollama',
    backend: 'openai',
    api_base: 'http://localhost:11434/v1',
    placeholder_model: 'llama3',
  },
  {
    label: 'LM Studio',
    backend: 'openai',
    api_base: 'http://localhost:1234/v1',
    placeholder_model: 'local-model',
  },
]

interface FormState {
  name: string
  backend: string
  api_base: string
  api_key: string
  api_model: string
  ctx_len: number
  max_concurrent: number
}

export function ApiProviderForm({ selectedDevice }: ApiProviderFormProps) {
  const { t } = useTranslation('models')
  const [loading, setLoading] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [storedSecrets, setStoredSecrets] = useState<string[]>([])
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const [form, setForm] = useState<FormState>({
    name: '',
    backend: 'openai',
    api_base: 'https://openrouter.ai/api/v1',
    api_key: '',
    api_model: '',
    ctx_len: 4096,
    max_concurrent: 32,
  })

  const isRemote = selectedDevice !== ''

  const nodePost = useCallback(
    <T,>(path: string, body?: unknown): Promise<T> =>
      isRemote
        ? api.remote<T>(selectedDevice, path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
          })
        : api.post<T>(path, body),
    [isRemote, selectedDevice]
  )

  // Fetch stored secrets
  useEffect(() => {
    api.get<{ secrets?: string[] }>(API.node.secrets)
      .then((d) => setStoredSecrets(d.secrets || []))
      .catch(() => {})
  }, [])

  const applyPreset = (preset: Preset) => {
    setForm((f) => ({
      ...f,
      backend: preset.backend,
      api_base: preset.api_base,
    }))
  }

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const handleConnect = async () => {
    if (!form.name || !form.api_base) return
    setLoading(true)
    setResult(null)
    try {
      const data = await nodePost<{ error?: string; success?: string }>(API.models.load, {
        name: form.name,
        backend: form.backend,
        api_base: form.api_base,
        api_key: form.api_key,
        api_model: form.api_model || form.name,
        ctx_len: form.ctx_len || 4096,
        max_concurrent: form.max_concurrent || 32,
      })
      if (data.error) {
        setResult({ type: 'error', message: data.error })
      } else {
        setResult({ type: 'success', message: data.success || 'API provider connected' })
        setForm((f) => ({ ...f, name: '', api_key: '', api_model: '' }))
      }
    } catch (e) {
      setResult({ type: 'error', message: e instanceof Error ? e.message : 'Connection failed' })
    }
    setLoading(false)
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        {t(
          'apiProvider.desc',
          'Connect to a cloud or self-hosted API. You choose the model and provide credentials.'
        )}{' '}
        <span className="text-gray-600">
          OpenRouter, Together, Groq, vLLM...
        </span>
      </p>

      {/* Preset buttons */}
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => applyPreset(p)}
            className={cn(
              'text-xs px-2.5 py-1 rounded border transition-colors',
              form.api_base === p.api_base
                ? 'border-spore/30 text-spore bg-spore/5'
                : 'border-white/10 text-gray-500 hover:text-gray-300 hover:border-white/20'
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.modelName', 'Model name')}
          </label>
          <input
            value={form.name}
            onChange={(e) => update('name', e.target.value)}
            placeholder="claude-sonnet"
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.apiBase', 'API Base URL')}
          </label>
          <input
            value={form.api_base}
            onChange={(e) => update('api_base', e.target.value)}
            placeholder="https://openrouter.ai/api/v1"
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.apiKey', 'API Key')}
          </label>
          <div className="flex space-x-1">
            <div className="relative flex-1">
              <input
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(e) => update('api_key', e.target.value)}
                placeholder={
                  storedSecrets.length
                    ? t('apiProvider.apiKeyPlaceholderSecrets', 'sk-... or select secret')
                    : 'sk-...'
                }
                className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 pr-8 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
              />
              <button
                onClick={() => setShowKey((s) => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            {storedSecrets.length > 0 && (
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value) update('api_key', `secret:${e.target.value}`)
                }}
                className="bg-black border border-white/10 rounded-lg px-2 py-2 text-sm font-mono text-gray-400 focus:outline-none cursor-pointer"
              >
                <option value="">secret</option>
                {storedSecrets.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.upstreamModel', 'Upstream model')}
          </label>
          <input
            value={form.api_model}
            onChange={(e) => update('api_model', e.target.value)}
            placeholder="anthropic/claude-sonnet-4"
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.ctxLen', 'Context length')}
          </label>
          <input
            type="number"
            value={form.ctx_len}
            onChange={(e) => update('ctx_len', parseInt(e.target.value) || 4096)}
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('apiProvider.maxConcurrent', 'Max concurrent')}
          </label>
          <input
            type="number"
            value={form.max_concurrent}
            onChange={(e) => update('max_concurrent', parseInt(e.target.value) || 32)}
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
      </div>

      <button
        onClick={handleConnect}
        disabled={loading || !form.name || !form.api_base}
        className="bg-white/10 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-white/20 disabled:opacity-40 inline-flex items-center gap-1.5"
      >
        <Link className="w-3.5 h-3.5" />
        {loading
          ? t('apiProvider.connecting', 'Connecting...')
          : t('apiProvider.connect', 'Connect API')}
      </button>

      {result && (
        <ResultBanner
          type={result.type}
          message={result.message}
          onDismiss={() => setResult(null)}
        />
      )}
    </div>
  )
}
