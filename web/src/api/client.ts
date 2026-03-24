import { useAuthStore } from '../stores/auth'

let logoutPending = false

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

  private async request<T>(
    path: string,
    opts?: RequestInit
  ): Promise<T> {
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
    const base = nodeAddr.startsWith('http') ? nodeAddr : `http://${nodeAddr}`
    const url = `${base}${path}`
    const response = await fetch(url, {
      ...opts,
      headers: {
        ...this.getHeaders(),
        ...opts?.headers,
      },
    })

    if (!response.ok) {
      const body = await response.text().catch(() => '')
      throw new Error(`Remote API error ${response.status}: ${body}`)
    }

    const text = await response.text()
    if (!text) return undefined as T
    return JSON.parse(text) as T
  }

  stream(path: string): EventSource {
    const apiKey = useAuthStore.getState().apiKey
    const separator = path.includes('?') ? '&' : '?'
    const url = `${this.getBaseUrl()}${path}${apiKey ? `${separator}api_key=${encodeURIComponent(apiKey)}` : ''}`
    return new EventSource(url)
  }
}

export const api = new ApiClient()
