import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { FleetNode } from '@/api/types';
import { useFleetStore } from '@/stores/fleet';
import { useAuthStore } from '@/stores/auth';

const QUERY_KEY = ['admin', 'nodes'] as const;

export function useFleetNodes(): { isLoading: boolean } {
  const appState = useAuthStore((s) => s.appState);
  const setFleetNodes = useFleetStore((s) => s.setFleetNodes);

  const { data, isLoading } = useQuery<{ nodes: FleetNode[] }>({
    queryKey: QUERY_KEY,
    queryFn: () => api.get<{ nodes: FleetNode[] }>(API.admin.nodes),
    refetchInterval: 5000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  useEffect(() => {
    if (data?.nodes) setFleetNodes(data.nodes);
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
