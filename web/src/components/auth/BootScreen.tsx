import { useState, useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/auth'
import { useNodeStore } from '@/stores/node'

const BOOT_TEMPLATES: Array<(v: string) => string> = [
  (v) => `Initializing mycellm-node daemon${v ? ` (v${v})` : ''}...`,
  () => 'Loading Ed25519 identity...',
  () => 'Binding QUIC transport on :8421...',
  () => 'Connecting to bootstrap peers...',
  () => 'Node online — ready to serve.',
]

export function BootScreen() {
  const setAppState = useAuthStore((s) => s.setAppState)
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
      if (idx < BOOT_TEMPLATES.length) {
        const version = useNodeStore.getState().versionInfo?.current ?? ''
        const line = BOOT_TEMPLATES[idx](version)
        setLogs((prev) => [...prev, line])
        idxRef.current = idx + 1
      } else {
        clearInterval(iv)
        setTimeout(() => setAppState('dashboard'), 600)
      }
    }, 250)

    return () => clearInterval(iv)
  }, [setAppState])

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
