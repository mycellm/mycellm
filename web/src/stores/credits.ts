import { create } from 'zustand'
import type { CreditTier, Transaction } from '../api/types'

interface CreditsState {
  balance: number
  earned: number
  spent: number
  tier: CreditTier | null
  history: Transaction[]
  setCredits: (credits: { balance: number; earned: number; spent: number }) => void
  setTier: (tier: CreditTier | null) => void
  setHistory: (history: Transaction[]) => void
}

export const useCreditsStore = create<CreditsState>()((set) => ({
  balance: 0,
  earned: 0,
  spent: 0,
  tier: null,
  history: [],
  setCredits: ({ balance, earned, spent }) => set({ balance, earned, spent }),
  setTier: (tier) => set({ tier }),
  setHistory: (history) => set({ history }),
}))
