import { create } from 'zustand'
import type { NodeStatus, SystemInfo, VersionInfo, Connection } from '../api/types'

interface NodeState {
  status: NodeStatus | null
  systemInfo: SystemInfo | null
  versionInfo: VersionInfo | null
  connections: Connection[]
  setStatus: (status: NodeStatus | null) => void
  setSystemInfo: (info: SystemInfo | null) => void
  setVersionInfo: (info: VersionInfo | null) => void
  setConnections: (connections: Connection[]) => void
}

export const useNodeStore = create<NodeState>()((set) => ({
  status: null,
  systemInfo: null,
  versionInfo: null,
  connections: [],
  setStatus: (status) => set({ status }),
  setSystemInfo: (systemInfo) => set({ systemInfo }),
  setVersionInfo: (versionInfo) => set({ versionInfo }),
  setConnections: (connections) => set({ connections }),
}))
