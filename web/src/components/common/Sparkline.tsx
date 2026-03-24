import { useMemo, useId } from 'react'
import { cn } from '@/lib/utils'

interface SparklineProps {
  data: number[]
  color?: string
  height?: number
  className?: string
}

export function Sparkline({
  data,
  color = '#22C55E',
  height = 32,
  className,
}: SparklineProps) {
  const gradientId = useId()

  const { polyline, area } = useMemo(() => {
    if (data.length < 2) return { polyline: '', area: '' }

    const min = Math.min(...data)
    const max = Math.max(...data)
    const range = max - min || 1

    const points = data.map((v, i) => {
      const x = (i / (data.length - 1)) * 100
      const y = height - ((v - min) / range) * (height - 2) - 1
      return `${x},${y}`
    })

    const polyline = points.join(' ')

    // Close path for fill area
    const area = `${points.join(' ')} 100,${height} 0,${height}`

    return { polyline, area }
  }, [data, height])

  if (data.length < 2) return null

  return (
    <svg
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className={cn('w-full', className)}
      style={{ height }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.2" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>

      <polygon
        points={area}
        fill={`url(#${gradientId})`}
      />

      <polyline
        points={polyline}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
