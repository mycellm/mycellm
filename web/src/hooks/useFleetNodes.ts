import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { FleetNode } from '@/api/types';
import { useFleetStore } from '@/stores/fleet';

const QUERY_KEY = ['admin', 'nodes'] as const;

export function useFleetNodes(): { isLoading: boolean } {
  const setFleetNodes = useFleetStore((s) => s.setFleetNodes);

  const { data, isLoading } = useQuery<FleetNode[]>({
    queryKey: QUERY_KEY,
    queryFn: () => api.get<FleetNode[]>(API.admin.nodes),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (data) setFleetNodes(data);
  }, [data, setFleetNodes]);

  return { isLoading };
}

export function useApproveNode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (peerId: string) =>
      api.post(API.admin.approveNode(peerId), {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
}

export function useRemoveNode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (peerId: string) =>
      api.post(API.admin.removeNode(peerId), {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
}
