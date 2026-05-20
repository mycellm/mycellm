import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { useFleetStore } from '@/stores/fleet'
import { useFleetHardware } from '@/hooks/useFleetHardware'
import { useModels } from '@/hooks/useModels'
import { SortHeader } from '@/components/common/SortHeader'
interface DeviceTableProps {
  selected: string
  onSelect: (addr: string, name?: string) => void
}

interface DeviceEntry {
  id: string
  name: string
  addr: string
  gpu: string
  backend: string
  ram: number
  models: string[]
  online: boolean
  role: string
  isSelf: boolean
}

export function DeviceTable({ selected, onSelect }: DeviceTableProps) {
  const { t } = useTranslation('models')
  const { models } = useModels()
  const fleetHardware = useFleetStore((s) => s.fleetHardware)
  const fleetNodes = useFleetStore((s) => s.fleetNodes)
  useFleetHardware()

  const [sortBy, setSortBy] = useState('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [nodeStatus, setNodeStatus] = useState<{
    node_name?: string
    role?: string
    hardware?: { gpu?: string; gpu_name?: string; backend?: string; gpu_backend?: string; vram_gb?: number }
    models?: { name: string }[]
  } | null>(null)

  useEffect(() => {
    const fetchStatus = () => {
      api.get<typeof nodeStatus>(API.node.status)
        .then(setNodeStatus)
        .catch(() => {})
    }
    fetchStatus()
    const iv = setInterval(fetchStatus, 5000)
    return () => clearInterval(iv)
  }, [])

  const handleSort = (field: string) => {
    if (sortBy === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(field)
      setSortDir('asc')
    }
  }

  // Build device list
  const devices: DeviceEntry[] = []

  // Self node
  const hw = nodeStatus?.hardware || {}
  devices.push({
    id: 'local',
    name: nodeStatus?.node_name || 'this node',
    addr: `${window.location.hostname}:${window.location.port || '8420'}`,
    gpu: hw.gpu || hw.gpu_name || 'CPU',
    backend: hw.backend || hw.gpu_backend || 'cpu',
    ram: hw.vram_gb || 0,
    models: models.filter((m) => !m.owned_by || m.owned_by === 'local').map((m) => m.id),
    online: true,
    role: nodeStatus?.role || 'bootstrap',
    isSelf: true,
  })

  // Fleet nodes
  for (const n of fleetHardware) {
    if (n.type === 'self') continue
    // Look up actual API address from fleet registry
    const registryNode = fleetNodes.find((fn) => fn.node_name === n.name)
    const addr = registryNode?.api_addr || (n.name.includes(':') ? n.name : `${n.name}:8420`)
    devices.push({
      id: n.name,
      name: n.name,
      addr,
      gpu: n.gpu || 'CPU',
      backend: n.backend || 'cpu',
      ram: n.ram_gb || n.vram_gb || 0,
      models: n.models || [],
      online: n.online,
      role: 'seeder',
      isSelf: false,
    })
  }

  const sorted = [...devices].sort((a, b) => {
    if (a.isSelf) return -1
    if (b.isSelf) return 1
    const va = a[sortBy as keyof DeviceEntry]
    const vb = b[sortBy as keyof DeviceEntry]
    if (typeof va === 'number' && typeof vb === 'number') {
      return sortDir === 'asc' ? va - vb : vb - va
    }
    return sortDir === 'asc'
      ? String(va).localeCompare(String(vb))
      : String(vb).localeCompare(String(va))
  })

  const thClass = 'text-left font-mono text-xs text-gray-500 uppercase tracking-wider py-2 px-3'

  return (
    <div className="border border-white/10 bg-surface rounded-xl overflow-hidden">
      <table className="w-full">
        <thead className="border-b border-white/10 bg-black/30">
          <tr>
            <SortHeader label={t('device.name', 'Device')} field="name" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} />
            <SortHeader label={t('device.address', 'Address')} field="addr" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} className="hidden md:table-cell" />
            <SortHeader label={t('device.gpu', 'GPU')} field="gpu" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} className="hidden lg:table-cell" />
            <SortHeader label={t('device.backend', 'Backend')} field="backend" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} className="hidden lg:table-cell" />
            <SortHeader label={t('device.ram', 'RAM')} field="ram" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} className="hidden md:table-cell" />
            <th className={thClass}>{t('device.models', 'Models')}</th>
            <th className={cn(thClass, 'w-16')}>{t('device.status', 'Status')}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((d) => {
            const isSelected = d.isSelf ? selected === '' : d.addr === selected
            return (
              <tr
                key={d.id}
                onClick={() => onSelect(d.isSelf ? '' : d.addr, d.name)}
                className={cn(
                  'cursor-pointer border-b border-white/5 transition-colors',
                  isSelected
                    ? 'bg-spore/10 border-l-2 border-l-spore'
                    : 'hover:bg-white/[0.03]'
                )}
              >
                <td className="px-3 py-3">
                  <div className="flex items-center space-x-2">
                    <div
                      className={cn(
                        'w-2 h-2 rounded-full',
                        d.online ? 'bg-spore' : 'bg-compute'
                      )}
                    />
                    <span className="font-mono text-sm text-white">{d.name}</span>
                    {d.role && (
                      <span className="text-xs text-gray-600">{d.role}</span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-3 hidden md:table-cell font-mono text-xs text-gray-500">
                  {d.addr}
                </td>
                <td className="px-3 py-3 hidden lg:table-cell text-sm text-gray-400 truncate max-w-[160px]" title={d.gpu}>
                  {d.gpu}
                </td>
                <td className="px-3 py-3 hidden lg:table-cell text-xs text-gray-500 uppercase">
                  {d.backend}
                </td>
                <td className="px-3 py-3 hidden md:table-cell text-sm text-gray-400">
                  {d.ram > 0 ? `${d.ram} GB` : '-'}
                </td>
                <td className="px-3 py-3">
                  {d.models.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {d.models.map((m, i) => (
                        <span
                          key={i}
                          className="text-xs font-mono bg-spore/10 text-spore px-1.5 py-0.5 rounded"
                        >
                          {m}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-xs text-gray-600">
                      {t('device.noModels', 'none')}
                    </span>
                  )}
                </td>
                <td className="px-3 py-3">
                  <span
                    className={cn(
                      'text-xs font-mono',
                      d.online ? 'text-spore' : 'text-compute'
                    )}
                  >
                    {d.online ? t('device.online', 'online') : t('device.offline', 'offline')}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
