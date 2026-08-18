import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { GroupsResponse } from '@/api/types';
import { useAuthStore } from '@/stores/auth';

/**
 * Serving groups: gateways this node fronts, and what each actually serves.
 *
 * Kept separate from `useModels` on purpose. The model list answers "what can
 * I ask for"; this answers "what is behind it, and is it alive". Merging them
 * would hide the case this exists to surface — a group that is online but
 * serving nothing, which used to keep advertising ghost models.
 */
export function useServingGroups() {
  const appState = useAuthStore((s) => s.appState);

  const { data, isLoading } = useQuery<GroupsResponse>({
    queryKey: ['serving-groups'],
    queryFn: () => api.get<GroupsResponse>(API.node.groups),
    refetchInterval: 10000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  return {
    groups: data?.groups ?? [],
    count: data?.count ?? 0,
    healthyCount: data?.healthy_count ?? 0,
    deploymentCount: data?.deployment_count ?? 0,
    isLoading,
  };
}
