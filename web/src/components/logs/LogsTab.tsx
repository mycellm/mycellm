import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Terminal } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useLogStream } from '@/hooks/useLogStream'
import { useLogsStore } from '@/stores/logs'
import { EmptyState } from '@/components/common/EmptyState'

const LOG_TAG_COLORS: Record<string, string> = {
  'mycellm.inference': 'text-compute',
  'mycellm.transport': 'text-relay',
  'mycellm.router': 'text-relay',
  'mycellm.dht': 'text-relay',
  'mycellm.accounting': 'text-ledger',
  mycellm: 'text-spore',
}

function tagColor(name: string): string {
  for (const [prefix, color] of Object.entries(LOG_TAG_COLORS)) {
    if (name.startsWith(prefix)) return color
  }
  return 'text-gray-600'
}

function levelLabel(level: string): string {
  switch (level) {
    case 'ERROR':
      return 'ERR'
    case 'WARNING':
      return 'WRN'
    case 'INFO':
      return 'INF'
    case 'DEBUG':
      return 'DBG'
    default:
      return level.slice(0, 3).toUpperCase()
  }
}

function levelColor(level: string): string {
  switch (level) {
    case 'ERROR':
      return 'text-compute'
    case 'WARNING':
      return 'text-ledger'
    case 'INFO':
      return 'text-gray-500'
    case 'DEBUG':
      return 'text-gray-700'
    default:
      return 'text-gray-600'
  }
}

function formatTime(time: string): string {
  // Expect ISO string or HH:MM:SS — extract time portion
  if (!time) return ''
  if (time.includes('T')) {
    try {
      const d = new Date(time)
      return d.toLocaleTimeString('en-US', { hour12: false })
    } catch {
      return time
    }
  }
  return time
}

export function LogsTab() {
  const { t } = useTranslation('logs')
  useLogStream()

  const entries = useLogsStore((s) => s.entries)
  const autoScroll = useLogsStore((s) => s.autoScroll)
  const toggleAutoScroll = useLogsStore((s) => s.toggleAutoScroll)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (autoScroll) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [entries, autoScroll])

  return (
    <div className="border border-white/10 bg-black rounded-xl overflow-hidden flex flex-col h-[calc(100vh-220px)]">
      {/* Header bar */}
      <div className="h-10 border-b border-white/10 bg-surface flex items-center px-4 justify-between shrink-0">
        <div className="flex items-center space-x-2 text-xs font-mono text-gray-400">
          <Terminal size={14} />
          <span>{t('header.title', 'mycellm-node.log')}</span>
          <span className="text-gray-600">
            ({entries.length} {t('header.entries', 'entries')})
          </span>
        </div>
        <button
          onClick={toggleAutoScroll}
          className={cn(
            'text-xs font-mono px-2 py-0.5 rounded transition-colors',
            autoScroll ? 'text-spore bg-spore/10' : 'text-gray-500 hover:text-gray-300'
          )}
        >
          {autoScroll
            ? t('header.autoScrollOn', 'auto-scroll on')
            : t('header.autoScrollOff', 'auto-scroll off')}
        </button>
      </div>

      {/* Log entries */}
      <div className="p-4 font-mono text-xs leading-relaxed overflow-y-auto flex-grow bg-[#050505] custom-scrollbar">
        {entries.length === 0 ? (
          <EmptyState
            icon={Terminal}
            message={t('empty', 'No log entries yet.')}
          />
        ) : (
          entries.map((log, i) => (
            <div
              key={i}
              className="mb-0.5 flex hover:bg-white/[0.02] transition-colors"
            >
              {/* Time */}
              <span className="text-gray-600 mr-3 w-16 shrink-0">
                {formatTime(log.time)}
              </span>

              {/* Level badge */}
              <span
                className={cn(
                  'mr-2 w-6 shrink-0',
                  levelColor(log.level)
                )}
              >
                {levelLabel(log.level)}
              </span>

              {/* Tag - hidden on mobile */}
              <span
                className={cn(
                  'mr-2 w-40 shrink-0 truncate hidden md:inline',
                  tagColor(log.name)
                )}
              >
                {log.name}
              </span>

              {/* Message */}
              <span className={cn(tagColor(log.name), 'md:text-console')}>
                {log.message}
              </span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </div>
  )
}

export default LogsTab
