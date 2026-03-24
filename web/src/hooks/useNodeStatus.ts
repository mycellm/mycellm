import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { NodeStatus } from '@/api/types';
import { useNodeStore } from '@/stores/node';
import { useAuthStore } from '@/stores/auth';

export function useNodeStatus(): { isLoading: boolean; error: Error | null } {
  const appState = useAuthStore((s) => s.appState);
  const setStatus = useNodeStore((s) => s.setStatus);

  const { data, isLoading, error } = useQuery<NodeStatus>({
    queryKey: ['node', 'status'],
    queryFn: () => api.get<NodeStatus>(API.node.status),
    refetchInterval: 3000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  useEffect(() => {
    if (data) setStatus(data);
  }, [data, setStatus]);

  return { isLoading, error: error as Error | null };
}
