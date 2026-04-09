import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, X } from 'lucide-react'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import type { SavedModel } from '@/api/types'

interface ModelEditPanelProps {
  model: SavedModel
  onClose: () => void
  onSave: () => void
}

interface EditForm {
  api_base: string
  api_key: string
  api_key_hint: string
  api_model: string
  ctx_len: number
  max_concurrent: number
  scope: string
}

export function ModelEditPanel({ model, onClose, onSave }: ModelEditPanelProps) {
  const { t } = useTranslation('models')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [form, setForm] = useState<EditForm>({
    api_base: model.api_base || '',
    api_key: '',
    api_key_hint: '',
    api_model: model.api_model || '',
    ctx_len: model.ctx_len || 4096,
    max_concurrent: model.max_concurrent || 32,
    scope: model.scope || 'home',
  })

  // Fetch config to get api_key_hint
  useState(() => {
    api.get<{ api_key_hint?: string; api_base?: string; api_model?: string; ctx_len?: number }>(
      API.models.config(model.name)
    ).then((config) => {
      setForm((f) => ({
        ...f,
        api_base: config.api_base || f.api_base,
        api_model: config.api_model || f.api_model,
        api_key_hint: config.api_key_hint || '',
        ctx_len: config.ctx_len || f.ctx_len,
      }))
    }).catch(() => {})
  })

  const handleSave = async () => {
    setLoading(true)
    setError('')
    try {
      const body: Record<string, unknown> = { model: model.name }
      if (form.api_base) body.api_base = form.api_base
      if (form.api_model) body.api_model = form.api_model
      if (form.api_key) body.api_key = form.api_key
      if (form.ctx_len) body.ctx_len = form.ctx_len
      if (form.scope) body.scope = form.scope

      const result = await api.post<{ error?: string }>(API.models.update, body)
      if (result?.error) {
        setError(result.error)
      } else {
        onSave()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    }
    setLoading(false)
  }

  const update = <K extends keyof EditForm>(key: K, value: EditForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  return (
    <div className="px-4 py-3 border-t border-spore/10 bg-black/30">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.apiBase', 'API Base URL')}
          </label>
          <input
            value={form.api_base}
            onChange={(e) => update('api_base', e.target.value)}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.upstreamModel', 'Upstream Model')}
          </label>
          <input
            value={form.api_model}
            onChange={(e) => update('api_model', e.target.value)}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.apiKey', 'API Key')}{' '}
            {form.api_key_hint && (
              <span className="text-gray-600">
                ({t('edit.current', 'current')}: {form.api_key_hint})
              </span>
            )}
          </label>
          <input
            type="password"
            value={form.api_key}
            onChange={(e) => update('api_key', e.target.value)}
            placeholder={t('edit.apiKeyPlaceholder', 'Leave empty to keep current key')}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.ctxLen', 'Context Length')}
          </label>
          <input
            type="number"
            value={form.ctx_len}
            onChange={(e) => update('ctx_len', parseInt(e.target.value) || 4096)}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.maxConcurrent', 'Max Concurrent')}
          </label>
          <input
            type="number"
            value={form.max_concurrent}
            onChange={(e) => update('max_concurrent', parseInt(e.target.value) || 32)}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-gray-500 block mb-0.5">
            {t('edit.scope', 'Scope')}
          </label>
          <select
            value={form.scope}
            onChange={(e) => update('scope', e.target.value)}
            className="w-full bg-black border border-white/10 rounded px-2 py-1.5 font-mono text-white focus:border-spore/50 focus:outline-none cursor-pointer"
          >
            <option value="home">{t('edit.scopeHome', 'Home (private)')}</option>
            <option value="public">{t('edit.scopePublic', 'Public (shared)')}</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="text-compute text-xs mt-2">{error}</div>
      )}

      <div className="flex items-center space-x-2 mt-3">
        <button
          onClick={handleSave}
          disabled={loading}
          className="bg-spore text-black px-3 py-1 rounded text-xs font-medium hover:bg-spore/90 disabled:opacity-40 inline-flex items-center gap-1"
        >
          <Save className="w-3 h-3" />
          {loading ? t('edit.saving', 'Saving...') : t('edit.saveReload', 'Save & Reload')}
        </button>
        <button
          onClick={onClose}
          className="text-xs text-gray-500 hover:text-white px-3 py-1 inline-flex items-center gap-1"
        >
          <X className="w-3 h-3" />
          {t('edit.cancel', 'Cancel')}
        </button>
      </div>
    </div>
  )
}
