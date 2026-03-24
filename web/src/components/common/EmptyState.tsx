import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: LucideIcon
  message: string
  className?: string
}

export function EmptyState({ icon: Icon, message, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12', className)}>
      {Icon && <Icon className="w-12 h-12 text-gray-600 mb-3" />}
      <p className="text-gray-500 text-sm text-center">{message}</p>
    </div>
  )
}
