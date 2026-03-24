import { useState, useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/auth'

const BOOT_LINES = [
  'Initializing mycellm-node daemon (v0.1.2)...',
  'Loading Ed25519 identity...',
  'Binding QUIC transport on :8421...',
  'Connecting to bootstrap peers...',
  'Node online — ready to serve.',
]

export function BootScreen() {
  const setAppState = useAuthStore((s) => s.setAppState)
  const [logs, setLogs] = useState<string[]>([])
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let idx = 0
    const iv = setInterval(() => {
      if (idx < BOOT_LINES.length) {
        setLogs((prev) => [...prev, BOOT_LINES[idx]])
        idx++
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
        <div className="flex items-center space-x-4 mb-8">
          <img
            src="/brand/mycellm-red-logo-sans.svg"
            alt=""
            className="h-12 drop-shadow-[0_0_8px_rgba(239,68,68,0.3)]"
          />
          <div>
            <h1 className="text-2xl font-bold tracking-tighter text-white">
              mycellm<span className="text-spore">_</span>
            </h1>
            <p className="text-xs text-gray-500 uppercase tracking-widest">
              Boot Sequence
            </p>
          </div>
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
