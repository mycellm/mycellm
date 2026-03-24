import { useState, useEffect, useRef, useCallback, Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Search,
  ChevronRight,
  CheckCircle,
  AlertTriangle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { VariantTable } from './VariantTable'
import type { SearchResult } from '@/api/types'

interface ModelBrowserProps {
  selectedDevice: string
}

interface SuggestedModel {
  repo_id: string
  param_b: number
  est_size_gb: number
  min_ram_gb: number
  description: string
  compatible: boolean
}

interface NodeResources {
  ram_gb: number
  disk_free_gb: number
}

function capBadges(m: SearchResult) {
  const caps: { label: string; color: string }[] = []
  const name = (m.repo_id || '').toLowerCase()
  const tags = (m.tags || []).map((t) => t.toLowerCase())
  if (name.includes('code') || name.includes('coder') || tags.includes('code'))
    caps.push({ label: 'code', color: 'text-relay bg-relay/10' })
  if (name.includes('instruct') || name.includes('chat') || tags.includes('conversational'))
    caps.push({ label: 'chat', color: 'text-spore bg-spore/10' })
  if (name.includes('vision') || name.includes('vl') || name.includes('llava'))
    caps.push({ label: 'vision', color: 'text-poison bg-poison/10' })
  if (name.includes('reason') || name.includes('think') || name.includes('r1') || name.includes('qwq'))
    caps.push({ label: 'reasoning', color: 'text-ledger bg-ledger/10' })
  if (name.includes('embed'))
    caps.push({ label: 'embedding', color: 'text-gray-400 bg-white/5' })
  if (caps.length === 0)
    caps.push({ label: 'general', color: 'text-gray-500 bg-white/5' })
  return caps
}

export function ModelBrowser({ selectedDevice }: ModelBrowserProps) {
  const { t } = useTranslation('models')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [filterCompatible, setFilterCompatible] = useState(true)
  const [suggestions, setSuggestions] = useState<SuggestedModel[] | null>(null)
  const [nodeResources, setNodeResources] = useState<NodeResources>({ ram_gb: 0, disk_free_gb: 0 })
  const [expandedRepo, setExpandedRepo] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  const isRemote = selectedDevice !== ''

  const nodeGet = useCallback(
    <T,>(path: string): Promise<T> =>
      isRemote ? api.remote<T>(selectedDevice, path) : api.get<T>(path),
    [isRemote, selectedDevice]
  )

  // Fetch suggestions on mount
  useEffect(() => {
    nodeGet<{
      suggestions?: SuggestedModel[]
      node_ram_gb?: number
      node_disk_free_gb?: number
    }>(API.models.suggested)
      .then((d) => {
        setSuggestions(d.suggestions || [])
        setNodeResources({
          ram_gb: d.node_ram_gb || 0,
          disk_free_gb: d.node_disk_free_gb || 0,
        })
      })
      .catch(() => {})
  }, [nodeGet])

  // Reset when device changes
  useEffect(() => {
    setSearchResults([])
    setHasSearched(false)
    setExpandedRepo(null)
    setSuggestions(null)
  }, [selectedDevice])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    setHasSearched(true)
    setExpandedRepo(null)
    try {
      const data = await nodeGet<{
        models?: SearchResult[]
        node_ram_gb?: number
        node_disk_free_gb?: number
      }>(`${API.models.search}?q=${encodeURIComponent(searchQuery)}&limit=12`)
      setSearchResults(data.models || [])
      if (data.node_ram_gb) {
        setNodeResources({
          ram_gb: data.node_ram_gb,
          disk_free_gb: data.node_disk_free_gb || 0,
        })
      }
    } catch {
      setSearchResults([])
    }
    setSearching(false)
  }

  // Debounced search
  const handleQueryChange = (value: string) => {
    setSearchQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (value.trim().length >= 3) {
      debounceRef.current = setTimeout(() => {
        handleSearch()
      }, 300)
    }
  }

  const isCompat = (m: { est_min_size_gb?: number; est_min_ram_gb?: number }) =>
    !('est_min_ram_gb' in m && m.est_min_ram_gb) ||
    !nodeResources.ram_gb ||
    (m.est_min_ram_gb || 0) <= nodeResources.ram_gb

  const toggleExpand = (repoId: string) => {
    setExpandedRepo(expandedRepo === repoId ? null : repoId)
  }

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="flex space-x-2 items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            value={searchQuery}
            onChange={(e) => handleQueryChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder={t(
              'browser.searchPlaceholder',
              'Search models... (e.g. llama 7b, mistral, phi)'
            )}
            className="w-full bg-black border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
          />
        </div>
        <button
          onClick={handleSearch}
          disabled={searching}
          className="bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 whitespace-nowrap"
        >
          {searching
            ? t('browser.searching', 'Searching...')
            : t('browser.search', 'Search')}
        </button>
        <button
          onClick={() => setFilterCompatible((f) => !f)}
          className={cn(
            'text-xs px-2 py-1.5 rounded border transition-colors whitespace-nowrap',
            filterCompatible
              ? 'border-spore/30 text-spore bg-spore/5'
              : 'border-white/10 text-gray-500'
          )}
        >
          {filterCompatible
            ? t('browser.compatible', 'Compatible')
            : t('browser.allSizes', 'All sizes')}
        </button>
      </div>

      {/* Search results */}
      {searchResults.length > 0 && (() => {
        const filtered = filterCompatible
          ? searchResults.filter(isCompat)
          : searchResults
        const hiddenCount = searchResults.length - filtered.length

        return (
          <>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-600 font-mono uppercase">
                  <th className="text-left py-1 pr-1 w-5" />
                  <th className="text-left py-1 pr-2">
                    {t('browser.colModel', 'Model')}
                  </th>
                  <th className="text-left py-1 pr-2 hidden sm:table-cell">
                    {t('browser.colType', 'Type')}
                  </th>
                  <th className="text-right py-1 pr-2 hidden sm:table-cell">
                    {t('browser.colParams', 'Params')}
                  </th>
                  <th className="text-left py-1 pr-2 hidden md:table-cell">
                    {t('browser.colArch', 'Arch')}
                  </th>
                  <th className="text-right py-1 pr-2 hidden md:table-cell">
                    {t('browser.colContext', 'Context')}
                  </th>
                  <th className="text-right py-1 pr-2">
                    {t('browser.colMinSize', 'Min size')}
                  </th>
                  <th className="text-right py-1 pr-2 hidden lg:table-cell">
                    {t('browser.colDownloads', 'Downloads')}
                  </th>
                  <th className="text-right py-1 w-5" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((m, i) => {
                  const compat = isCompat(m)
                  const isExpanded = expandedRepo === m.repo_id
                  return (
                    <Fragment key={i}>
                      <tr
                        onClick={() => toggleExpand(m.repo_id)}
                        className={cn(
                          'cursor-pointer border-t border-white/5 transition-colors',
                          isExpanded
                            ? 'bg-white/[0.05]'
                            : compat
                            ? 'hover:bg-white/[0.03]'
                            : 'opacity-50'
                        )}
                      >
                        <td className="py-2 pr-1">
                          <ChevronRight
                            className={cn(
                              'w-3 h-3 text-gray-600 transition-transform',
                              isExpanded && 'rotate-90'
                            )}
                          />
                        </td>
                        <td className="py-2 pr-2">
                          <div className="font-mono text-sm text-white truncate max-w-[250px]">
                            {m.repo_id}
                          </div>
                        </td>
                        <td className="py-2 pr-2 hidden sm:table-cell">
                          <div className="flex gap-1">
                            {capBadges(m).map((c, j) => (
                              <span
                                key={j}
                                className={cn('px-1 py-0 rounded text-xs', c.color)}
                              >
                                {c.label}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-300 font-mono hidden sm:table-cell">
                          {m.param_b > 0 ? `${m.param_b}B` : '-'}
                        </td>
                        <td className="py-2 pr-2 text-gray-500 hidden md:table-cell">
                          {m.architecture || '-'}
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-500 hidden md:table-cell">
                          {m.context_length > 0
                            ? `${(m.context_length / 1000).toFixed(0)}k`
                            : '-'}
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-400">
                          {m.est_min_size_gb > 0 ? `~${m.est_min_size_gb}GB` : '-'}
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-600 hidden lg:table-cell">
                          {m.downloads?.toLocaleString()}
                        </td>
                        <td className="py-2 text-right">
                          {!compat && (
                            <AlertTriangle className="w-3 h-3 text-compute" />
                          )}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={9} className="p-0">
                            <VariantTable
                              repoId={m.repo_id}
                              selectedDevice={selectedDevice}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
            {hiddenCount > 0 && (
              <div className="text-xs text-gray-600 mt-2">
                {hiddenCount} model(s) hidden (too large for{' '}
                {nodeResources.ram_gb}GB RAM).
                <button
                  onClick={() => setFilterCompatible(false)}
                  className="text-gray-400 hover:text-white ml-1 underline"
                >
                  {t('browser.showAll', 'Show all')}
                </button>
              </div>
            )}
          </>
        )
      })()}

      {/* No results */}
      {searchResults.length === 0 && !searching && hasSearched && (
        <div className="text-center text-sm text-gray-600 py-4">
          {t('browser.noResults', 'No GGUF models found for "{{query}}"', {
            query: searchQuery,
          })}
        </div>
      )}

      {/* Suggestions (shown when no search yet) */}
      {!hasSearched && suggestions && suggestions.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs text-gray-500 font-mono uppercase tracking-wider">
              {t('browser.suggested', 'Suggested for this node')}
            </h3>
            {nodeResources.ram_gb > 0 && (
              <span className="text-xs text-gray-600">
                {nodeResources.ram_gb}GB RAM &middot; {nodeResources.disk_free_gb}GB
                disk free
              </span>
            )}
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-600 font-mono uppercase">
                <th className="text-left py-1 pr-1 w-5" />
                <th className="text-left py-1 pr-2">
                  {t('browser.colModel', 'Model')}
                </th>
                <th className="text-right py-1 pr-2">
                  {t('browser.colParams', 'Params')}
                </th>
                <th className="text-right py-1 pr-2">
                  {t('browser.colEstSize', 'Est. size')}
                </th>
                <th className="text-right py-1 pr-2 hidden sm:table-cell">
                  {t('browser.colMinRam', 'Min RAM')}
                </th>
                <th className="text-right py-1 w-5" />
              </tr>
            </thead>
            <tbody>
              {suggestions
                .filter((s) => !filterCompatible || s.compatible)
                .map((s, i) => {
                  const isExpanded = expandedRepo === s.repo_id
                  return (
                    <Fragment key={i}>
                      <tr
                        onClick={() => toggleExpand(s.repo_id)}
                        className={cn(
                          'cursor-pointer border-t border-white/5 transition-colors',
                          isExpanded
                            ? 'bg-white/[0.05]'
                            : s.compatible
                            ? 'hover:bg-white/[0.03]'
                            : 'opacity-50'
                        )}
                      >
                        <td className="py-2 pr-1">
                          <ChevronRight
                            className={cn(
                              'w-3 h-3 text-gray-600 transition-transform',
                              isExpanded && 'rotate-90'
                            )}
                          />
                        </td>
                        <td className="py-2 pr-2">
                          <div className="font-mono text-sm text-white">
                            {s.repo_id.split('/').pop()}
                          </div>
                          <div className="text-gray-500 truncate max-w-[250px]">
                            {s.description}
                          </div>
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-300 font-mono">
                          {s.param_b}B
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-400">
                          ~{s.est_size_gb}GB
                        </td>
                        <td className="py-2 pr-2 text-right text-gray-600 hidden sm:table-cell">
                          {s.min_ram_gb}GB+
                        </td>
                        <td className="py-2 text-right">
                          {s.compatible ? (
                            <CheckCircle className="w-3 h-3 text-spore" />
                          ) : (
                            <AlertTriangle className="w-3 h-3 text-compute" />
                          )}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={6} className="p-0">
                            <VariantTable
                              repoId={s.repo_id}
                              selectedDevice={selectedDevice}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
            </tbody>
          </table>
          {filterCompatible && suggestions.some((s) => !s.compatible) && (
            <div className="text-xs text-gray-600 mt-2">
              {suggestions.filter((s) => !s.compatible).length} model(s) hidden.
              <button
                onClick={() => setFilterCompatible(false)}
                className="text-gray-400 hover:text-white ml-1 underline"
              >
                {t('browser.showAll', 'Show all')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
