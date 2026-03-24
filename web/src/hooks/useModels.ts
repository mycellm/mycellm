import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { Model, SavedModel } from '@/api/types';
import { useAuthStore } from '@/stores/auth';

interface ModelsResponse {
  data: Model[];
}

export function useModels(): {
  models: Model[];
  savedModels: SavedModel[];
  isLoading: boolean;
  isSavedLoading: boolean;
} {
  const appState = useAuthStore((s) => s.appState);

  const {
    data: modelsData,
    isLoading,
  } = useQuery<ModelsResponse>({
    queryKey: ['models'],
    queryFn: () => api.get<ModelsResponse>(API.models.list),
    refetchInterval: 5000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  const {
    data: savedData,
    isLoading: isSavedLoading,
  } = useQuery<{ configs: SavedModel[] }>({
    queryKey: ['models', 'saved'],
    queryFn: () => api.get<{ configs: SavedModel[] }>(API.models.saved),
    refetchInterval: 5000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  return {
    models: modelsData?.data ?? [],
    savedModels: savedData?.configs ?? [],
    isLoading,
    isSavedLoading,
  };
}
