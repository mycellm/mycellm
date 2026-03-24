import { cn } from '@/lib/utils'

type Status = 'online' | 'offline' | 'pending' | 'degraded'
type Size = 'sm' | 'md' | 'lg'

interface StatusDotProps {
  status: Status
  size?: Size
  className?: string
}

const sizeClasses: Record<Size, string> = {
  sm: 'w-2 h-2',
  md: 'w-3 h-3',
  lg: 'w-4 h-4',
}

const statusClasses: Record<Status, string> = {
  online: 'bg-spore',
  offline: 'bg-compute',
  pending: 'bg-ledger',
  degraded: 'bg-ledger',
}

const pulseStatuses: Status[] = ['online', 'pending']

export function StatusDot({ status, size = 'md', className }: StatusDotProps) {
  const shouldPulse = pulseStatuses.includes(status)

  return (
    <span className={cn('relative inline-flex items-center justify-center', className)}>
      {shouldPulse && (
        <span
          className={cn(
            'absolute rounded-full opacity-75 animate-ping',
            sizeClasses[size],
            statusClasses[status]
          )}
        />
      )}
      <span
        className={cn(
          'relative rounded-full',
          sizeClasses[size],
          statusClasses[status]
        )}
      />
    </span>
  )
}
