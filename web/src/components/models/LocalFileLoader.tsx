import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { HardDrive, Play, FileText } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { ResultBanner } from '@/components/common/ResultBanner'
import { EmptyState } from '@/components/common/EmptyState'

interface LocalFileLoaderProps {
  selectedDevice: string
}

interface LocalFile {
  filename: string
  path: string
  model_name: string
  size_gb: number
  quant?: string
  loaded?: boolean
}

export function LocalFileLoader({ selectedDevice }: LocalFileLoaderProps) {
  const { t } = useTranslation('models')
  const [localFiles, setLocalFiles] = useState<LocalFile[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingFile, setLoadingFile] = useState<string | null>(null)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  // Manual path form
  const [modelPath, setModelPath] = useState('')
  const [modelName, setModelName] = useState('')

  const isRemote = selectedDevice !== ''

  const nodeGet = useCallback(
    <T,>(path: string): Promise<T> =>
      isRemote ? api.remote<T>(selectedDevice, path) : api.get<T>(path),
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

  // Fetch local GGUF files
  useEffect(() => {
    nodeGet<{ files?: LocalFile[] }>(API.models.localFiles)
      .then((d) => setLocalFiles(d.files || []))
      .catch(() => {})
  }, [nodeGet])

  const handleLoadFile = async (file: LocalFile) => {
    setLoadingFile(file.filename)
    setResult(null)
    try {
      const data = await nodePost<{ error?: string; success?: string }>(API.models.load, {
        model_path: file.path,
        name: file.model_name,
        backend: 'llama.cpp',
        ctx_len: 4096,
      })
      if (data.error) {
        setResult({ type: 'error', message: data.error })
      } else {
        setResult({ type: 'success', message: data.success || `Loading ${file.model_name}...` })
        // Refresh file list
        nodeGet<{ files?: LocalFile[] }>(API.models.localFiles)
          .then((d) => setLocalFiles(d.files || []))
          .catch(() => {})
      }
    } catch (e) {
      setResult({ type: 'error', message: e instanceof Error ? e.message : 'Failed to load' })
    }
    setLoadingFile(null)
  }

  const handleLoadPath = async () => {
    if (!modelPath) return
    setLoading(true)
    setResult(null)
    try {
      const data = await nodePost<{ error?: string; success?: string }>(API.models.load, {
        model_path: modelPath,
        name: modelName || undefined,
        backend: 'llama.cpp',
        ctx_len: 4096,
      })
      if (data.error) {
        setResult({ type: 'error', message: data.error })
      } else {
        setResult({ type: 'success', message: data.success || 'Model loading...' })
        setModelPath('')
        setModelName('')
      }
    } catch (e) {
      setResult({ type: 'error', message: e instanceof Error ? e.message : 'Failed to load' })
    }
    setLoading(false)
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        {t('localFile.desc', 'Load a GGUF model already on this machine\'s disk.')}
      </p>

      {/* Manual path input */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="text-xs text-gray-500 block mb-1">
            {t('localFile.pathLabel', 'Model path (.gguf)')}
          </label>
          <input
            value={modelPath}
            onChange={(e) => setModelPath(e.target.value)}
            placeholder="/path/to/model.gguf"
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            {t('localFile.nameLabel', 'Name (optional)')}
          </label>
          <input
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            placeholder="my-model"
            className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
      </div>
      <button
        onClick={handleLoadPath}
        disabled={loading || !modelPath}
        className="bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40"
      >
        {loading
          ? t('localFile.loading', 'Loading...')
          : t('localFile.loadModel', 'Load Model')}
      </button>

      {/* Discovered local files */}
      {localFiles.length > 0 && (
        <div>
          <h3 className="text-xs text-gray-500 font-mono uppercase tracking-wider mb-2">
            {t('localFile.discovered', 'Discovered GGUF Files')}
          </h3>
          <div className="space-y-1.5">
            {localFiles.map((f) => (
              <div
                key={f.filename}
                className="flex items-center justify-between bg-black/40 border border-white/5 rounded-lg px-4 py-2.5"
              >
                <div className="flex items-center space-x-3 min-w-0">
                  <FileText className="w-4 h-4 text-gray-500 shrink-0" />
                  <div className="min-w-0">
                    <div className="font-mono text-sm text-white truncate">
                      {f.filename}
                    </div>
                    <div className="text-xs text-gray-600">
                      {f.size_gb}GB
                      {f.quant && ` \u00B7 ${f.quant}`}
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => handleLoadFile(f)}
                  disabled={loadingFile === f.filename || f.loaded}
                  className={cn(
                    'shrink-0 inline-flex items-center space-x-1 px-3 py-1 rounded text-xs font-medium transition-colors',
                    f.loaded
                      ? 'bg-spore/10 text-spore cursor-default'
                      : 'text-spore hover:text-spore/80 hover:bg-spore/10'
                  )}
                >
                  {f.loaded ? (
                    <span>{t('localFile.loaded', 'Loaded')}</span>
                  ) : loadingFile === f.filename ? (
                    <span>{t('localFile.loading', 'Loading...')}</span>
                  ) : (
                    <>
                      <Play className="w-3 h-3" />
                      <span>{t('localFile.load', 'Load')}</span>
                    </>
                  )}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {localFiles.length === 0 && (
        <EmptyState
          icon={HardDrive}
          message={t('localFile.empty', 'No local GGUF files found. Download one from HuggingFace or place a .gguf file in the models directory.')}
          className="py-6"
        />
      )}

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
