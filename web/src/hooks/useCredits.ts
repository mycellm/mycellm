import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { Credits } from '@/api/types';
import { useCreditsStore } from '@/stores/credits';

export function useCredits(): { isLoading: boolean } {
  const setCredits = useCreditsStore((s) => s.setCredits);

  const { data, isLoading } = useQuery<Credits>({
    queryKey: ['node', 'credits'],
    queryFn: () => api.get<Credits>(API.node.credits),
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (data) setCredits(data);
  }, [data, setCredits]);

  return { isLoading };
}
