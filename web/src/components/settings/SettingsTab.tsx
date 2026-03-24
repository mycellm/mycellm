import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import type { NodeConfig as NodeConfigType } from '@/api/types'
import { SecretsManager } from './SecretsManager'
import { NodeConfig } from './NodeConfig'
import { TelemetryToggle } from './TelemetryToggle'
import { FleetAdminKey } from './FleetAdminKey'
import { IntegrationLinks } from './IntegrationLinks'

interface SecretsResponse {
  secrets: string[]
}

export function SettingsTab() {
  const [secrets, setSecrets] = useState<string[]>([])
  const [telemetryEnabled, setTelemetryEnabled] = useState(false)

  // Fetch config once
  const { data: config } = useQuery<NodeConfigType>({
    queryKey: ['node', 'config'],
    queryFn: () => api.get<NodeConfigType>(API.node.config),
    staleTime: Infinity,
  })

  // Fetch secrets once
  const { data: secretsData } = useQuery<SecretsResponse>({
    queryKey: ['node', 'secrets'],
    queryFn: () => api.get<SecretsResponse>(API.node.secrets),
    staleTime: Infinity,
  })

  useEffect(() => {
    if (secretsData) {
      setSecrets(secretsData.secrets || [])
    }
  }, [secretsData])

  useEffect(() => {
    if (config) {
      setTelemetryEnabled(config.telemetry)
    }
  }, [config])

  return (
    <div className="space-y-5">
      <SecretsManager secrets={secrets} onSecretsChange={setSecrets} />

      <NodeConfig config={config ?? null} />

      <TelemetryToggle enabled={telemetryEnabled} onToggle={setTelemetryEnabled} />

      <FleetAdminKey />

      <IntegrationLinks />
    </div>
  )
}

export default SettingsTab
