import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

const colorMap = {
  spore: 'text-spore',
  compute: 'text-compute',
  relay: 'text-relay',
  ledger: 'text-ledger',
  poison: 'text-poison',
} as const

const iconBgMap = {
  spore: 'text-spore/40',
  compute: 'text-compute/40',
  relay: 'text-relay/40',
  ledger: 'text-ledger/40',
  poison: 'text-poison/40',
} as const

interface StatCardProps {
  label: string
  value: string | number
  sub?: string
  icon?: LucideIcon
  color?: 'spore' | 'compute' | 'relay' | 'ledger' | 'poison'
  highlight?: boolean
  className?: string
}

export function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  color = 'spore',
  highlight,
  className,
}: StatCardProps) {
  return (
    <div
      className={cn(
        'relative bg-surface border border-white/10 rounded-xl p-3 md:p-4 overflow-hidden',
        className
      )}
    >
      {Icon && (
        <Icon
          className={cn('absolute top-3 right-3 w-5 h-5 md:w-6 md:h-6', iconBgMap[color])}
        />
      )}

      <div className={cn(
        'text-2xl md:text-3xl font-mono leading-tight',
        highlight ? colorMap[color] : 'text-console'
      )}>
        {value}
      </div>

      <div className="text-xs font-mono text-gray-500 uppercase tracking-widest mt-1">
        {label}
      </div>

      {sub && (
        <div className="text-xs text-gray-600 mt-0.5">{sub}</div>
      )}
    </div>
  )
}
