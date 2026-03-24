import { ChevronUp, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

interface SortHeaderProps {
  label: string
  field: string
  currentSort: string
  currentDir: 'asc' | 'desc'
  onSort: (field: string) => void
  className?: string
}

export function SortHeader({
  label,
  field,
  currentSort,
  currentDir,
  onSort,
  className,
}: SortHeaderProps) {
  const active = currentSort === field
  const Icon = active && currentDir === 'asc' ? ChevronUp : ChevronDown

  return (
    <th
      onClick={() => onSort(field)}
      className={cn(
        'font-mono text-xs uppercase tracking-wider cursor-pointer select-none',
        'px-3 py-2 text-left transition-colors',
        active ? 'text-spore' : 'text-gray-500 hover:text-gray-300',
        className
      )}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && <Icon className="w-3 h-3" />}
      </span>
    </th>
  )
}
