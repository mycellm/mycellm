/**
 * Log unexpected client-side errors (API failures, etc.) for debugging.
 */
export function logClientError(context: string, error: unknown): void {
  console.error(`[mycellm] ${context}`, error)
}
