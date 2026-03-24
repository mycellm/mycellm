import { useTranslation } from 'react-i18next'
import { useNodeStore } from '@/stores/node'
import { cn } from '@/lib/utils'

interface SystemInfoPanelProps {
  compact?: boolean
  className?: string
}

export function SystemInfoPanel({ compact, className }: SystemInfoPanelProps) {
  const { t } = useTranslation('overview')
  const systemInfo = useNodeStore((s) => s.systemInfo)

  if (!systemInfo) {
    return (
      <div className={cn('text-sm font-mono text-gray-600', className)}>
        {t('hardware.unavailable', 'Hardware info unavailable')}
      </div>
    )
  }

  const { cpu, memory, disk, gpu, os } = systemInfo

  if (compact) {
    return (
      <div className={cn('grid grid-cols-2 gap-2 text-sm font-mono', className)}>
        <CompactRow label={t('hardware.cpu', 'CPU')} value={cpu.name} />
        <CompactRow
          label={t('hardware.ram', 'RAM')}
          value={`${memory.total_gb.toFixed(1)} GB (${memory.used_pct}%)`}
        />
        <CompactRow label={t('hardware.gpu', 'GPU')} value={gpu.name || 'None'} />
        <CompactRow label={t('hardware.os', 'OS')} value={os.distro} />
      </div>
    )
  }

  return (
    <div className={cn('space-y-3 text-sm font-mono', className)}>
      {/* CPU */}
      <Section title={t('hardware.cpu', 'CPU')}>
        <Row label={t('hardware.cpuName', 'Name')} value={cpu.name} />
        <Row label={t('hardware.arch', 'Arch')} value={cpu.arch} />
        <Row
          label={t('hardware.cores', 'Cores')}
          value={`${cpu.cores} (${cpu.physical_cores} physical)`}
        />
      </Section>

      {/* Memory */}
      <Section title={t('hardware.memory', 'Memory')}>
        <Row label={t('hardware.total', 'Total')} value={`${memory.total_gb.toFixed(1)} GB`} />
        <Row label={t('hardware.used', 'Used')} value={`${memory.used_pct}%`} />
        <Row
          label={t('hardware.available', 'Available')}
          value={`${memory.available_gb.toFixed(1)} GB`}
        />
      </Section>

      {/* Disk */}
      <Section title={t('hardware.disk', 'Disk')}>
        <Row label={t('hardware.total', 'Total')} value={`${disk.total_gb.toFixed(1)} GB`} />
        <Row label={t('hardware.used', 'Used')} value={`${disk.used_pct}%`} />
        <Row
          label={t('hardware.available', 'Available')}
          value={`${disk.available_gb.toFixed(1)} GB`}
        />
      </Section>

      {/* GPU */}
      <Section title={t('hardware.gpu', 'GPU')}>
        <Row label={t('hardware.gpuName', 'Name')} value={gpu.name || 'None'} />
        <Row label={t('hardware.backend', 'Backend')} value={gpu.backend || 'N/A'} />
        <Row
          label={t('hardware.vram', 'VRAM')}
          value={gpu.vram_gb ? `${gpu.vram_gb.toFixed(1)} GB` : 'N/A'}
        />
      </Section>

      {/* OS */}
      <Section title={t('hardware.os', 'OS')}>
        <Row label={t('hardware.distro', 'Distro')} value={os.distro} />
        <Row label={t('hardware.hostname', 'Hostname')} value={os.hostname} />
        <Row label={t('hardware.python', 'Python')} value={os.python_version} />
        <Row label={t('hardware.version', 'mycellm')} value={os.mycellm_version} />
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-gray-500 text-xs uppercase tracking-widest mb-1">{title}</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5">{children}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-500 shrink-0">{label}</span>
      <span className="text-console truncate text-right">{value}</span>
    </div>
  )
}

function CompactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="overflow-hidden">
      <div className="text-gray-500 text-xs">{label}</div>
      <div className="text-console truncate">{value}</div>
    </div>
  )
}
