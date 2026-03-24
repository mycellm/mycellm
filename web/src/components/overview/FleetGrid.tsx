import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Cpu,
  LayoutGrid,
  List,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useFleetStore } from '@/stores/fleet'
import { useSettingsStore } from '@/stores/settings'
import type { HardwareNode } from '@/api/types'

const backendColors: Record<string, string> = {
  cuda: 'text-spore',
  metal: 'text-relay',
  rocm: 'text-poison',
  cpu: 'text-gray-500',
}

function HardwareCard({ node }: { node: HardwareNode }) {
  const gpuName = node.gpu || 'CPU'
  const isGpu = gpuName !== 'CPU' && gpuName !== 'none'
  const vramPct = node.vram_gb > 0 ? Math.min(100, node.ram_used_pct || 50) : 0
  const backendColor = backendColors[node.backend] || 'text-gray-500'

  return (
    <div
      className={cn(
        'border rounded-xl p-4 transition-colors',
        node.online !== false
          ? 'border-white/10 bg-[#0d0d0d]'
          : 'border-white/5 bg-black/50 opacity-50'
      )}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className={cn(
              'w-2.5 h-2.5 rounded-full shrink-0',
              node.online !== false ? 'bg-spore' : 'bg-gray-600'
            )}
          />
          <span className="font-mono text-sm text-white font-medium truncate">
            {node.name}
          </span>
        </div>
        <span
          className={cn(
            'text-xs font-mono px-1.5 py-0.5 rounded shrink-0',
            node.type === 'self'
              ? 'bg-spore/10 text-spore'
              : 'bg-white/5 text-gray-500'
          )}
        >
          {node.type}
        </span>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <Cpu size={13} className={backendColor} />
        <span className="text-sm text-gray-300 truncate">{gpuName}</span>
      </div>

      {(node.vram_gb > 0 || node.ram_gb > 0) && (
        <div className="mb-2">
          <div className="flex justify-between text-xs text-gray-500 mb-0.5">
            <span>{isGpu ? 'VRAM' : 'RAM'}</span>
            <span>{isGpu ? node.vram_gb : node.ram_gb} GB</span>
          </div>
          <div className="w-full bg-void rounded-full h-1.5 overflow-hidden border border-white/5">
            <div
              className={cn(
                'h-full transition-all',
                vramPct > 85
                  ? 'bg-compute'
                  : vramPct > 60
                    ? 'bg-ledger'
                    : 'bg-spore'
              )}
              style={{ width: `${vramPct || 30}%` }}
            />
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 text-xs text-gray-500 mt-2">
        <span className={backendColor}>
          {(node.backend || 'cpu').toUpperCase()}
        </span>
        {node.tps > 0 && (
          <span className="text-compute font-mono">{node.tps} T/s</span>
        )}
        {node.models && (
          <span>
            {node.models.length} model{node.models.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {node.models && node.models.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {node.models.map((m, i) => (
            <span
              key={i}
              className="text-xs font-mono bg-white/5 text-gray-400 px-1.5 py-0.5 rounded truncate max-w-[150px]"
            >
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SortIcon({ col, sortKey, sortDir }: { col: string; sortKey: string; sortDir: string }) {
  if (sortKey !== col)
    return <ArrowUpDown size={10} className="text-gray-600 ml-1" />
  return sortDir === 'asc' ? (
    <ArrowUp size={10} className="text-spore ml-1" />
  ) : (
    <ArrowDown size={10} className="text-spore ml-1" />
  )
}

export function FleetGrid() {
  const { t } = useTranslation('overview')
  const fleetHardware = useFleetStore((s) => s.fleetHardware)
  const fleetView = useSettingsStore((s) => s.fleetView)
  const fleetSort = useSettingsStore((s) => s.fleetSort)
  const setFleetView = useSettingsStore((s) => s.setFleetView)
  const setFleetSort = useSettingsStore((s) => s.setFleetSort)

  const sortDir = fleetSort.startsWith('-') ? 'desc' : 'asc'
  const sortKey = fleetSort.replace(/^-/, '')

  const sorted = useMemo(() => {
    if (!fleetHardware || fleetHardware.length === 0) return []
    return [...fleetHardware].sort((a, b) => {
      if (a.type === 'self') return -1
      if (b.type === 'self') return 1

      let av: string | number
      let bv: string | number

      switch (sortKey) {
        case 'name':
          av = (a.name || '').toLowerCase()
          bv = (b.name || '').toLowerCase()
          break
        case 'gpu':
          av = (a.gpu || '').toLowerCase()
          bv = (b.gpu || '').toLowerCase()
          break
        case 'backend':
          av = (a.backend || '').toLowerCase()
          bv = (b.backend || '').toLowerCase()
          break
        case 'ram':
          av = a.ram_gb || 0
          bv = b.ram_gb || 0
          break
        case 'tps':
          av = a.tps || 0
          bv = b.tps || 0
          break
        case 'models':
          av = (a.models || []).length
          bv = (b.models || []).length
          break
        case 'status':
          av = a.online !== false ? 1 : 0
          bv = b.online !== false ? 1 : 0
          break
        default:
          av = (a.name || '').toLowerCase()
          bv = (b.name || '').toLowerCase()
      }

      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [fleetHardware, sortKey, sortDir])

  if (!fleetHardware || fleetHardware.length <= 1) return null

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setFleetSort(sortDir === 'asc' ? `-${key}` : key)
    } else {
      setFleetSort(key)
    }
  }

  const aggregate = {
    totalTps: fleetHardware.reduce((sum, n) => sum + (n.tps || 0), 0),
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest flex items-center gap-2">
          <Cpu size={12} />
          <span>
            {t('fleetHardware', 'Fleet Hardware')} ({fleetHardware.length}{' '}
            {t('nodes', 'nodes')})
          </span>
          {aggregate.totalTps > 0 && (
            <span className="text-compute font-mono ml-2">
              {aggregate.totalTps} T/s {t('aggregate', 'aggregate')}
            </span>
          )}
        </h2>
        <div className="flex items-center border border-white/10 rounded-lg overflow-hidden">
          <button
            onClick={() => setFleetView('grid')}
            className={cn(
              'p-1.5 transition-colors',
              fleetView === 'grid'
                ? 'bg-white/10 text-white'
                : 'text-gray-500 hover:text-gray-300'
            )}
            title={t('gridView', 'Grid view')}
          >
            <LayoutGrid size={14} />
          </button>
          <button
            onClick={() => setFleetView('list')}
            className={cn(
              'p-1.5 transition-colors',
              fleetView === 'list'
                ? 'bg-white/10 text-white'
                : 'text-gray-500 hover:text-gray-300'
            )}
            title={t('listView', 'List view')}
          >
            <List size={14} />
          </button>
        </div>
      </div>

      {fleetView === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {sorted.map((n, i) => (
            <HardwareCard key={n.name + i} node={n} />
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto -mx-5 px-5">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-white/5">
                <th className="text-left py-2 px-2 font-normal w-6" />
                <th
                  className="text-left py-2 px-2 font-normal cursor-pointer select-none"
                  onClick={() => toggleSort('name')}
                >
                  <span className="flex items-center">
                    {t('node', 'Node')}
                    <SortIcon col="name" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-left py-2 px-2 font-normal cursor-pointer select-none"
                  onClick={() => toggleSort('gpu')}
                >
                  <span className="flex items-center">
                    GPU
                    <SortIcon col="gpu" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-left py-2 px-2 font-normal cursor-pointer select-none hidden sm:table-cell"
                  onClick={() => toggleSort('backend')}
                >
                  <span className="flex items-center">
                    Backend
                    <SortIcon col="backend" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-right py-2 px-2 font-normal cursor-pointer select-none hidden sm:table-cell"
                  onClick={() => toggleSort('ram')}
                >
                  <span className="flex items-center justify-end">
                    RAM
                    <SortIcon col="ram" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-right py-2 px-2 font-normal cursor-pointer select-none"
                  onClick={() => toggleSort('tps')}
                >
                  <span className="flex items-center justify-end">
                    T/s
                    <SortIcon col="tps" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-right py-2 px-2 font-normal cursor-pointer select-none hidden md:table-cell"
                  onClick={() => toggleSort('models')}
                >
                  <span className="flex items-center justify-end">
                    {t('models', 'Models')}
                    <SortIcon col="models" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
                <th
                  className="text-right py-2 px-2 font-normal cursor-pointer select-none"
                  onClick={() => toggleSort('status')}
                >
                  <span className="flex items-center justify-end">
                    {t('status', 'Status')}
                    <SortIcon col="status" sortKey={sortKey} sortDir={sortDir} />
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((n, i) => (
                <tr
                  key={n.name + i}
                  className="border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                >
                  <td className="py-2 px-2">
                    <div
                      className={cn(
                        'w-2 h-2 rounded-full',
                        n.online !== false ? 'bg-spore' : 'bg-gray-600'
                      )}
                    />
                  </td>
                  <td className="py-2 px-2 text-white">{n.name}</td>
                  <td className="py-2 px-2 text-gray-300">{n.gpu || 'CPU'}</td>
                  <td className="py-2 px-2 hidden sm:table-cell">
                    <span className={backendColors[n.backend] || 'text-gray-500'}>
                      {(n.backend || 'cpu').toUpperCase()}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right text-gray-400 hidden sm:table-cell">
                    {n.ram_gb ? `${n.ram_gb} GB` : '-'}
                  </td>
                  <td className="py-2 px-2 text-right text-compute">
                    {n.tps > 0 ? n.tps : '-'}
                  </td>
                  <td className="py-2 px-2 text-right text-gray-400 hidden md:table-cell">
                    {(n.models || []).length}
                  </td>
                  <td className="py-2 px-2 text-right">
                    <span
                      className={
                        n.online !== false ? 'text-spore' : 'text-gray-500'
                      }
                    >
                      {n.online !== false
                        ? t('online', 'Online')
                        : t('offlineLabel', 'Offline')}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
