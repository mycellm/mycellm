import { useEffect, useRef } from 'react';
import { api } from '@/api/client';
import { API } from '@/api/endpoints';
import type { LogEntry } from '@/api/types';
import { useLogsStore } from '@/stores/logs';
import { useAuthStore } from '@/stores/auth';

export function useLogStream(): void {
  const appState = useAuthStore((s) => s.appState);
  const setEntries = useLogsStore((s) => s.setEntries);
  const addEntry = useLogsStore((s) => s.addEntry);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (appState !== 'dashboard') return;

    let mounted = true;

    async function fetchInitialLogs() {
      try {
        const resp = await api.get<{ logs: LogEntry[] }>(
          `${API.node.logs}?limit=200`,
        );
        if (mounted) {
          setEntries(resp.logs ?? []);
        }
      } catch {
        // Initial fetch failed; stream will still attempt to connect
      }
    }

    function connect() {
      if (!mounted) return;

      const es = api.stream(API.node.logsStream);
      eventSourceRef.current = es;

      es.onmessage = (event: MessageEvent) => {
        if (!mounted) return;
        try {
          const entry: LogEntry = JSON.parse(event.data);
          addEntry(entry);
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

    fetchInitialLogs().then(connect);

    return () => {
      mounted = false;
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [appState, setEntries, addEntry]);
}
