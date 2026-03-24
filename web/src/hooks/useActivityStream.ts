import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { ActivityData, ActivityEvent } from '@/api/types';
import { useActivityStore } from '@/stores/activity';
import { useAuthStore } from '@/stores/auth';

export function useActivityStream(): void {
  const appState = useAuthStore((s) => s.appState);
  const setActivityData = useActivityStore((s) => s.setActivityData);
  const addLiveEvent = useActivityStore((s) => s.addLiveEvent);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Poll for stats/sparkline updates
  const { data } = useQuery<ActivityData>({
    queryKey: ['node', 'activity'],
    queryFn: () => api.get<ActivityData>(`${API.node.activity}?limit=100`),
    refetchInterval: 3000,
    enabled: appState === 'dashboard',
    retry: false,
  });

  useEffect(() => {
    if (data) setActivityData(data);
  }, [data, setActivityData]);

  // SSE for live events
  useEffect(() => {
    if (appState !== 'dashboard') return;

    let mounted = true;

    async function fetchInitial() {
      try {
        const data = await api.get<ActivityData>(
          `${API.node.activity}?limit=100`,
        );
        if (mounted) {
          setActivityData(data);
        }
      } catch {
        // Initial fetch failed; stream will still attempt to connect
      }
    }

    function connect() {
      if (!mounted) return;

      const es = api.stream(API.node.activityStream);
      eventSourceRef.current = es;

      es.onmessage = (event: MessageEvent) => {
        if (!mounted) return;
        try {
          const parsed: ActivityEvent = JSON.parse(event.data);
          addLiveEvent(parsed);
        } catch {
          // Ignore malformed messages
        }
      };

      es.onerror = () => {
        es.close();
        eventSourceRef.current = null;
        if (mounted) {
          reconnectTimerRef.current = setTimeout(connect, 3000);
        }
      };
    }

    fetchInitial().then(connect);

    return () => {
      mounted = false;
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [appState, setActivityData, addLiveEvent]);
}
