import React, { useState, useEffect, useRef } from 'react'
import { Terminal, Activity, Server, Globe, Cpu, Database, Zap, Shield, Key } from 'lucide-react'

const ASCII_SHROOM = `████████████████████
 ████████████████████
  ██████████████████████
  ██████  ████████  ████
  ██████  ████████  ████
  ██████████████████████
   ████████████████████
    ██████████████████`

const TAG_COLORS = {
  VRAM: 'text-compute',
  INFER: 'text-compute',
  P2P: 'text-relay',
  DHT: 'text-relay',
  ROUTING: 'text-relay',
  CREDIT: 'text-ledger',
  SPORES: 'text-poison',
  NODE: 'text-spore',
  API: 'text-spore',
  BOOT: 'text-spore',
}

export default function App() {
  const [appState, setAppState] = useState('booting')
  const [bootLogs, setBootLogs] = useState([])
  const [networkMode, setNetworkMode] = useState('federated')
  const [status, setStatus] = useState(null)
  const [credits, setCredits] = useState({ balance: 0, earned: 0, spent: 0 })
  const [logs, setLogs] = useState([
    { text: '[NODE] Substrate operational. Awaiting tasks.', color: 'text-spore', time: new Date().toLocaleTimeString() },
  ])
  const logsEndRef = useRef(null)

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, bootLogs])

  // Boot sequence
  useEffect(() => {
    if (appState !== 'booting') return
    const seq = [
      'Initializing mycellm-node daemon (v0.1.0)...',
      'Mounting local storage volumes...',
      'Loading Ed25519 device keypair...',
      'Reading device certificate...',
      'Binding QUIC transport...',
      'Detecting hardware...',
      'Initializing credit ledger...',
      'Starting API server...',
      'Swarm connected. Awaiting inference tasks.',
    ]
    let i = 0
    const iv = setInterval(() => {
      if (i < seq.length) {
        setBootLogs(prev => [...prev, seq[i]])
        i++
      } else {
        clearInterval(iv)
        setTimeout(() => setAppState('dashboard'), 600)
      }
    }, 300)
    return () => clearInterval(iv)
  }, [appState])

  // Fetch status periodically
  useEffect(() => {
    if (appState !== 'dashboard') return
    const fetchStatus = async () => {
      try {
        const [statusResp, creditsResp] = await Promise.all([
          fetch('/v1/node/status'),
          fetch('/v1/node/credits'),
        ])
        if (statusResp.ok) setStatus(await statusResp.json())
        if (creditsResp.ok) setCredits(await creditsResp.json())
      } catch { /* node offline */ }
    }
    fetchStatus()
    const iv = setInterval(fetchStatus, 3000)
    return () => clearInterval(iv)
  }, [appState])

  // Simulated log entries
  useEffect(() => {
    if (appState !== 'dashboard') return
    const templates = networkMode === 'federated' ? [
      { text: '[DHT] Checking bootstrap peers...', color: 'text-relay' },
      { text: '[P2P] Listening for peer connections.', color: 'text-relay' },
      { text: '[CREDIT] Ledger balance updated.', color: 'text-ledger' },
      { text: '[VRAM] Hardware health check OK.', color: 'text-compute' },
    ] : [
      { text: '[INFER] Local inference engine ready.', color: 'text-compute' },
      { text: '[API] Serving local endpoints.', color: 'text-spore' },
    ]
    const iv = setInterval(() => {
      const entry = templates[Math.floor(Math.random() * templates.length)]
      setLogs(prev => {
        const next = [...prev, { ...entry, time: new Date().toLocaleTimeString() }]
        return next.length > 80 ? next.slice(-80) : next
      })
    }, 4000)
    return () => clearInterval(iv)
  }, [appState, networkMode])

  if (appState === 'booting') {
    return (
      <div className="min-h-screen bg-void text-console font-mono flex items-center justify-center p-6">
        <div className="max-w-2xl w-full border border-spore/20 bg-black/50 p-6 rounded-lg shadow-[0_0_30px_rgba(34,197,94,0.05)]">
          <div className="flex items-center space-x-4 mb-8">
            <pre className="text-[6px] leading-none text-compute drop-shadow-[0_0_8px_rgba(239,68,68,0.5)]">{ASCII_SHROOM}</pre>
            <div>
              <h1 className="text-2xl font-bold tracking-tighter text-white">mycellm<span className="text-spore">_</span></h1>
              <p className="text-xs text-gray-500 uppercase tracking-widest">Boot Sequence</p>
            </div>
          </div>
          <div className="space-y-2 text-sm text-gray-400 h-64 overflow-y-auto pr-2 custom-scrollbar">
            {bootLogs.map((log, i) => (
              <div key={i} className="flex">
                <span className="text-spore mr-2">❯</span>
                <span className={i === bootLogs.length - 1 ? 'text-white' : ''}>{log}</span>
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
          <div className="mt-6 pt-6 border-t border-white/5 flex justify-between text-xs text-gray-600">
            <span>Identity: Loading...</span>
            <span className="animate-pulse">Loading protocol...</span>
          </div>
        </div>
      </div>
    )
  }

  const hw = status?.hardware || { gpu: 'Detecting...', vram_gb: 0, backend: 'cpu' }
  const peers = status?.peers || []
  const models = status?.models || []
  const nodeName = status?.node_name || 'mycellm-node'
  const peerId = status?.peer_id || ''

  return (
    <div className="min-h-screen bg-void text-console font-sans">
      {/* Top Nav */}
      <header className="border-b border-white/10 bg-void/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <pre className="text-[4px] leading-none text-compute drop-shadow-[0_0_5px_rgba(239,68,68,0.5)]">{ASCII_SHROOM}</pre>
            <span className="font-mono text-xl font-bold tracking-tighter text-white">
              mycellm<span className="text-spore">.</span>
            </span>
            <div className="h-4 w-px bg-white/20 mx-2" />
            <span className="font-mono text-xs bg-white/10 text-gray-300 px-2 py-1 rounded">{nodeName}</span>
          </div>
          <div className="flex items-center space-x-6 font-mono text-sm">
            <div className="flex items-center space-x-2 text-ledger drop-shadow-[0_0_8px_rgba(250,204,21,0.2)]">
              <Key size={14} />
              <span>{credits.balance?.toFixed(2) || '0.00'}</span>
            </div>
            <div className="flex items-center space-x-2 text-spore">
              <Shield size={14} />
              <span>{peerId ? 'Authorized' : 'Pending'}</span>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-8 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column */}
        <div className="lg:col-span-4 space-y-6">
          {/* Mode Toggle */}
          <div className="border border-white/10 bg-[#111] rounded-xl p-5 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-relay to-transparent opacity-50" />
            <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">Operational Mode</h2>
            <div className="flex bg-black rounded-lg p-1 border border-white/10">
              <button
                onClick={() => setNetworkMode('local')}
                className={`flex-1 flex items-center justify-center space-x-2 py-2 px-3 rounded-md text-sm font-medium transition-all ${
                  networkMode === 'local' ? 'bg-console text-black shadow-sm' : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                <Server size={16} /><span>Local Root</span>
              </button>
              <button
                onClick={() => setNetworkMode('federated')}
                className={`flex-1 flex items-center justify-center space-x-2 py-2 px-3 rounded-md text-sm font-medium transition-all ${
                  networkMode === 'federated' ? 'bg-relay text-white shadow-[0_0_15px_rgba(59,130,246,0.4)]' : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                <Globe size={16} /><span>Federated</span>
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-4 leading-relaxed">
              {networkMode === 'local'
                ? 'Node isolated. Serving local API endpoints only.'
                : 'Connected to swarm. Seeding inference layers.'}
            </p>
          </div>

          {/* Hardware Card */}
          <div className="border border-white/10 bg-[#111] rounded-xl p-5">
            <div className="flex justify-between items-center mb-4">
              <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest">Hardware</h2>
              <span className="text-xs font-mono text-compute animate-pulse">● Active</span>
            </div>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-sm mb-1">
                  <span>{hw.gpu}</span>
                  <span className="font-mono">{hw.vram_gb > 0 ? `${hw.vram_gb.toFixed(1)} GB` : 'CPU'}</span>
                </div>
                {hw.vram_gb > 0 && (
                  <div className="w-full bg-black rounded-full h-2 overflow-hidden border border-white/5">
                    <div className="h-full bg-compute transition-all" style={{ width: '60%' }} />
                  </div>
                )}
              </div>
              <div className="grid grid-cols-2 gap-4 pt-2">
                <div className="bg-black border border-white/5 p-3 rounded-lg">
                  <div className="text-xs text-gray-500 font-mono mb-1">Backend</div>
                  <div className="text-xl font-bold flex items-center space-x-2">
                    <Cpu size={16} className="text-compute" />
                    <span className="uppercase">{hw.backend}</span>
                  </div>
                </div>
                <div className="bg-black border border-white/5 p-3 rounded-lg">
                  <div className="text-xs text-gray-500 font-mono mb-1">Inference</div>
                  <div className="text-xl font-bold font-mono">
                    {status?.inference?.active || 0}/{status?.inference?.max_concurrent || 2}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column */}
        <div className="lg:col-span-8 space-y-6 flex flex-col">
          {/* Metrics Row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className={`border p-5 rounded-xl transition-colors ${
              networkMode === 'federated' ? 'border-relay/30 bg-relay/5' : 'border-white/10 bg-[#111]'
            }`}>
              <h3 className="font-mono text-xs text-gray-500 mb-2">Connected Peers</h3>
              <div className="text-3xl font-mono text-white flex items-center space-x-2">
                <Activity size={24} className={networkMode === 'federated' ? 'text-relay' : 'text-gray-600'} />
                <span>{peers.length}</span>
              </div>
            </div>
            <div className="border border-white/10 bg-[#111] p-5 rounded-xl">
              <h3 className="font-mono text-xs text-gray-500 mb-2">Active Models</h3>
              <div className="flex flex-wrap gap-2 mt-2">
                {models.length > 0
                  ? models.map((m, i) => <span key={i} className="text-xs font-mono bg-white/10 px-2 py-1 rounded">{m.name}</span>)
                  : <span className="text-xs text-gray-600">None loaded</span>}
              </div>
            </div>
            <div className="border border-white/10 bg-[#111] p-5 rounded-xl">
              <h3 className="font-mono text-xs text-gray-500 mb-2">Credits</h3>
              <div className="text-2xl font-mono text-ledger">{credits.balance?.toFixed(2)}</div>
              <div className="text-xs text-gray-500 mt-1">
                +{credits.earned?.toFixed(2)} / -{credits.spent?.toFixed(2)}
              </div>
            </div>
          </div>

          {/* Terminal Log */}
          <div className="flex-grow border border-white/10 bg-black rounded-xl overflow-hidden flex flex-col h-[400px]">
            <div className="h-10 border-b border-white/10 bg-[#111] flex items-center px-4 justify-between">
              <div className="flex items-center space-x-2 text-xs font-mono text-gray-400">
                <Terminal size={14} />
                <span>mycellm-node.log</span>
              </div>
              <div className="flex space-x-2">
                <div className="w-3 h-3 rounded-full bg-white/10" />
                <div className="w-3 h-3 rounded-full bg-white/10" />
                <div className="w-3 h-3 rounded-full bg-white/10" />
              </div>
            </div>
            <div className="p-4 font-mono text-xs leading-relaxed overflow-y-auto custom-scrollbar flex-grow bg-[#050505]">
              {logs.map((log, i) => (
                <div key={i} className="mb-1 flex hover:bg-white/[0.02]">
                  <span className="text-gray-600 mr-3 w-20 shrink-0">{log.time}</span>
                  <span className={log.color}>{log.text}</span>
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
