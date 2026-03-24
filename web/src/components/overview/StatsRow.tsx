import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useActivityStore } from '@/stores/activity'

interface SparklineProps {
  data: number[]
  color?: string
  height?: number
  width?: number
}

function Sparkline({ data, color = '#22C55E', height = 32, width = 70 }: SparklineProps) {
  if (!data || data.length === 0) return <div style={{ width, height }} />

  const max = Math.max(...data, 1)
  const points = data
    .map((v, i) => {
      const x = (i / Math.max(data.length - 1, 1)) * width
      const y = height - (v / max) * (height - 4) - 2
      return `${x},${y}`
    })
    .join(' ')

  return (
    <svg width={width} height={height} className="opacity-80">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function formatLatency(ms: number): string {
  if (!ms || ms <= 0) return '0ms'
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

function formatDataRate(bytes: number[]): string {
  if (!bytes || bytes.length === 0) return '0 B/s'
  const last = bytes[bytes.length - 1] || 0
  if (last >= 1_000_000) return `${(last / 1_000_000).toFixed(1)} MB/s`
  if (last >= 1_000) return `${(last / 1_000).toFixed(1)} KB/s`
  return `${last} B/s`
}

export function StatsRow() {
  const { t } = useTranslation('overview')
  const stats = useActivityStore((s) => s.stats)
  const sparklines = useActivityStore((s) => s.sparklines)

  const cards = [
    {
      label: t('requestsMin', 'Requests/min'),
      value: stats?.requests_1m ?? 0,
      sub: `${stats?.requests_5m ?? 0} / 5m`,
      sparkData: sparklines?.throughput || [],
      sparkColor: '#EF4444',
      valueColor: 'text-compute',
    },
    {
      label: t('tokensMin', 'Tokens/min'),
      value: stats?.tokens_1m ?? 0,
      sub: null,
      sparkData: sparklines?.throughput || [],
      sparkColor: '#A855F7',
      valueColor: 'text-poison',
    },
    {
      label: t('avgLatency', 'Avg Latency'),
      value: formatLatency(stats?.avg_latency_ms ?? 0),
      sub: null,
      sparkData: sparklines?.latency || [],
      sparkColor: '#FACC15',
      valueColor: 'text-ledger',
    },
    {
      label: t('dataThroughput', 'Data'),
      value: formatDataRate(sparklines?.data_size || []),
      sub: null,
      sparkData: sparklines?.data_size || [],
      sparkColor: '#3B82F6',
      valueColor: 'text-relay',
    },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((card) => (
        <div
          key={card.label}
          className="border border-white/10 bg-surface rounded-xl p-4"
        >
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <div className="text-xs text-gray-500 font-mono uppercase truncate">
                {card.label}
              </div>
              <div
                className={cn('text-2xl font-mono mt-1', card.valueColor)}
              >
                {card.value}
              </div>
              {card.sub && (
                <div className="text-xs text-gray-600">{card.sub}</div>
              )}
            </div>
            <Sparkline
              data={card.sparkData}
              color={card.sparkColor}
              height={32}
              width={70}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
