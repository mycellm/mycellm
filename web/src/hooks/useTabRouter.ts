import { useState, useEffect, useCallback } from 'react';

const VALID_TABS = [
  'overview',
  'network',
  'models',
  'chat',
  'credits',
  'logs',
  'settings',
] as const;

export type Tab = (typeof VALID_TABS)[number];

function isValidTab(value: string): value is Tab {
  return (VALID_TABS as readonly string[]).includes(value);
}

function getTabFromPath(): Tab {
  const raw = window.location.pathname.replace(/^\/+/, '').split('/')[0] || '';
  return isValidTab(raw) ? raw : 'overview';
}

export function useTabRouter(): { tab: Tab; setTab: (t: Tab) => void } {
  const [tab, setTabState] = useState<Tab>(getTabFromPath);

  const setTab = useCallback((t: Tab) => {
    setTabState(t);
    window.history.pushState(null, '', `/${t}`);
  }, []);

  useEffect(() => {
    const onPopState = () => {
      setTabState(getTabFromPath());
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  return { tab, setTab };
}
