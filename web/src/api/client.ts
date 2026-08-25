import { useAuthStore } from '../stores/auth'
import { API } from './endpoints'

let logoutPending = false

export interface SseConnection {
  onmessage: ((event: { data: string }) => void) | null
  onerror: (() => void) | null
  close(): void
}

class ApiClient {
  private getBaseUrl(): string {
    return window.location.origin
  }

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    const apiKey = useAuthStore.getState().apiKey
    if (apiKey) {
      headers['Authorization'] = `Bearer ${apiKey}`
    }
    return headers
  }

  private async fetchWithAuth(path: string, opts?: RequestInit): Promise<Response> {
    const url = `${this.getBaseUrl()}${path}`
    const response = await fetch(url, {
      ...opts,
      headers: {
        ...this.getHeaders(),
        ...opts?.headers,
      },
    })

    if (response.status === 401) {
      // Trigger logout once — use queueMicrotask to avoid calling
      // logout() synchronously during a React render cycle.
      // logoutPending stays true permanently; hooks with
      // enabled: appState === 'dashboard' stop polling after logout,
      // so no further 401s arrive. Flag resets on next successful request.
      if (!logoutPending) {
        logoutPending = true
        queueMicrotask(() => {
          useAuthStore.getState().logout()
        })
      }
      throw new Error('Unauthorized')
    }

    return response
  }

  private async request<T>(
    path: string,
    opts?: RequestInit
  ): Promise<T> {
    const response = await this.fetchWithAuth(path, opts)

    if (response.status === 429) {
      throw new Error('Rate limited')
    }

    if (!response.ok) {
      const body = await response.text().catch(() => '')
      throw new Error(`API error ${response.status}: ${body}`)
    }

    // Reset logout flag on successful request (allows logout on future 401s after re-login)
    logoutPending = false

    const text = await response.text()
    if (!text) return undefined as T
    return JSON.parse(text) as T
  }

  // Like post(), but hands back the raw Response instead of parsed JSON/thrown
  // errors — for callers (e.g. chat streaming/retry ladders) that need to
  // branch on specific status codes (429/503) themselves rather than have
  // request() collapse them into a generic Error.
  async postRaw(path: string, body?: unknown, opts?: RequestInit): Promise<Response> {
    const response = await this.fetchWithAuth(path, {
      ...opts,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })

    if (response.ok) {
      logoutPending = false
    }

    return response
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'GET' })
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  async put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  async delete<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'DELETE',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  async remote<T>(
    nodeAddr: string,
    path: string,
    opts?: RequestInit
  ): Promise<T> {
    // Routed through the local daemon (same-origin) rather than fetched
    // cross-origin — a direct fetch to nodeAddr would hand this session's
    // Authorization header to whatever origin nodeAddr resolves to. The
    // daemon relays to nodeAddr itself and only to nodes it already has
    // approved, without attaching this session's credentials outbound.
    return this.post<T>(API.node.proxy, {
      node_addr: nodeAddr,
      path,
      method: opts?.method || 'GET',
      body: opts?.body,
    })
  }

  /**
   * POST a request and yield each `data:` payload as it arrives.
   *
   * ⚠️ NOT `stream()` WITH A BODY. `stream()` is GET-only and hands frames to a
   * callback; chat completions need a POST body and a value the caller can
   * `for await` over so it can stop on abort. Both share `readSse` below, so
   * the frame parsing exists once.
   *
   * Yields the raw payload string — including the literal `[DONE]`, which the
   * caller checks. Swallowing it here would hide the difference between "the
   * stream ended" and "the connection dropped", and those need different
   * handling.
   */
  async *postStream(
    path: string,
    body?: unknown,
    opts?: RequestInit
  ): AsyncGenerator<string, void, unknown> {
    const response = await this.fetchWithAuth(path, {
      ...opts,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    if (!response.ok) {
      const text = await response.text().catch(() => '')
      throw new StreamHttpError(response.status, text)
    }
    if (!response.body) throw new StreamHttpError(0, 'No response body')
    yield* readSse(response.body)
  }

  stream(path: string): SseConnection {
    const controller = new AbortController()
    const conn: SseConnection = {
      onmessage: null,
      onerror: null,
      close() {
        controller.abort()
      },
    }

    // Goes through fetchWithAuth (not a bare fetch) so the stream carries the
    // same Authorization: Bearer header as every other call and shares the
    // 401 -> logout path. EventSource can't set headers, which is why the key
    // used to ride in the query string; parsing text/event-stream by hand is
    // the price of getting it into a header.
    ;(async () => {
      try {
        const response = await this.fetchWithAuth(path, {
          signal: controller.signal,
        })
        if (!response.ok || !response.body) {
          conn.onerror?.()
          return
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          buffer = buffer.replace(/\r\n/g, '\n')

          const events = buffer.split('\n\n')
          buffer = events.pop() ?? ''

          for (const rawEvent of events) {
            const data = rawEvent
              .split('\n')
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.slice(5).trimStart())
              .join('\n')
            if (data) conn.onmessage?.({ data })
          }
        }

        conn.onerror?.()
      } catch {
        if (controller.signal.aborted) return
        conn.onerror?.()
      }
    })()

    return conn
  }
}

/**
 * Parse a `text/event-stream` body into its `data:` payloads.
 *
 * Split on a blank line, keep the trailing partial in the buffer — a chunk
 * boundary lands mid-frame constantly, and treating a partial frame as
 * complete produces a JSON parse error on perfectly good output.
 */
async function* readSse(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<string, void, unknown> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const data = frame
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n')
        if (data) yield data
      }
    }
  } finally {
    // Cancel rather than leak the reader when the consumer breaks early
    // (abort, or a [DONE] the caller stops on).
    try {
      await reader.cancel()
    } catch {
      /* already closed */
    }
  }
}

/** An HTTP failure on a streaming request, with the body for error rendering. */
export class StreamHttpError extends Error {
  constructor(
    readonly status: number,
    readonly body: string
  ) {
    super(`HTTP ${status}`)
    this.name = 'StreamHttpError'
  }
}

export const api = new ApiClient()
