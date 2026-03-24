import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { FleetNode, HardwareNode } from '../api/types'

interface ManagedNode {
  addr: string
  label: string
  addedAt: string
}

interface FleetState {
  fleetNodes: FleetNode[]
  fleetHardware: HardwareNode[]
  managedNodes: ManagedNode[]
  setFleetNodes: (nodes: FleetNode[]) => void
  setFleetHardware: (hardware: HardwareNode[]) => void
  addManagedNode: (node: ManagedNode) => void
  removeManagedNode: (addr: string) => void
}

export const useFleetStore = create<FleetState>()(
  persist(
    (set) => ({
      fleetNodes: [],
      fleetHardware: [],
      managedNodes: [],
      setFleetNodes: (fleetNodes) => set({ fleetNodes }),
      setFleetHardware: (fleetHardware) => set({ fleetHardware }),
      addManagedNode: (node) =>
        set((state) => ({
          managedNodes: [...state.managedNodes, node],
        })),
      removeManagedNode: (addr) =>
        set((state) => ({
          managedNodes: state.managedNodes.filter((n) => n.addr !== addr),
        })),
    }),
    {
      name: 'mycellm_managed_nodes',
      partialize: (state) => ({ managedNodes: state.managedNodes }),
    }
  )
)
