import { useState, useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/auth'

const BOOT_LINES = [
  'mycellm_ v0.1.2',
  'Initializing node...',
  'Loading identity...',
  'Connecting to bootstrap...',
  'Ready.',
]

const LINE_DELAY = 300
const FINISH_DELAY = 500

export function BootScreen() {
  const setAppState = useAuthStore((s) => s.setAppState)
  const [visibleCount, setVisibleCount] = useState(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (visibleCount < BOOT_LINES.length) {
      timerRef.current = setTimeout(() => {
        setVisibleCount((c) => c + 1)
      }, LINE_DELAY)
    } else {
      timerRef.current = setTimeout(() => {
        setAppState('dashboard')
      }, FINISH_DELAY)
    }

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [visibleCount, setAppState])

  return (
    <div className="min-h-screen flex items-start justify-start bg-void p-6 sm:p-12">
      <div className="font-mono text-spore text-sm sm:text-base space-y-1">
        {BOOT_LINES.slice(0, visibleCount).map((line, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="text-gray-600 select-none">{'>'}</span>
            <span
              className={
                i === visibleCount - 1 && visibleCount < BOOT_LINES.length
                  ? 'animate-pulse'
                  : ''
              }
            >
              {line}
            </span>
          </div>
        ))}
        {visibleCount < BOOT_LINES.length && (
          <span className="inline-block w-2 h-4 bg-spore animate-pulse ml-4" />
        )}
      </div>
    </div>
  )
}
