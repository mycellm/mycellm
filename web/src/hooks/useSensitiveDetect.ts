import { useCallback } from 'react';

interface SensitiveDetectResult {
  types: string[];
}

const PATTERNS: Array<{ label: string; regex: RegExp }> = [
  {
    label: 'API key',
    regex: /(?:sk-|ghp_|gho_|akia|hf_)[a-zA-Z0-9]{10,}/i,
  },
  {
    label: 'Password',
    regex: /(?:password|secret|token|credential)\s*[:=]\s*\S+/i,
  },
  {
    label: 'Private key',
    regex: /-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----/,
  },
  {
    label: 'Credit card',
    regex: /\b(?:\d[ -]*?){13,19}\b/,
  },
  {
    label: 'Connection string',
    regex: /(?:postgres|mysql|mongodb|redis):\/\/\S+/i,
  },
];

export function useSensitiveDetect(): (
  text: string,
) => SensitiveDetectResult | null {
  return useCallback((text: string): SensitiveDetectResult | null => {
    const matched: string[] = [];

    for (const { label, regex } of PATTERNS) {
      if (regex.test(text)) {
        matched.push(label);
      }
    }

    return matched.length > 0 ? { types: matched } : null;
  }, []);
}
