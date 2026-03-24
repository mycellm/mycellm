import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, RefreshCw, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { ResultBanner } from '@/components/common/ResultBanner'
import type { Relay } from '@/api/types'

interface RelayPanelProps {
  selectedDevice: string
}

export function RelayPanel({ selectedDevice }: RelayPanelProps) {
  const { t } = useTranslation('models')
  const [relays, setRelays] = useState<Relay[]>([])
  const [newUrl, setNewUrl] = useState('')
  const [newName, setNewName] = useState('')
  const [adding, setAdding] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const fetchRelays = () => {
    api.get<{ relays?: Relay[] }>(API.node.relay)
      .then((d) => setRelays(d.relays || []))
      .catch(() => {})
  }

  useEffect(() => {
    fetchRelays()
    const iv = setInterval(fetchRelays, 5000)
    return () => clearInterval(iv)
  }, [])

  const addRelay = async () => {
    if (!newUrl.trim()) return
    setAdding(true)
    setResult(null)
    try {
      const resp = await api.post<{
        error?: string
        relay?: Relay
      }>(API.node.relayAdd, {
        url: newUrl.trim(),
        name: newName.trim() || undefined,
      })
      if (resp.error) {
        setResult({ type: 'error', message: resp.error })
      } else {
        const relay = resp.relay
        if (relay?.online) {
          setResult({
            type: 'success',
            message: `Connected to ${relay.name} -- ${relay.model_count || 0} model(s) discovered`,
          })
        } else {
          setResult({
            type: 'error',
            message: `Added but offline: ${relay?.error || 'cannot connect'}`,
          })
        }
        setNewUrl('')
        setNewName('')
        fetchRelays()
      }
    } catch (e) {
      setResult({ type: 'error', message: e instanceof Error ? e.message : 'Failed to add relay' })
    }
    setAdding(false)
    setTimeout(() => setResult(null), 5000)
  }

  const removeRelay = async (url: string) => {
    try {
      await api.post(API.node.relayRemove, { url })
      fetchRelays()
    } catch {}
  }

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      const resp = await api.post<{ models_discovered?: number }>(
        API.node.relayRefresh,
        {}
      )
      setResult({
        type: 'success',
        message: `Discovered ${resp.models_discovered || 0} new model(s)`,
      })
      fetchRelays()
    } catch (e) {
      setResult({ type: 'error', message: e instanceof Error ? e.message : 'Refresh failed' })
    }
    setRefreshing(false)
    setTimeout(() => setResult(null), 4000)
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        {t(
          'relay.desc',
          'Connect to a device on your network. Models are auto-discovered -- no credentials needed.'
        )}{' '}
        <span className="text-gray-600">
          Ollama, LM Studio, iPad, llama.cpp server...
        </span>
      </p>

      {/* Existing relays */}
      {relays.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500 font-mono uppercase tracking-wider">
              {relays.length} relay{relays.length !== 1 ? 's' : ''}{' '}
              {t('relay.connected', 'connected')}
            </span>
            <button
              onClick={refreshAll}
              disabled={refreshing}
              className="flex items-center space-x-1 text-xs text-gray-500 hover:text-white transition-colors disabled:opacity-40"
            >
              <RefreshCw
                className={cn('w-3 h-3', refreshing && 'animate-spin')}
              />
              <span>{t('relay.refreshAll', 'Refresh all')}</span>
            </button>
          </div>
          {relays.map((r) => (
            <div
              key={r.url}
              className="bg-black/40 border border-white/5 rounded-lg px-4 py-3"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2.5">
                  <div
                    className={cn(
                      'w-2 h-2 rounded-full flex-shrink-0',
                      r.online ? 'bg-spore' : 'bg-compute animate-pulse'
                    )}
                  />
                  <span className="font-mono text-sm text-white font-medium">
                    {r.name}
                  </span>
                  <span className="text-xs text-gray-600 font-mono">{r.url}</span>
                </div>
                <div className="flex items-center space-x-3">
                  <span
                    className={cn(
                      'text-xs',
                      r.online ? 'text-gray-500' : 'text-compute'
                    )}
                  >
                    {r.online
                      ? `${r.model_count} model${r.model_count !== 1 ? 's' : ''}`
                      : r.error || t('relay.offline', 'offline')}
                  </span>
                  <button
                    onClick={() => removeRelay(r.url)}
                    className="text-gray-600 hover:text-compute transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {r.online && r.models?.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2.5">
                  {r.models.map((m) => (
                    <span
                      key={m}
                      className="text-xs font-mono bg-spore/5 text-spore border border-spore/10 rounded px-2 py-0.5"
                    >
                      relay:{m}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add relay form */}
      <div className="flex items-end gap-2">
        <div className="flex-[2]">
          <label className="text-xs text-gray-500 block mb-1">
            {t('relay.deviceUrl', 'Device URL')}
          </label>
          <input
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            placeholder="http://ipad.lan:8080"
            onKeyDown={(e) => e.key === 'Enter' && addRelay()}
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div className="flex-1">
          <label className="text-xs text-gray-500 block mb-1">
            {t('relay.label', 'Label')}{' '}
            <span className="text-gray-700">
              ({t('relay.optional', 'optional')})
            </span>
          </label>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="iPad Pro"
            onKeyDown={(e) => e.key === 'Enter' && addRelay()}
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <button
          onClick={addRelay}
          disabled={adding || !newUrl.trim()}
          className="bg-white/10 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-white/20 disabled:opacity-40 whitespace-nowrap flex items-center space-x-1.5"
        >
          {adding ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Plus className="w-3.5 h-3.5" />
          )}
          <span>
            {adding
              ? t('relay.connecting', 'Connecting...')
              : t('relay.addRelay', 'Add Relay')}
          </span>
        </button>
      </div>

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
