import { create } from 'zustand'
import type { ChatMessage, RoutingOptions } from '../api/types'

interface ChatState {
  messages: ChatMessage[]
  model: string
  routingOpts: RoutingOptions
  sending: boolean
  addMessage: (message: ChatMessage) => void
  removeMessages: (predicate: (msg: ChatMessage) => boolean) => void
  clearMessages: () => void
  setModel: (model: string) => void
  setRoutingOpts: (opts: Partial<RoutingOptions>) => void
  setSending: (sending: boolean) => void
}

export const useChatStore = create<ChatState>()((set) => ({
  messages: [],
  model: '',
  routingOpts: {
    min_tier: 'any',
    required_tags: [],
    routing: 'best',
    fallback: 'downgrade',
  },
  sending: false,
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  removeMessages: (predicate) =>
    set((state) => ({ messages: state.messages.filter((m) => !predicate(m)) })),
  clearMessages: () => set({ messages: [] }),
  setModel: (model) => set({ model }),
  setRoutingOpts: (opts) =>
    set((state) => ({
      routingOpts: { ...state.routingOpts, ...opts },
    })),
  setSending: (sending) => set({ sending }),
}))
