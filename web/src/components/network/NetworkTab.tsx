import { Loader2 } from 'lucide-react'
import { useFleetNodes } from '@/hooks/useFleetNodes'
import { FleetRegistry } from './FleetRegistry'
import { ManualNodeAdd } from './ManualNodeAdd'
import { NetworkModels } from './NetworkModels'
import { QuicPeers } from './QuicPeers'

export function NetworkTab() {
  const { isLoading } = useFleetNodes()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[200px]">
        <Loader2 className="w-6 h-6 text-spore animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <FleetRegistry />
      <ManualNodeAdd />
      <NetworkModels />
      <QuicPeers />
    </div>
  )
}

export default NetworkTab
