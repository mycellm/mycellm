import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { HardwareNode } from '@/api/types';
import { useFleetStore } from '@/stores/fleet';

interface FleetHardwareResponse {
  nodes: HardwareNode[];
}

export function useFleetHardware(): { isLoading: boolean } {
  const setFleetHardware = useFleetStore((s) => s.setFleetHardware);

  const { data, isLoading } = useQuery<FleetHardwareResponse>({
    queryKey: ['node', 'fleet', 'hardware'],
    queryFn: () => api.get<FleetHardwareResponse>(API.node.fleetHardware),
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (data) setFleetHardware(data.nodes);
  }, [data, setFleetHardware]);

  return { isLoading };
}
