import { useState, useEffect, useCallback } from 'react';

const VALID_TABS = [
  'overview',
  'network',
  'models',
  'chat',
  'queue',
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

/**
 * Where the user was on each tab.
 *
 * ⚠️ SCROLL POSITION WAS DESTROYED ON EVERY SWITCH — measured 318px → 0 after
 * a single round trip. Being dumped back to the top is one of the clearest
 * signals that you have left one document and arrived at another, and it is
 * why the dashboard read as separate pages despite being a SPA. A real app
 * remembers where you were.
 *
 * Module-level rather than state: it must survive the tab component
 * unmounting, which is exactly when it is recorded.
 */
const scrollByTab = new Map<Tab, number>();

export function useTabRouter(): { tab: Tab; setTab: (t: Tab) => void } {
  const [tab, setTabState] = useState<Tab>(getTabFromPath);

  const setTab = useCallback((t: Tab) => {
    setTabState((prev) => {
      if (prev === t) return prev;
      scrollByTab.set(prev, window.scrollY);
      return t;
    });
    window.history.pushState(null, '', `/${t}`);
  }, []);

  // Restore once the content is actually tall enough to hold the position.
  //
  // ⚠️ ONE requestAnimationFrame IS NOT ENOUGH, AND THAT WAS THE FIRST
  // ATTEMPT'S BUG. The tab mounts empty and fills in when its queries resolve,
  // so a single frame later the document is still a few rows tall and
  // `scrollTo(318)` silently clamps to 0 — the restore ran, did nothing, and
  // looked exactly like no restore at all. Retry across a short window until
  // the page can hold the target, then stop.
  useEffect(() => {
    const target = scrollByTab.get(tab) ?? 0;
    if (target === 0) {
      window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
      return;
    }

    let frame = 0;
    let raf = 0;
    const deadline = 40; // ~660ms at 60fps — past the slowest tab's settle

    const attempt = () => {
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      if (maxScroll >= target) {
        window.scrollTo({ top: target, behavior: 'instant' as ScrollBehavior });
        return; // content is tall enough; the position will hold
      }
      if (++frame < deadline) raf = requestAnimationFrame(attempt);
      // Gave up: the tab is genuinely shorter than where we were. Leaving it
      // at the top is correct — there is nowhere else to be.
    };

    raf = requestAnimationFrame(attempt);
    return () => cancelAnimationFrame(raf);
  }, [tab]);

  useEffect(() => {
    const onPopState = () => {
      // Back/forward is a tab change too — record where we were leaving from
      // so returning by button behaves like returning by click.
      setTabState((prev) => {
        const next = getTabFromPath();
        if (prev !== next) scrollByTab.set(prev, window.scrollY);
        return next;
      });
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  return { tab, setTab };
}
