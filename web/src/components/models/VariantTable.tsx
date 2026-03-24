import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Download,
  CheckCircle,
  XCircle,
  AlertTriangle,
  HardDrive,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { useModels } from '@/hooks/useModels'
import type { RepoFile, DownloadStatus } from '@/api/types'

interface RepoFilesData {
  repo_id: string
  param_b: number
  architecture: string
  context_length: number
  disk_free_gb: number
  files: (RepoFile & { warnings?: string[] })[]
}

interface VariantTableProps {
  repoId: string
  selectedDevice: string
}

const QUANT_INFO: Record<string, { quality: string; desc: string; stars: number }> = {
  Q2_K:   { quality: 'Low',         desc: 'Smallest, lowest quality. Testing only.', stars: 1 },
  Q3_K_S: { quality: 'Low-Med',     desc: 'Small but noticeable quality loss.',       stars: 2 },
  Q3_K_M: { quality: 'Medium-Low',  desc: 'Decent balance for very constrained RAM.', stars: 2 },
  Q3_K_L: { quality: 'Medium-Low',  desc: 'Better than Q3_K_M, still small.',        stars: 2 },
  Q4_K_S: { quality: 'Good',        desc: 'Good quality, smaller than Q4_K_M.',       stars: 3 },
  Q4_K_M: { quality: 'Recommended', desc: 'Best balance of quality and size.',        stars: 4 },
  Q4_0:   { quality: 'Good',        desc: 'Older quant format, decent quality.',       stars: 3 },
  Q5_K_S: { quality: 'High',        desc: 'High quality, moderate size increase.',     stars: 4 },
  Q5_K_M: { quality: 'High',        desc: 'Very good quality, recommended if RAM allows.', stars: 4 },
  Q6_K:   { quality: 'Very High',   desc: 'Near-original quality.',                    stars: 5 },
  Q8_0:   { quality: 'Excellent',   desc: 'Almost lossless. Large file.',              stars: 5 },
  F16:    { quality: 'Full',        desc: 'Full 16-bit precision. Very large.',        stars: 5 },
  F32:    { quality: 'Full',        desc: 'Full 32-bit precision. Maximum size.',      stars: 5 },
  IQ4_NL: { quality: 'Good',        desc: 'iQuant: good quality, small size.',         stars: 3 },
  IQ4_XS: { quality: 'Good',        desc: 'iQuant: slightly smaller than IQ4_NL.',     stars: 3 },
}

function getQuantColor(quant: string): string {
  if (quant?.startsWith('Q4')) return 'text-spore bg-spore/10'
  if (quant?.startsWith('Q5') || quant?.startsWith('Q6')) return 'text-relay bg-relay/10'
  if (quant?.startsWith('Q8') || quant === 'F16') return 'text-poison bg-poison/10'
  if (quant?.startsWith('Q2') || quant?.startsWith('Q3')) return 'text-ledger bg-ledger/10'
  return 'text-gray-500 bg-white/5'
}

interface LocalFileEntry {
  filename: string
  model_name: string
}

