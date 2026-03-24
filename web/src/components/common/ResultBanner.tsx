import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ResultBannerProps {
  type: 'success' | 'error'
  message: string
  onDismiss?: () => void
  className?: string
}

const styles = {
  success: 'border-spore/30 bg-spore/5 text-spore',
  error: 'border-compute/30 bg-compute/5 text-compute',
} as const

export function ResultBanner({ type, message, onDismiss, className }: ResultBannerProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 rounded-lg border px-4 py-2 text-sm font-mono',
        'animate-[fadeIn_0.2s_ease-in]',
        styles[type],
        className
      )}
    >
      <span>{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 p-0.5 rounded hover:bg-white/10 transition-colors"
          aria-label="Dismiss"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  )
}
