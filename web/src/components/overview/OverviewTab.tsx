import { useFleetHardware } from '@/hooks/useFleetHardware'
import { useActivityStream } from '@/hooks/useActivityStream'
import { useFleetStore } from '@/stores/fleet'
import { NetworkIdentity } from './NetworkIdentity'
import { ApiEndpointCard } from './ApiEndpointCard'
import { StatsRow } from './StatsRow'
import { NetworkHealthBar } from './NetworkHealthBar'
import { FleetGrid } from './FleetGrid'
import { ActivityFeed } from './ActivityFeed'
import { NetworkTopology } from './NetworkTopology'

export function OverviewTab() {
  useFleetHardware()
  useActivityStream()

  const fleetHardware = useFleetStore((s) => s.fleetHardware)
  const hasFleet = fleetHardware && fleetHardware.length > 1

  return (
    <div className="space-y-5">
      {/* Identity + API endpoint */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <NetworkIdentity />
        <ApiEndpointCard />
      </div>

      {/* Stats row */}
      <StatsRow />

      {/* Network health */}
      <NetworkHealthBar />

      {/* Fleet hardware grid */}
      {hasFleet && <FleetGrid />}

      {/* Network topology */}
      <NetworkTopology />

      {/* Activity feed */}
      <ActivityFeed />
    </div>
  )
}
