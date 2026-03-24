import { useTranslation } from 'react-i18next'
import { useFleetStore } from '@/stores/fleet'
import { cn } from '@/lib/utils'

interface NodeSelectorProps {
  selected: string
  onSelect: (addr: string) => void
  className?: string
}

export function NodeSelector({ selected, onSelect, className }: NodeSelectorProps) {
  const { t } = useTranslation('models')
  const managedNodes = useFleetStore((s) => s.managedNodes)

  return (
    <select
      value={selected}
      onChange={(e) => onSelect(e.target.value)}
      className={cn(
        'bg-black border border-white/10 rounded-lg text-sm font-mono text-console',
        'px-3 py-2 w-full appearance-none cursor-pointer',
        'focus:outline-none focus:border-spore/40',
        className
      )}
    >
      <option value="">
        {t('device.thisNode', 'This node (local)')}
      </option>
      {managedNodes.map((node) => (
        <option key={node.addr} value={node.addr}>
          {node.label} ({node.addr})
        </option>
      ))}
    </select>
  )
}
