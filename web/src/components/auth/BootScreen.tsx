import { useState, useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/auth'
import { useNodeStore } from '@/stores/node'

/**
 * First-run splash.
 *
 * ⚠️ TWO THINGS WERE WRONG WITH THIS AND BOTH ARE FIXED HERE.
 *
 * It played on EVERY page load — five lines at 250ms plus a 600ms pause, so
 * ~1.85s before the dashboard appeared, every refresh, on any node with an API
 * key. Opening `/network` directly felt like that tab was slow; it was the
 * shell, and every page paid it equally. It now plays once per browser
 * (`hasBooted`), and again after a logout.
 *
 * And the lines were FICTION. "Loading Ed25519 identity", "Binding QUIC
 * transport on :8421" — none of that was happening; the node booted hours ago
 * and the browser is merely connecting to it. Narrating work that is not
 * occurring is the same defect this release has been removing everywhere else,
 * just wearing a nicer font. The lines below report only things the app has
 * actually observed by this point: the version from `/health`, and the peer and
 * model counts from the `/v1/node/status` call that just succeeded.
 */
type BootLine = (n: { version: string; peers: number; models: number }) => string

const BOOT_LINES: BootLine[] = [
  ({ version }) => `Connected to mycellm node${version ? ` v${version}` : ''}`,
  ({ models }) =>
    models > 0
      ? `${models} model${models === 1 ? '' : 's'} loaded`
      : 'No models loaded on this node',
  ({ peers }) =>
    peers > 0
      ? `${peers} peer${peers === 1 ? '' : 's'} connected`
      : 'No peers connected',
]

/** Fast enough to read, short enough not to be in the way. */
const LINE_INTERVAL_MS = 110
const SETTLE_MS = 150

export function BootScreen() {
  const setAppState = useAuthStore((s) => s.setAppState)
  const setHasBooted = useAuthStore((s) => s.setHasBooted)
  const [logs, setLogs] = useState<string[]>([])
  const endRef = useRef<HTMLDivElement>(null)
  const idxRef = useRef(0)
  const startedRef = useRef(false)

  useEffect(() => {
    // Guard against double-invocation (StrictMode or dep changes)
    if (startedRef.current) return
    startedRef.current = true

    const iv = setInterval(() => {
      const idx = idxRef.current
      if (idx < BOOT_LINES.length) {
        const node = useNodeStore.getState()
        setLogs((prev) => [
          ...prev,
          BOOT_LINES[idx]({
            version: node.versionInfo?.current ?? '',
            peers: node.status?.peers?.length ?? 0,
            models: node.status?.models?.length ?? 0,
          }),
        ])
        idxRef.current = idx + 1
      } else {
        clearInterval(iv)
        setHasBooted(true)
        setTimeout(() => setAppState('dashboard'), SETTLE_MS)
      }
    }, LINE_INTERVAL_MS)

    return () => clearInterval(iv)
  }, [setAppState, setHasBooted])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <div className="min-h-screen bg-void text-console font-mono flex items-center justify-center p-6">
      <div className="max-w-2xl w-full border border-spore/20 bg-black/50 p-6 rounded-lg shadow-[0_0_30px_rgba(34,197,94,0.05)]">
        <div className="mb-8">
          <img
            src="/brand/mycellm-h-R.svg"
            alt="mycellm"
            className="h-10 sm:h-12"
          />
          <p className="text-xs text-gray-500 uppercase tracking-widest mt-2">
            Boot Sequence
          </p>
        </div>
        <div className="space-y-2 text-sm text-gray-400 h-64 overflow-y-auto pr-2 custom-scrollbar">
          {logs.map((log, i) => (
            <div key={i} className="flex">
              <span className="text-spore mr-2">❯</span>
              <span className={i === logs.length - 1 ? 'text-white' : ''}>
                {log}
              </span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>
    </div>
  )
}
