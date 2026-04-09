import { useState, useEffect, Fragment, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Pencil, Trash2, Play, Square, RotateCw, X, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { logClientError } from '@/lib/logClientError'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { useModels } from '@/hooks/useModels'
import { EmptyState } from '@/components/common/EmptyState'
import { ModelEditPanel } from './ModelEditPanel'
import type { SavedModel } from '@/api/types'

interface ModelTableProps {
  selectedDevice: string
}

type ModelState = 'active' | 'loading' | 'on-disk' | 'disabled' | 'failed'

interface MergedModel {
  name: string
  state: ModelState
  backend: string
  quant: string
  size: string
  ctx: number
  scope: string
  hasFile: boolean
  filename?: string
  filePath?: string
  progress?: number
  eta_seconds?: number
  phase?: string
  error?: string
  elapsed?: number
}

interface LoadStatus {
  model: string
  status: string
  backend?: string
  phase?: string
  error?: string
  elapsed?: number
  progress?: number
  eta_seconds?: number
  size_gb?: number
}

interface LocalFile {
  model_name: string
  filename: string
  path: string
  size_gb: number
  quant?: string
  ctx_len?: number
}

const STATE_ORDER: Record<string, number> = {
  loading: 0,
  active: 1,
  failed: 2,
  'on-disk': 3,
  disabled: 4,
}

function StateIndicator({ state }: { state: ModelState }) {
  switch (state) {
    case 'active':
      return (
        <svg width="10" height="10" className="inline-block">
          <circle cx="5" cy="5" r="4" fill="#22C55E" />
        </svg>
      )
    case 'loading':
      return (
        <svg width="10" height="10" className="inline-block animate-pulse">
          <polygon points="5,1 9,9 1,9" fill="#FACC15" />
        </svg>
      )
    case 'on-disk':
      return (
        <svg width="10" height="10" className="inline-block">
          <rect x="1" y="1" width="8" height="8" fill="#666" rx="1" />
        </svg>
      )
    case 'disabled':
      return (
        <svg width="10" height="10" className="inline-block">
          <polygon points="5,0 10,5 5,10 0,5" fill="none" stroke="#666" strokeWidth="1.5" />
        </svg>
      )
    case 'failed':
      return (
        <svg width="10" height="10" className="inline-block">
          <line x1="2" y1="2" x2="8" y2="8" stroke="#EF4444" strokeWidth="2" />
          <line x1="8" y1="2" x2="2" y2="8" stroke="#EF4444" strokeWidth="2" />
        </svg>
      )
    default:
      return null
  }
}

function StateBadge({ state, t }: { state: ModelState; t: (k: string, d: string) => string }) {
  const styles: Record<ModelState, string> = {
    active: 'bg-spore/10 text-spore',
    loading: 'bg-ledger/10 text-ledger animate-pulse',
    'on-disk': 'bg-white/5 text-gray-500',
    disabled: 'bg-white/5 text-gray-500',
    failed: 'bg-compute/10 text-compute',
  }
  const labels: Record<ModelState, string> = {
    active: t('state.active', 'active'),
    loading: t('state.loading', 'loading'),
    'on-disk': t('state.onDisk', 'on disk'),
    disabled: t('state.disabled', 'disabled'),
    failed: t('state.failed', 'failed'),
  }
  return (
    <span className={cn('text-xs px-1.5 py-0.5 rounded', styles[state])}>
      {labels[state]}
    </span>
  )
}

const STATE_NAME_COLOR: Record<ModelState, string> = {
  active: 'text-white',
  loading: 'text-ledger',
  'on-disk': 'text-gray-400',
  disabled: 'text-gray-400',
  failed: 'text-compute',
}

export function ModelTable({ selectedDevice }: ModelTableProps) {
  const { t } = useTranslation('models')
  const { models, savedModels } = useModels()
  const [editingModel, setEditingModel] = useState<string | null>(null)
  const [localFiles, setLocalFiles] = useState<LocalFile[]>([])
  const [loadStatuses, setLoadStatuses] = useState<LoadStatus[]>([])
  const [savedConfigs, setSavedConfigs] = useState<SavedModel[]>([])

  const isRemote = selectedDevice !== ''

  // Fetch helpers
  const nodeGet = useCallback(
    <T,>(path: string): Promise<T> =>
      isRemote
        ? api.remote<T>(selectedDevice, path)
        : api.get<T>(path),
    [isRemote, selectedDevice]
  )

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

  // Fetch local files + saved configs (stable updates to avoid flicker)
  useEffect(() => {
    let lastFilesJson = ''
    let lastConfigsJson = ''
    const fetchData = () => {
      nodeGet<{ files?: LocalFile[] }>(API.models.localFiles)
        .then((d) => {
          const json = JSON.stringify(d.files || [])
          if (json !== lastFilesJson) { lastFilesJson = json; setLocalFiles(d.files || []) }
        })
        .catch(() => {})
      nodeGet<{ configs?: SavedModel[] }>(API.models.saved)
        .then((d) => {
          const json = JSON.stringify(d.configs || [])
          if (json !== lastConfigsJson) { lastConfigsJson = json; setSavedConfigs(d.configs || []) }
        })
        .catch(() => {})
    }
    fetchData()
    const iv = setInterval(fetchData, 5000)
    return () => clearInterval(iv)
  }, [nodeGet])

  // Poll load statuses (stable updates)
  useEffect(() => {
    let lastJson = ''
    const poll = () => {
      nodeGet<{ statuses?: LoadStatus[] }>(API.models.loadStatus)
        .then((d) => {
          const json = JSON.stringify(d.statuses || [])
          if (json !== lastJson) { lastJson = json; setLoadStatuses(d.statuses || []) }
        })
        .catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 2000)
    return () => clearInterval(iv)
  }, [nodeGet])

  // Merge all sources
  const merged = new Map<string, MergedModel>()

  // 1. Load statuses (loading/failed)
  for (const s of loadStatuses) {
    if (s.status === 'loading' || s.status === 'failed') {
      merged.set(s.model, {
        name: s.model,
        state: s.status as ModelState,
        backend: s.backend || 'llama.cpp',
        phase: s.phase,
        error: s.error,
        elapsed: s.elapsed,
        progress: s.progress || 0,
        eta_seconds: s.eta_seconds,
        quant: '',
        size: s.size_gb ? `${s.size_gb}GB` : '',
        ctx: 0,
        scope: 'home',
        hasFile: false,
      })
    }
  }

  // 2. Active loaded models (savedModels for local, savedConfigs for remote)
  const activeModels = (isRemote ? savedConfigs : savedModels).filter((m) => m.loaded)
  for (const m of activeModels) {
    if (merged.has(m.name) && merged.get(m.name)!.state === 'loading') continue
    const onDisk = localFiles.find((f) => f.model_name === m.name)
    merged.set(m.name, {
      name: m.name,
      state: 'active',
      backend: m.backend || 'llama.cpp',
      quant: m.quant || '',
      ctx: m.ctx_len || 4096,
      size: onDisk ? `${onDisk.size_gb}GB` : m.param_count_b ? `~${(m.param_count_b * 0.5).toFixed(1)}GB` : '',
      hasFile: !!onDisk,
      filename: onDisk?.filename,
      filePath: onDisk?.path,
      scope: m.scope || 'home',
    })
  }

  // Also include LOCAL models from the /v1/models endpoint that might not be in savedModels
  if (!isRemote) {
    for (const m of models) {
      // Only show models owned locally — fleet/peer models belong to their respective devices
      if (m.owned_by && m.owned_by !== 'local') continue
      if (!merged.has(m.id)) {
        const onDisk = localFiles.find((f) => f.model_name === m.id)
        merged.set(m.id, {
          name: m.id,
          state: 'active',
          backend: 'llama.cpp',
          quant: '',
          ctx: 4096,
          size: onDisk ? `${onDisk.size_gb}GB` : '',
          hasFile: !!onDisk,
          filename: onDisk?.filename,
          filePath: onDisk?.path,
          scope: 'home',
        })
      }
    }
  }

  // 3. On-disk GGUF files
  for (const f of localFiles) {
    if (!merged.has(f.model_name)) {
      merged.set(f.model_name, {
        name: f.model_name,
        state: 'on-disk',
        backend: 'llama.cpp',
        quant: f.quant || '',
        ctx: f.ctx_len || 0,
        size: `${f.size_gb}GB`,
        hasFile: true,
        filename: f.filename,
        filePath: f.path,
        scope: 'home',
      })
    }
  }

  // 4. Saved configs not yet in the table (unloaded API providers, or remote unloaded models)
  for (const c of savedConfigs) {
    if (!merged.has(c.name)) {
      merged.set(c.name, {
        name: c.name,
        state: 'disabled',
        backend: c.backend,
        quant: '',
        ctx: c.ctx_len || 4096,
        size: '',
        hasFile: false,
        scope: c.scope || 'home',
      })
    }
  }

  const allModels = [...merged.values()].sort(
    (a, b) => (STATE_ORDER[a.state] ?? 9) - (STATE_ORDER[b.state] ?? 9)
  )

  const activeCount = allModels.filter((m) => m.state === 'active').length
  const loadingCount = allModels.filter((m) => m.state === 'loading').length

  const refreshAll = () => {
    nodeGet<{ files?: LocalFile[] }>(API.models.localFiles)
      .then((d) => setLocalFiles(d.files || []))
      .catch(() => {})
    nodeGet<{ configs?: SavedModel[] }>(API.models.saved)
      .then((d) => setSavedConfigs(d.configs || []))
      .catch(() => {})
  }

  const handleLoad = async (m: MergedModel) => {
    try {
      await nodePost(API.models.load, {
        model_path: m.filePath,
        name: m.name,
        backend: 'llama.cpp',
        ctx_len: m.ctx || 4096,
      })
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: load model', error)
    }
  }

  const handleUnload = async (name: string) => {
    try {
      await nodePost(API.models.unload, { model: name })
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: unload model', error)
    }
  }

  const handleReload = async (name: string) => {
    try {
      await nodePost(API.models.reload, { model: name })
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: reload model', error)
    }
  }

  const handleDelete = async (m: MergedModel) => {
    if (m.hasFile && m.filename) {
      if (!confirm(t('table.confirmDeleteFile', `Delete ${m.filename}?`))) return
      try {
        await nodePost(API.models.deleteFile, { filename: m.filename })
        refreshAll()
      } catch (error) {
        logClientError('ModelTable: delete model file', error)
      }
    }
  }

  const handleRemoveConfig = async (name: string) => {
    if (!confirm(t('table.confirmRemove', `Remove config for ${name}?`))) return
    try {
      await nodePost(API.models.removeConfig, { model: name })
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: remove config', error)
    }
  }

  const handleDismiss = async (name: string) => {
    try {
      // Clear from load-status tracker (handles failed/stuck entries)
      await nodePost(API.models.clearLoadStatus, { model: name })
      // Also try removing saved config if it exists
      try {
        await nodePost(API.models.removeConfig, { model: name })
      } catch (error) {
        logClientError('ModelTable: remove config after clearLoadStatus', error)
      }
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: clear load status', error)
    }
  }

  const handleToggleScope = async (m: MergedModel) => {
    const next = m.scope === 'public' ? 'home' : 'public'
    try {
      await nodePost(API.models.scope, { model: m.name, scope: next })
      refreshAll()
    } catch (error) {
      logClientError('ModelTable: set model scope', error)
    }
  }

  if (allModels.length === 0) {
    return (
      <div className="border border-white/10 bg-surface rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-white/10">
          <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest">
            {t('table.title', 'Models')}
          </h2>
        </div>
        <EmptyState
          message={t('table.empty', 'No models on this node. Search HuggingFace below to get started.')}
        />
      </div>
    )
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest flex items-center space-x-3">
          <span>{t('table.title', 'Models')}</span>
          {activeCount > 0 && (
            <span className="text-spore">
              &#9679; {activeCount} {t('table.active', 'active')}
            </span>
          )}
          {loadingCount > 0 && (
            <span className="text-ledger animate-pulse">
              &#9650; {loadingCount} {t('table.loading', 'loading')}
            </span>
          )}
        </h2>
      </div>

      <table className="w-full text-sm">
        <thead className="bg-black/30">
          <tr className="text-xs text-gray-500 font-mono uppercase">
            <th className="text-left py-2 px-4 w-7" />
            <th className="text-left py-2 px-4">{t('table.colName', 'Name')}</th>
            <th className="text-left py-2 px-4 hidden md:table-cell">
              {t('table.colBackend', 'Backend')}
            </th>
            <th className="text-left py-2 px-4 hidden md:table-cell">
              {t('table.colQuant', 'Quant')}
            </th>
            <th className="text-left py-2 px-4 hidden md:table-cell">
              {t('table.colSize', 'Size')}
            </th>
            <th className="text-left py-2 px-4 hidden lg:table-cell">
              {t('table.colStatus', 'Status')}
            </th>
            <th className="text-right py-2 px-4">
              {t('table.colActions', 'Actions')}
            </th>
          </tr>
        </thead>
        <tbody>
          {allModels.map((m) => (
            <Fragment key={m.name}>
              <tr
                className={cn(
                  'border-t border-white/5 transition-all duration-300',
                  editingModel === m.name
                    ? 'bg-white/[0.05]'
                    : m.state === 'loading'
                    ? 'bg-ledger/[0.03]'
                    : m.state === 'failed'
                    ? 'bg-compute/[0.03]'
                    : m.state === 'active'
                    ? 'hover:bg-white/[0.02]'
                    : 'hover:bg-white/[0.02] opacity-70'
                )}
              >
                <td className="py-2.5 px-4" title={m.state}>
                  <StateIndicator state={m.state} />
                </td>
                <td className={cn('py-2.5 px-4 font-mono truncate max-w-[300px]', STATE_NAME_COLOR[m.state])} title={m.name}>
                  {m.name}
                  {m.state === 'loading' && (
                    <div className="mt-1 space-y-1">
                      {m.progress && m.progress > 0 && m.progress < 1 ? (
                        <div className="flex items-center space-x-2">
                          <div
                            className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden"
                            style={{ maxWidth: '160px' }}
                          >
                            <div
                              className="h-full bg-ledger rounded-full transition-all duration-500"
                              style={{ width: `${Math.round(m.progress * 100)}%` }}
                            />
                          </div>
                          <span className="text-xs text-ledger/70 font-mono">
                            {Math.round(m.progress * 100)}%
                          </span>
                          {m.eta_seconds != null && (
                            <span className="text-xs text-gray-600">
                              {m.eta_seconds > 60
                                ? `${Math.round(m.eta_seconds / 60)}m`
                                : `${Math.round(m.eta_seconds)}s`}{' '}
                              left
                            </span>
                          )}
                        </div>
                      ) : (
                        <div className="text-xs text-ledger/70 font-sans">
                          {m.phase || 'loading...'}
                          {m.elapsed ? ` \u00B7 ${m.elapsed}s` : ''}
                        </div>
                      )}
                    </div>
                  )}
                  {m.state === 'failed' && m.error && (
                    <div
                      className="text-xs text-compute/70 font-sans mt-0.5 truncate max-w-[250px]"
                      title={m.error}
                    >
                      {m.error}
                    </div>
                  )}
                </td>
                <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell">{m.backend}</td>
                <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell font-mono">
                  {m.quant || '-'}
                </td>
                <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell">
                  {m.size || '-'}
                </td>
                <td className="py-2.5 px-4 hidden lg:table-cell">
                  <div className="flex items-center space-x-2">
                    <StateBadge state={m.state} t={t} />
                    {m.state === 'active' && (
                      <button
                        onClick={() => handleToggleScope(m)}
                        className={cn(
                          'text-xs px-1.5 py-0.5 rounded border transition-colors',
                          m.scope === 'public'
                            ? 'border-spore/30 text-spore bg-spore/5 hover:bg-spore/10'
                            : 'border-white/10 text-gray-600 hover:text-gray-400'
                        )}
                        title={
                          m.scope === 'public'
                            ? t('table.scopePublicHint', 'Shared with network -- click to make private')
                            : t('table.scopePrivateHint', 'Private -- click to share with network')
                        }
                      >
                        {m.scope === 'public' ? '\u25CF public' : '\u25CB private'}
                      </button>
                    )}
                  </div>
                </td>
                <td className="py-2.5 px-4 text-right space-x-2 whitespace-nowrap">
                  {m.state === 'active' && (
                    <>
                      {m.backend !== 'llama.cpp' && (
                        <button
                          onClick={() =>
                            setEditingModel(editingModel === m.name ? null : m.name)
                          }
                          className={cn(
                            'text-xs transition-colors',
                            editingModel === m.name
                              ? 'text-spore'
                              : 'text-gray-500 hover:text-relay'
                          )}
                          title={t('table.edit', 'Edit')}
                        >
                          <Pencil className="w-3 h-3 inline" />
                        </button>
                      )}
                      <button
                        onClick={() => handleUnload(m.name)}
                        className="text-xs text-gray-500 hover:text-ledger transition-colors"
                        title={t('table.unload', 'Unload')}
                      >
                        <Square className="w-3 h-3 inline" />
                      </button>
                      {m.hasFile && (
                        <button
                          onClick={() => handleDelete(m)}
                          className="text-xs text-gray-600 hover:text-compute transition-colors"
                          title={t('table.delete', 'Delete')}
                        >
                          <Trash2 className="w-3 h-3 inline" />
                        </button>
                      )}
                    </>
                  )}
                  {m.state === 'failed' && (
                    <>
                      <button
                        onClick={() => handleLoad(m)}
                        className="text-xs text-ledger hover:text-ledger/80 transition-colors"
                        title={t('table.retry', 'Retry')}
                      >
                        <RotateCw className="w-3 h-3 inline" />
                      </button>
                      {m.hasFile && (
                        <button
                          onClick={() => handleDelete(m)}
                          className="text-xs text-gray-600 hover:text-compute transition-colors"
                          title={t('table.delete', 'Delete file')}
                        >
                          <Trash2 className="w-3 h-3 inline" />
                        </button>
                      )}
                      <button
                        onClick={() => handleDismiss(m.name)}
                        className="text-xs text-gray-600 hover:text-compute transition-colors"
                        title={t('table.dismiss', 'Dismiss')}
                      >
                        <XCircle className="w-3 h-3 inline" />
                      </button>
                    </>
                  )}
                  {m.state === 'loading' && (
                    <button
                      onClick={() => handleDismiss(m.name)}
                      className="text-xs text-gray-600 hover:text-compute transition-colors"
                      title={t('table.cancel', 'Cancel')}
                    >
                      <XCircle className="w-3 h-3 inline" />
                    </button>
                  )}
                  {m.state === 'on-disk' && (
                    <>
                      <button
                        onClick={() => handleLoad(m)}
                        className="text-xs text-spore hover:text-spore/80 transition-colors"
                      >
                        <Play className="w-3 h-3 inline" />
                      </button>
                      <button
                        onClick={() => handleDelete(m)}
                        className="text-xs text-gray-600 hover:text-compute transition-colors"
                      >
                        <Trash2 className="w-3 h-3 inline" />
                      </button>
                    </>
                  )}
                  {m.state === 'disabled' && (
                    <>
                      {m.backend !== 'llama.cpp' && (
                        <button
                          onClick={() =>
                            setEditingModel(editingModel === m.name ? null : m.name)
                          }
                          className="text-xs text-gray-500 hover:text-relay transition-colors"
                        >
                          <Pencil className="w-3 h-3 inline" />
                        </button>
                      )}
                      <button
                        onClick={() => handleReload(m.name)}
                        className="text-xs text-spore hover:text-spore/80 transition-colors"
                      >
                        <RotateCw className="w-3 h-3 inline" />
                      </button>
                      <button
                        onClick={() => handleRemoveConfig(m.name)}
                        className="text-xs text-gray-600 hover:text-compute transition-colors"
                      >
                        <X className="w-3 h-3 inline" />
                      </button>
                    </>
                  )}
                  {m.state === 'loading' && (
                    <span className="text-xs text-gray-600 font-mono">
                      {m.elapsed ? `${m.elapsed}s` : '...'}
                    </span>
                  )}
                </td>
              </tr>
              {/* Inline edit panel */}
              {editingModel === m.name && (
                <tr>
                  <td colSpan={7}>
                    <ModelEditPanel
                      model={{
                        name: m.name,
                        backend: m.backend,
                        loaded: m.state === 'active',
                        scope: m.scope,
                        ctx_len: m.ctx,
                      }}
                      onClose={() => setEditingModel(null)}
                      onSave={() => {
                        setEditingModel(null)
                        refreshAll()
                      }}
                    />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
