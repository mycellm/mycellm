import { create } from 'zustand'
import type { ActivityEvent } from '../api/types'

interface ActivityStats {
  requests_1m: number
  requests_5m: number
  tokens_1m: number
  avg_latency_ms: number
}

interface ActivitySparklines {
  throughput: number[]
  latency: number[]
  data_size: number[]
}

interface ActivityState {
  events: ActivityEvent[]
  stats: ActivityStats | null
  sparklines: ActivitySparklines | null
  liveEvents: ActivityEvent[]
  setActivityData: (data: {
    events: ActivityEvent[]
    stats: ActivityStats
    sparklines: ActivitySparklines
  }) => void
  addLiveEvent: (event: ActivityEvent) => void
}

export const useActivityStore = create<ActivityState>()((set) => ({
  events: [],
  stats: null,
  sparklines: null,
  liveEvents: [],
  setActivityData: ({ events, stats, sparklines }) =>
    set({ events, stats, sparklines }),
  addLiveEvent: (event) =>
    set((state) => ({
      liveEvents: [...state.liveEvents, event].slice(-50),
    })),
}))
