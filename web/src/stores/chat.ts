import { create } from 'zustand'
import type { ChatMessage, RoutingOptions } from '../api/types'

interface ChatState {
  messages: ChatMessage[]
  model: string
  routingOpts: RoutingOptions
  sending: boolean
  addMessage: (message: ChatMessage) => void
  removeMessages: (predicate: (msg: ChatMessage) => boolean) => void
  /** Patch one message in place — how streaming appends tokens. */
  updateMessage: (id: string, patch: (msg: ChatMessage) => ChatMessage) => void
  clearMessages: () => void
  setModel: (model: string) => void
  setRoutingOpts: (opts: Partial<RoutingOptions>) => void
  setSending: (sending: boolean) => void
}

export const useChatStore = create<ChatState>()((set) => ({
  messages: [],
  model: '',
  routingOpts: {
    required_tags: [],
    routing: 'best',
    fallback: 'downgrade',
    trust: '',
    fanout: 0,
    token_budget: 0,
    show_reasoning: false,
  },
  sending: false,
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  removeMessages: (predicate) =>
    set((state) => ({ messages: state.messages.filter((m) => !predicate(m)) })),
  updateMessage: (id, patch) =>
    set((state) => ({
      messages: state.messages.map((m) => (m.id === id ? patch(m) : m)),
    })),
  clearMessages: () => set({ messages: [] }),
  setModel: (model) => set({ model }),
  setRoutingOpts: (opts) =>
    set((state) => ({
      routingOpts: { ...state.routingOpts, ...opts },
    })),
  setSending: (sending) => set({ sending }),
}))
