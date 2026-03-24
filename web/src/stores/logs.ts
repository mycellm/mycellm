import { create } from 'zustand'
import type { LogEntry } from '../api/types'

interface LogsState {
  entries: LogEntry[]
  autoScroll: boolean
  setEntries: (entries: LogEntry[]) => void
  addEntry: (entry: LogEntry) => void
  toggleAutoScroll: () => void
}

export const useLogsStore = create<LogsState>()((set) => ({
  entries: [],
  autoScroll: true,
  setEntries: (entries) => set({ entries }),
  addEntry: (entry) =>
    set((state) => ({
      entries: [...state.entries, entry].slice(-500),
    })),
  toggleAutoScroll: () =>
    set((state) => ({ autoScroll: !state.autoScroll })),
}))
