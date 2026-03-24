import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { HardwareNode } from '@/api/types';
import { useFleetStore } from '@/stores/fleet';
import { useAuthStore } from '@/stores/auth';

interface FleetHardwareResponse {
  nodes: HardwareNode[];
}

export function useFleetHardware(): { isLoading: boolean } {
  const appState = useAuthStore((s) => s.appState);
  const setFleetHardware = useFleetStore((s) => s.setFleetHardware);

  const { data, isLoading } = useQuery<FleetHardwareResponse>({
    queryKey: ['node', 'fleet', 'hardware'],
    queryFn: () => api.get<FleetHardwareResponse>(API.node.fleetHardware),
    refetchInterval: 10000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  useEffect(() => {
    if (data) setFleetHardware(data.nodes);
  }, [data, setFleetHardware]);

  return { isLoading };
}