export function VariantTable({ repoId, selectedDevice }: VariantTableProps) {
  const { t } = useTranslation('models')
  const { models } = useModels()
  const [repoFiles, setRepoFiles] = useState<RepoFilesData | null>(null)
  const [downloadStatus, setDownloadStatus] = useState<Record<string, DownloadStatus>>({})
  const [localFiles, setLocalFiles] = useState<LocalFileEntry[]>([])
  const [filterCompatible, setFilterCompatible] = useState(true)

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

  // Fetch repo files
  useEffect(() => {
    nodeGet<RepoFilesData>(API.models.searchFiles(repoId))
      .then(setRepoFiles)
      .catch(() => {})
  }, [repoId, nodeGet])

  // Fetch local files for comparison
  useEffect(() => {
    nodeGet<{ files?: LocalFileEntry[] }>(API.models.localFiles)
      .then((d) => setLocalFiles(d.files || []))
      .catch(() => {})
  }, [nodeGet])

  // Poll downloads
  useEffect(() => {
    const hasActive = Object.values(downloadStatus).some(
      (d) => d.status === 'downloading'
    )
    if (!hasActive) return

    const iv = setInterval(() => {
      nodeGet<{ downloads?: DownloadStatus[] }>(API.models.downloads)
        .then((d) => {
          const updated: Record<string, DownloadStatus> = { ...downloadStatus }
          for (const dl of d.downloads || []) {
            updated[dl.download_id] = dl
          }
          setDownloadStatus(updated)
        })
        .catch(() => {})
    }, 2000)
    return () => clearInterval(iv)
  }, [downloadStatus, nodeGet])

  const handleDownload = async (file: RepoFilesData['files'][0]) => {
    if (!repoFiles) return
    try {
      const data = await nodePost<{ download_id?: string } & DownloadStatus>(
        API.models.download,
        {
          repo_id: repoId,
          filename: file.filename,
          quant: file.quant || '',
          param_b: repoFiles.param_b || 0,
          context_length: repoFiles.context_length || 4096,
          size_gb: file.size_gb || 0,
        }
      )
      if (data.download_id) {
        setDownloadStatus((prev) => ({
          ...prev,
          [data.download_id!]: data as DownloadStatus,
        }))
      }
    } catch {}
  }

  if (!repoFiles) {
    return (
      <div className="ml-6 py-2 text-xs text-gray-600">
        {t('variants.loading', 'Loading variants...')}
      </div>
    )
  }

  const files = repoFiles.files.filter(
    (f) => !filterCompatible || !f.warnings || f.warnings.length === 0
  )

  if (files.length === 0) {
    return (
      <div className="ml-6 py-2 text-xs text-gray-600">
        {t('variants.noCompatible', 'No compatible variants')}
        {filterCompatible && (
          <button
            onClick={() => setFilterCompatible(false)}
            className="text-gray-400 hover:text-white ml-1 underline"
          >
            {t('variants.showAll', 'Show all')}
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="ml-6 mr-2 mb-2 border-l border-spore/20 pl-3">
      <div className="flex items-center space-x-3 mb-1.5 text-xs text-gray-500">
        {repoFiles.param_b > 0 && <span>{repoFiles.param_b}B params</span>}
        {repoFiles.architecture && <span>{repoFiles.architecture}</span>}
        {repoFiles.context_length > 0 && (
          <span>{repoFiles.context_length.toLocaleString()} ctx</span>
        )}
        {repoFiles.disk_free_gb > 0 && (
          <span className="text-gray-600">{repoFiles.disk_free_gb}GB free</span>
        )}
        <button
          onClick={() => setFilterCompatible((f) => !f)}
          className={cn(
            'text-xs px-1.5 py-0.5 rounded border transition-colors',
            filterCompatible
              ? 'border-spore/30 text-spore bg-spore/5'
              : 'border-white/10 text-gray-500'
          )}
        >
          {filterCompatible
            ? t('variants.compatible', 'Compatible')
            : t('variants.allSizes', 'All sizes')}
        </button>
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-600 font-mono uppercase">
            <th className="text-left py-1 pr-2 w-20">
              {t('variants.quant', 'Quant')}
            </th>
            <th className="text-left py-1 pr-2">
              {t('variants.quality', 'Quality')}
            </th>
            <th className="text-left py-1 pr-2 w-24 hidden sm:table-cell">
              {t('variants.rating', 'Rating')}
            </th>
            <th className="text-right py-1 pr-2">
              {t('variants.size', 'Size')}
            </th>
            <th className="text-right py-1 pr-2">
              {t('variants.ram', 'RAM')}
            </th>
            <th className="text-right py-1 w-24" />
          </tr>
        </thead>
        <tbody>
          {files.map((f, i) => {
            const dl = Object.values(downloadStatus).find(
              (d) => d.filename === f.filename && d.repo_id === repoId
            )
            const hasWarnings = f.warnings && f.warnings.length > 0
            const isOnDisk = localFiles.some((lf) => lf.filename === f.filename)
            const isLoaded = models.some(
              (md) => f.filename.replace('.gguf', '') === md.id
            )
            const qi = QUANT_INFO[f.quant] || { quality: '?', desc: '', stars: 0 }

            return (
              <tr
                key={i}
                className={cn(
                  'border-t border-white/5',
                  hasWarnings && !isOnDisk
                    ? 'opacity-40'
                    : 'hover:bg-white/[0.02]'
                )}
              >
                <td className="py-1.5 pr-2">
                  <span className={cn('font-mono px-1.5 py-0.5 rounded', getQuantColor(f.quant))}>
                    {f.quant || '?'}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-gray-400" title={qi.desc}>
                  {qi.quality}
                </td>
                <td className="py-1.5 pr-2 hidden sm:table-cell">
                  <span
                    className={cn(
                      qi.stars >= 4
                        ? 'text-spore'
                        : qi.stars >= 3
                        ? 'text-ledger'
                        : 'text-gray-600'
                    )}
                  >
                    {'\u2605'.repeat(qi.stars)}
                    {'\u2606'.repeat(5 - qi.stars)}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-400 font-mono">
                  {f.size_gb}GB
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-600">
                  {f.est_ram_gb ? `~${f.est_ram_gb}` : '?'}GB
                </td>
                <td className="py-1.5 text-right">
                  {dl && dl.status === 'downloading' ? (
                    <div className="inline-flex items-center space-x-1">
                      <div className="w-12 bg-void rounded-full h-1 overflow-hidden border border-white/5">
                        <div
                          className="h-full bg-ledger"
                          style={{ width: `${dl.progress || 0}%` }}
                        />
                      </div>
                      <span className="text-ledger font-mono w-8 text-right">
                        {dl.progress?.toFixed(0)}%
                      </span>
                      {dl.speed_mbs && (
                        <span className="text-gray-600 text-[10px]">
                          {dl.speed_mbs.toFixed(1)} MB/s
                        </span>
                      )}
                      {dl.eta_s && (
                        <span className="text-gray-600 text-[10px]">
                          {dl.eta_s > 60
                            ? `${Math.round(dl.eta_s / 60)}m`
                            : `${Math.round(dl.eta_s)}s`}
                        </span>
                      )}
                    </div>
                  ) : isOnDisk || (dl && dl.status === 'complete') ? (
                    <span className="inline-flex items-center space-x-1">
                      {isLoaded ? (
                        <CheckCircle className="w-3 h-3 text-spore" />
                      ) : (
                        <HardDrive className="w-3 h-3 text-gray-500" />
                      )}
                      <span className={isLoaded ? 'text-spore' : 'text-gray-500'}>
                        {isLoaded
                          ? t('variants.loaded', 'loaded')
                          : t('variants.onDisk', 'on disk')}
                      </span>
                    </span>
                  ) : dl && dl.status === 'failed' ? (
                    <span className="inline-flex items-center space-x-1 text-compute">
                      <XCircle className="w-3 h-3" />
                      <span>{t('variants.failed', 'failed')}</span>
                    </span>
                  ) : hasWarnings ? (
                    <button
                      onClick={() => handleDownload(f)}
                      className="inline-flex items-center space-x-1 text-ledger hover:text-ledger/80"
                    >
                      <AlertTriangle className="w-3 h-3" />
                      <span>{t('variants.download', 'download')}</span>
                    </button>
                  ) : (
                    <button
                      onClick={() => handleDownload(f)}
                      className="inline-flex items-center space-x-1 text-spore hover:text-spore/80"
                    >
                      <Download className="w-3 h-3" />
                      <span>{t('variants.download', 'download')}</span>
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
