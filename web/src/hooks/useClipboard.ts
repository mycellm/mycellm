import { useState, useCallback, useRef } from 'react';

export function useClipboard(): {
  copy: (text: string) => Promise<void>;
  copied: boolean;
} {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(async (text: string) => {
    await navigator.clipboard.writeText(text);

    setCopied(true);

    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
    timerRef.current = setTimeout(() => {
      setCopied(false);
      timerRef.current = null;
    }, 2000);
  }, []);

  return { copy, copied };
}
