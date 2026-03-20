import React, { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react'
import {
  Terminal, Activity, Server, Globe, Cpu, Database, Zap, Shield, Key,
  Send, Plus, Trash2, RefreshCw, MessageSquare, BarChart3, Network,
  Boxes, ChevronRight, Loader2, AlertCircle, Check, X, Eye, EyeOff,
  Radio, MonitorSmartphone, ChevronDown, ArrowUpDown, ArrowUp, ArrowDown,
} from 'lucide-react'

// ── Constants ──

const ASCII_SHROOM = `████████████████████
 ████████████████████
  ██████████████████████
  ██████  ████████  ████
  ██████  ████████  ████
  ██████████████████████
   ████████████████████
    ██████████████████`

const TABS = [
  { id: 'overview', label: 'Overview', icon: BarChart3 },
  { id: 'network', label: 'Network', icon: Network },
  { id: 'models', label: 'Models', icon: Boxes },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'credits', label: 'Credits', icon: Key },
  { id: 'logs', label: 'Logs', icon: Terminal },
]

const LOG_TAG_COLORS = {
  'mycellm.inference': 'text-compute',
  'mycellm.transport': 'text-relay',
  'mycellm.router': 'text-relay',
  'mycellm.dht': 'text-relay',
  'mycellm.accounting': 'text-ledger',
  'mycellm': 'text-spore',
}

function tagColor(name) {
  for (const [prefix, color] of Object.entries(LOG_TAG_COLORS)) {
    if (name.startsWith(prefix)) return color
  }
  return 'text-gray-400'
}

// ── API key management ──

function getApiKey() {
  return localStorage.getItem('mycellm_api_key') || ''
}

function setApiKey(key) {
  localStorage.setItem('mycellm_api_key', key)
}

function authHeaders() {
  const key = getApiKey()
  return key ? { 'Authorization': `Bearer ${key}` } : {}
}

// ── API helpers ──

async function api(path, opts = {}) {
  const headers = { ...authHeaders(), ...(opts.headers || {}) }
  const resp = await fetch(path, { ...opts, headers })
  if (resp.status === 401) throw new Error('unauthorized')
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
  return resp.json()
}

/** Call a remote node's API directly (browser → peer IP). */
async function remoteApi(nodeAddr, path, opts = {}) {
  const base = nodeAddr.startsWith('http') ? nodeAddr : `http://${nodeAddr}`
  const headers = { ...authHeaders(), ...(opts.headers || {}) }
  const resp = await fetch(`${base}${path}`, { ...opts, headers })
  if (resp.status === 401) throw new Error('unauthorized')
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
  return resp.json()
}

// ── Node Context (for managed nodes registry) ──

const NodeRegistryContext = createContext(null)

function useNodeRegistry() {
  return useContext(NodeRegistryContext)
}

/** Persistent node registry stored in localStorage */
function useManagedNodes() {
  const [nodes, setNodes] = useState(() => {
    try {
      const saved = localStorage.getItem('mycellm_managed_nodes')
      return saved ? JSON.parse(saved) : []
    } catch { return [] }
  })

  const save = (updated) => {
    setNodes(updated)
    localStorage.setItem('mycellm_managed_nodes', JSON.stringify(updated))
  }

  const addNode = (addr, label) => {
    if (nodes.find(n => n.addr === addr)) return
    save([...nodes, { addr, label, addedAt: Date.now() }])
  }

  const removeNode = (addr) => {
    save(nodes.filter(n => n.addr !== addr))
  }

  return { nodes, addNode, removeNode }
}

// ── Boot Screen ──

function BootScreen({ onDone }) {
  const [bootLogs, setBootLogs] = useState([])
  const endRef = useRef(null)

  useEffect(() => {
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
        setTimeout(onDone, 600)
      }
    }, 250)
    return () => clearInterval(iv)
  }, [onDone])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [bootLogs])

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
          <div ref={endRef} />
        </div>
      </div>
    </div>
  )
}

// ── Reusable Components ──

function StatCard({ label, value, sub, icon: Icon, color = 'text-white', highlight = false }) {
  return (
    <div className={`border p-5 rounded-xl transition-colors ${highlight ? 'border-relay/30 bg-relay/5' : 'border-white/10 bg-[#111]'}`}>
      <h3 className="font-mono text-xs text-gray-500 mb-2">{label}</h3>
      <div className="flex items-center space-x-2">
        {Icon && <Icon size={22} className={color} />}
        <span className={`text-3xl font-mono ${color}`}>{value}</span>
      </div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  )
}

function NodeSelector({ selected, onSelect, includeLocal = true }) {
  const { nodes } = useNodeRegistry()
  const allNodes = [
    ...(includeLocal ? [{ addr: '', label: 'This node (local)' }] : []),
    ...nodes,
  ]
  if (allNodes.length <= 1 && includeLocal) return null
  return (
    <div className="flex items-center space-x-2 mb-4">
      <MonitorSmartphone size={14} className="text-gray-500" />
      <select value={selected} onChange={e => onSelect(e.target.value)}
        className="bg-black border border-white/10 rounded-lg px-3 py-1.5 text-sm font-mono text-white focus:border-spore/50 focus:outline-none">
        {allNodes.map(n => (
          <option key={n.addr} value={n.addr}>{n.label || n.addr}</option>
        ))}
      </select>
    </div>
  )
}

// ── System Info Panel (reusable) ──

function SystemInfoPanel({ sysInfo, compact = false }) {
  if (!sysInfo) return null
  const cpu = sysInfo.cpu || {}
  const mem = sysInfo.memory || {}
  const disk = sysInfo.disk || {}
  const gpu = sysInfo.gpu || {}
  const os_ = sysInfo.os || {}

  const osLabel = os_.distro || (os_.macos_version ? `macOS ${os_.macos_version}` : os_.system || '?')
  const cpuName = cpu.name || cpu.processor || '?'

  if (compact) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <div>
          <span className="text-gray-500">CPU</span>
          <div className="text-gray-300 mt-0.5 truncate" title={cpuName}>{cpuName}</div>
        </div>
        <div>
          <span className="text-gray-500">RAM</span>
          <div className="text-gray-300 mt-0.5">{mem.total_gb || '?'} GB{mem.used_pct ? ` (${mem.used_pct}% used)` : ''}</div>
        </div>
        <div>
          <span className="text-gray-500">GPU</span>
          <div className="text-gray-300 mt-0.5">{gpu.gpu || 'CPU'} / {gpu.backend || 'cpu'}</div>
        </div>
        <div>
          <span className="text-gray-500">OS</span>
          <div className="text-gray-300 mt-0.5 truncate" title={osLabel}>{osLabel}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* CPU + OS row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-black border border-white/5 p-4 rounded-lg">
          <div className="text-xs text-gray-500 mb-2">CPU</div>
          <div className="text-sm font-medium text-white truncate" title={cpuName}>{cpuName}</div>
          <div className="flex space-x-4 mt-2 text-xs text-gray-500">
            <span>{cpu.arch}</span>
            <span>{cpu.cores_physical} cores</span>
            {cpu.cores_performance && <span>{cpu.cores_performance}P + {cpu.cores_efficiency}E</span>}
          </div>
        </div>
        <div className="bg-black border border-white/5 p-4 rounded-lg">
          <div className="text-xs text-gray-500 mb-2">Operating System</div>
          <div className="text-sm font-medium text-white">{osLabel}</div>
          <div className="flex space-x-4 mt-2 text-xs text-gray-500">
            <span>{os_.hostname}</span>
            <span>Python {sysInfo.python}</span>
            <span>mycellm {sysInfo.mycellm_version}</span>
          </div>
        </div>
      </div>

      {/* Memory + Disk + GPU row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-black border border-white/5 p-4 rounded-lg">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs text-gray-500">Memory (RAM)</span>
            <span className="text-xs font-mono text-gray-400">{mem.total_gb} GB</span>
          </div>
          {mem.total_gb > 0 && (
            <div className="w-full bg-void rounded-full h-2 overflow-hidden border border-white/5">
              <div className="h-full bg-relay transition-all" style={{ width: `${mem.used_pct || 0}%` }} />
            </div>
          )}
          <div className="flex justify-between mt-1.5 text-xs text-gray-600">
            <span>{mem.available_gb} GB free</span>
            <span>{mem.used_pct}% used</span>
          </div>
        </div>

        <div className="bg-black border border-white/5 p-4 rounded-lg">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs text-gray-500">Disk</span>
            <span className="text-xs font-mono text-gray-400">{disk.total_gb} GB</span>
          </div>
          {disk.total_gb > 0 && (
            <div className="w-full bg-void rounded-full h-2 overflow-hidden border border-white/5">
              <div className="h-full bg-ledger transition-all" style={{ width: `${disk.used_pct || 0}%` }} />
            </div>
          )}
          <div className="flex justify-between mt-1.5 text-xs text-gray-600">
            <span>{disk.free_gb} GB free</span>
            <span>{disk.used_pct}% used</span>
          </div>
        </div>

        <div className="bg-black border border-white/5 p-4 rounded-lg">
          <div className="text-xs text-gray-500 mb-2">GPU / Accelerator</div>
          <div className="text-sm font-medium flex items-center space-x-2">
            <Cpu size={14} className="text-compute" />
            <span className="text-white">{gpu.gpu || 'CPU'}</span>
          </div>
          <div className="flex space-x-4 mt-2 text-xs text-gray-500">
            <span className="uppercase">{gpu.backend || 'cpu'}</span>
            {gpu.vram_gb > 0 && <span>{gpu.vram_gb} GB</span>}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Overview Tab ──

function OverviewTab({ status, credits }) {
  const [sysInfo, setSysInfo] = useState(null)
  const peers = status?.peers || []
  const models = status?.models || []

  useEffect(() => {
    api('/v1/node/system').then(setSysInfo).catch(() => {})
  }, [])

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard label="Connected Peers" value={peers.length} icon={Activity} color="text-relay" />
        <StatCard label="Loaded Models" value={models.length} icon={Boxes} color="text-spore" />
        <StatCard label="Credit Balance" value={credits.balance?.toFixed(2)} icon={Key} color="text-ledger"
          sub={`+${credits.earned?.toFixed(2) || '0.00'} / -${credits.spent?.toFixed(2) || '0.00'}`} />
        <StatCard label="Active Inference" value={`${status?.inference?.active || 0}/${status?.inference?.max_concurrent || 2}`}
          icon={Zap} color="text-compute" />
      </div>

      {/* System Info */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">System</h2>
        {sysInfo ? <SystemInfoPanel sysInfo={sysInfo} /> : (
          <div className="text-sm text-gray-500 flex items-center space-x-2">
            <Loader2 size={14} className="animate-spin" /><span>Loading system info...</span>
          </div>
        )}
      </div>

      {/* Peers quick list */}
      {peers.length > 0 && (
        <div className="border border-white/10 bg-[#111] rounded-xl p-5">
          <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">Connected Peers</h2>
          <div className="space-y-2">
            {peers.map((p, i) => (
              <div key={i} className="flex items-center justify-between bg-black border border-white/5 p-3 rounded-lg text-sm">
                <div className="flex items-center space-x-3">
                  <div className="w-2 h-2 rounded-full bg-spore animate-pulse" />
                  <span className="font-mono text-gray-300">{p.peer_id?.slice(0, 16)}...</span>
                </div>
                <div className="flex items-center space-x-4 text-xs text-gray-500">
                  <span>{p.role}</span>
                  <span>{p.models?.join(', ') || 'no models'}</span>
                  <span className={p.status === 'routable' ? 'text-spore' : 'text-gray-600'}>{p.status}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Network Tab (with auto-discovered + manual nodes) ──

function NetworkTab({ status }) {
  const { nodes: manualNodes, addNode, removeNode } = useNodeRegistry()
  const [registryNodes, setRegistryNodes] = useState([])
  const [networkModels, setNetworkModels] = useState([])
  const [newAddr, setNewAddr] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const peers = status?.peers || []

  // Poll the server-side node registry (auto-announced nodes)
  useEffect(() => {
    const fetch_ = () => api('/v1/admin/nodes').then(d => setRegistryNodes(d.nodes || [])).catch(() => {})
    fetch_()
    const iv = setInterval(fetch_, 5000)
    return () => clearInterval(iv)
  }, [])

  // Fetch network-wide models from local node
  useEffect(() => {
    const fetch_ = () => api('/v1/models').then(d => setNetworkModels(d.data || [])).catch(() => {})
    fetch_()
    const iv = setInterval(fetch_, 5000)
    return () => clearInterval(iv)
  }, [])

  const handleAddNode = () => {
    if (!newAddr.trim()) return
    let addr = newAddr.trim()
    if (!addr.includes(':')) addr += ':8420'
    addNode(addr, newLabel.trim() || addr)
    setNewAddr('')
    setNewLabel('')
  }

  const handleApprove = async (peerId) => {
    try {
      await api(`/v1/admin/nodes/${peerId}/approve`, { method: 'POST' })
      // Refresh registry
      api('/v1/admin/nodes').then(d => setRegistryNodes(d.nodes || [])).catch(() => {})
    } catch {}
  }

  const handleRemoveRegistry = async (peerId) => {
    try {
      await api(`/v1/admin/nodes/${peerId}/remove`, { method: 'POST' })
      api('/v1/admin/nodes').then(d => setRegistryNodes(d.nodes || [])).catch(() => {})
    } catch {}
  }

  return (
    <div className="space-y-6">
      {/* Auto-discovered Nodes (from registry) */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
          Fleet ({registryNodes.length} node{registryNodes.length !== 1 ? 's' : ''})
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          Nodes announce themselves when they start with this node as bootstrap.
          Approve to enable management from this dashboard.
        </p>

        <div className="space-y-3">
          {registryNodes.map((rn) => {
            const sys = rn.system || {}
            const cpu = sys.cpu || {}
            const mem = sys.memory || {}
            const gpu = sys.gpu || {}
            const os_ = sys.os || {}
            const caps = rn.capabilities || {}
            const models = caps.models || []
            const isPending = rn.status === 'pending'
            const osLabel = os_.distro || (os_.macos_version ? `macOS ${os_.macos_version}` : os_.system || '?')

            return (
              <div key={rn.peer_id} className={`border rounded-xl p-4 ${
                isPending ? 'border-ledger/30 bg-ledger/5' : rn.online ? 'border-white/10 bg-black' : 'border-compute/20 bg-compute/5'
              }`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-3">
                    <div className={`w-2.5 h-2.5 rounded-full ${
                      isPending ? 'bg-ledger animate-pulse' : rn.online ? 'bg-spore animate-pulse' : 'bg-compute'
                    }`} />
                    <span className="font-mono text-sm font-medium">{rn.node_name || 'unnamed'}</span>
                    <span className="font-mono text-xs text-gray-500">{rn.api_addr}</span>
                    <span className="font-mono text-xs text-gray-600">{rn.peer_id?.slice(0, 12)}...</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    {isPending && (
                      <button onClick={() => handleApprove(rn.peer_id)}
                        className="flex items-center space-x-1 bg-spore text-black px-2.5 py-1 rounded text-xs font-medium hover:bg-spore/90 transition-all">
                        <Check size={12} /><span>Approve</span>
                      </button>
                    )}
                    <span className={`text-xs font-mono px-2 py-0.5 rounded ${
                      isPending ? 'bg-ledger/10 text-ledger' :
                      rn.online ? 'bg-spore/10 text-spore' : 'bg-compute/10 text-compute'
                    }`}>
                      {isPending ? 'pending' : rn.online ? 'approved' : 'offline'}
                    </span>
                    <button onClick={() => handleRemoveRegistry(rn.peer_id)}
                      className="text-gray-600 hover:text-compute transition-colors p-1">
                      <X size={14} />
                    </button>
                  </div>
                </div>

                {/* Compact system info */}
                {sys.cpu && (
                  <div className="mt-3 pt-3 border-t border-white/5">
                    <SystemInfoPanel sysInfo={sys} compact />
                  </div>
                )}

                {/* Models */}
                {models.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-white/5 flex flex-wrap gap-1.5">
                    {models.map((m, i) => (
                      <span key={i} className="text-xs font-mono bg-spore/10 text-spore px-2 py-0.5 rounded">
                        {m.name || m}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          {registryNodes.length === 0 && (
            <div className="text-center py-6 text-gray-600 text-sm">
              <Radio size={24} className="mx-auto mb-2 opacity-30" />
              <p>No nodes have announced yet.</p>
              <p className="text-xs mt-1">Start remote nodes with MYCELLM_BOOTSTRAP_PEERS pointing here.</p>
            </div>
          )}
        </div>
      </div>

      {/* Manual node add (fallback) */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">Add Node Manually</h2>
        <div className="flex space-x-2">
          <input value={newLabel} onChange={e => setNewLabel(e.target.value)}
            placeholder="Label"
            className="w-32 bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
          <input value={newAddr} onChange={e => setNewAddr(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAddNode()}
            placeholder="IP:port (e.g. 10.1.1.11:8420)"
            className="flex-grow bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
          <button onClick={handleAddNode} disabled={!newAddr.trim()}
            className="bg-white/10 text-white px-3 py-2 rounded-lg text-sm font-medium hover:bg-white/20 disabled:opacity-40 transition-all">
            <Plus size={14} />
          </button>
        </div>
      </div>

      {/* Network Models */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">Models Across Network</h2>
        {networkModels.length > 0 ? (
          <div className="space-y-2">
            {networkModels.map((m, i) => (
              <div key={i} className="flex items-center justify-between bg-black border border-white/5 p-3 rounded-lg text-sm">
                <div className="flex items-center space-x-3">
                  <Boxes size={14} className={m.owned_by === 'local' ? 'text-spore' : 'text-relay'} />
                  <span className="font-mono">{m.id}</span>
                </div>
                <span className={`text-xs font-mono px-2 py-1 rounded ${
                  m.owned_by === 'local' ? 'bg-spore/10 text-spore' : 'bg-relay/10 text-relay'
                }`}>{m.owned_by}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No models available on the network.</p>
        )}
      </div>

      {/* QUIC Peers (from protocol) */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
          QUIC Peers ({peers.length})
        </h2>
        {peers.length > 0 ? (
          <div className="space-y-3">
            {peers.map((p, i) => (
              <div key={i} className="bg-black border border-white/5 p-4 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-2">
                    <div className={`w-2 h-2 rounded-full ${p.status === 'routable' ? 'bg-spore' : 'bg-gray-600'}`} />
                    <span className="font-mono text-sm">{p.peer_id?.slice(0, 24)}...</span>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded font-mono ${
                    p.status === 'routable' ? 'bg-spore/10 text-spore' : 'bg-gray-800 text-gray-500'
                  }`}>{p.status}</span>
                </div>
                <div className="flex space-x-6 text-xs text-gray-500">
                  <span>Role: <span className="text-gray-300">{p.role}</span></span>
                  <span>Models: <span className="text-gray-300">{p.models?.length > 0 ? p.models.join(', ') : 'none'}</span></span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-gray-500">
            <Network size={32} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">No QUIC peers connected yet.</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Models Tab (device list + model config) ──

function SortHeader({ label, field, sortBy, sortDir, onSort }) {
  const active = sortBy === field
  return (
    <button onClick={() => onSort(field)} className="flex items-center space-x-1 text-left group">
      <span>{label}</span>
      {active ? (sortDir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)
        : <ArrowUpDown size={12} className="opacity-0 group-hover:opacity-50" />}
    </button>
  )
}

function ModelsTab({ status, onRefresh }) {
  const [devices, setDevices] = useState([]) // merged local + fleet
  const [selected, setSelected] = useState('local') // 'local' or api_addr
  const [sortBy, setSortBy] = useState('name')
  const [sortDir, setSortDir] = useState('asc')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [backendType, setBackendType] = useState('openai')
  const [remoteStatus, setRemoteStatus] = useState(null)

  const [form, setForm] = useState({
    name: '', model_path: '',
    api_base: 'https://openrouter.ai/api/v1', api_key: '', api_model: '', ctx_len: 4096,
    api_key_hint: '',
  })
  const [showKey, setShowKey] = useState(false)

  // Build device list: local node + approved fleet nodes
  useEffect(() => {
    const fetchDevices = async () => {
      const localHw = status?.hardware || {}
      const localDevice = {
        id: 'local', name: status?.node_name || 'this node', addr: 'local',
        gpu: localHw.gpu || 'CPU', backend: localHw.backend || 'cpu',
        ram: localHw.vram_gb || 0, models: status?.models || [],
        online: true, role: status?.role || 'bootstrap',
      }

      let fleet = []
      try {
        const resp = await api('/v1/admin/nodes')
        fleet = (resp.nodes || []).filter(n => n.status === 'approved').map(n => {
          const hw = n.system?.gpu || n.capabilities?.hardware || {}
          const mem = n.system?.memory || {}
          const models = n.capabilities?.models || []
          return {
            id: n.peer_id, name: n.node_name || n.api_addr, addr: n.api_addr,
            gpu: hw.gpu || 'CPU', backend: hw.backend || 'cpu',
            ram: mem.total_gb || hw.vram_gb || 0,
            models: models.map(m => typeof m === 'string' ? { name: m } : m),
            online: n.online, role: n.role || 'seeder',
          }
        })
      } catch {}

      setDevices([localDevice, ...fleet])
    }
    fetchDevices()
    const iv = setInterval(fetchDevices, 5000)
    return () => clearInterval(iv)
  }, [status])

  // Fetch selected remote node's live models
  const selectedDevice = devices.find(d => d.id === selected || d.addr === selected)
  const isRemote = selected !== 'local'

  useEffect(() => {
    if (!isRemote || !selectedDevice?.addr) { setRemoteStatus(null); return }
    const fetch_ = () => remoteApi(selectedDevice.addr, '/v1/node/status')
      .then(setRemoteStatus).catch(() => setRemoteStatus(null))
    fetch_()
    const iv = setInterval(fetch_, 5000)
    return () => clearInterval(iv)
  }, [selected, isRemote, selectedDevice?.addr])

  const models = isRemote ? (remoteStatus?.models || selectedDevice?.models || []) : (status?.models || [])

  // Sort devices
  const handleSort = (field) => {
    if (sortBy === field) { setSortDir(d => d === 'asc' ? 'desc' : 'asc') }
    else { setSortBy(field); setSortDir('asc') }
  }

  const sorted = [...devices].sort((a, b) => {
    let va = a[sortBy], vb = b[sortBy]
    if (typeof va === 'string') va = va.toLowerCase()
    if (typeof vb === 'string') vb = vb.toLowerCase()
    if (typeof va === 'number' && typeof vb === 'number') return sortDir === 'asc' ? va - vb : vb - va
    return sortDir === 'asc' ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
  })

  // API helpers for selected device
  const doApi = async (path, body) => {
    const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    return isRemote ? remoteApi(selectedDevice.addr, path, opts) : api(path, opts)
  }

  const handleLoad = async () => {
    setLoading(true); setResult(null)
    try {
      // If editing, unload old model first
      if (editingModel) {
        await doApi('/v1/node/models/unload', { model: editingModel })
      }
      const body = { backend: backendType, name: form.name }
      if (backendType === 'llama.cpp') { body.model_path = form.model_path }
      else {
        body.api_base = form.api_base; body.api_key = form.api_key
        body.api_model = form.api_model; body.ctx_len = parseInt(form.ctx_len) || 4096
      }
      const resp = await doApi('/v1/node/models/load', body)
      const target = isRemote ? ` on ${selectedDevice.name}` : ''
      const action = editingModel ? 'Updated' : 'Loaded'
      setResult(resp.error ? { error: resp.error } : { success: `${action} "${resp.model}"${target}` })
      if (!resp.error) { setEditingModel(null); onRefresh() }
    } catch (e) { setResult({ error: e.message }) }
    setLoading(false)
  }

  const [editingModel, setEditingModel] = useState(null) // name of model being edited

  const handleEdit = async (m) => {
    setEditingModel(m.name)
    setBackendType(m.backend === 'llama.cpp' ? 'llama.cpp' : 'openai')
    setResult(null)

    // Fetch full config from the node
    try {
      const fetcher = isRemote
        ? () => remoteApi(selectedDevice.addr, `/v1/node/models/${encodeURIComponent(m.name)}/config`)
        : () => api(`/v1/node/models/${encodeURIComponent(m.name)}/config`)
      const cfg = await fetcher()
      setForm(f => ({
        ...f,
        name: cfg.name || m.name,
        api_base: cfg.api_base || f.api_base,
        api_model: cfg.api_model || '',
        api_key: '', // never pre-fill — user must re-enter
        api_key_hint: cfg.api_key_hint || '',
        ctx_len: cfg.ctx_len || 4096,
        model_path: cfg.model_path || '',
      }))
    } catch {
      // Fallback: just fill what we know
      setForm(f => ({ ...f, name: m.name, ctx_len: m.ctx_len || 4096 }))
    }
  }

  const handleUnload = async (modelName) => {
    try {
      await doApi('/v1/node/models/unload', { model: modelName })
      if (editingModel === modelName) setEditingModel(null)
      onRefresh()
    }
    catch (e) { setResult({ error: e.message }) }
  }

  const thClass = 'text-left font-mono text-xs text-gray-500 uppercase tracking-wider py-2 px-3'

  return (
    <div className="space-y-6">
      {/* Device Table */}
      <div className="border border-white/10 bg-[#111] rounded-xl overflow-hidden">
        <table className="w-full">
          <thead className="border-b border-white/10 bg-black/30">
            <tr>
              <th className={thClass}><SortHeader label="Device" field="name" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} /></th>
              <th className={`${thClass} hidden md:table-cell`}><SortHeader label="Address" field="addr" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} /></th>
              <th className={`${thClass} hidden lg:table-cell`}><SortHeader label="GPU" field="gpu" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} /></th>
              <th className={`${thClass} hidden lg:table-cell`}><SortHeader label="Backend" field="backend" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} /></th>
              <th className={`${thClass} hidden md:table-cell`}><SortHeader label="RAM" field="ram" sortBy={sortBy} sortDir={sortDir} onSort={handleSort} /></th>
              <th className={thClass}>Models</th>
              <th className={`${thClass} w-16`}>Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((d) => {
              const isSelected = (d.id === selected) || (d.addr === selected)
              return (
                <tr key={d.id} onClick={() => setSelected(d.id === 'local' ? 'local' : d.addr || d.id)}
                  className={`cursor-pointer border-b border-white/5 transition-colors ${
                    isSelected ? 'bg-spore/10 border-l-2 border-l-spore' : 'hover:bg-white/[0.03]'
                  }`}>
                  <td className="px-3 py-3">
                    <div className="flex items-center space-x-2">
                      <div className={`w-2 h-2 rounded-full ${d.online ? 'bg-spore' : 'bg-compute'}`} />
                      <span className="font-mono text-sm text-white">{d.name}</span>
                      {d.role && <span className="text-xs text-gray-600">{d.role}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-3 hidden md:table-cell font-mono text-xs text-gray-500">{d.addr}</td>
                  <td className="px-3 py-3 hidden lg:table-cell text-sm text-gray-400 truncate max-w-[160px]" title={d.gpu}>{d.gpu}</td>
                  <td className="px-3 py-3 hidden lg:table-cell text-xs text-gray-500 uppercase">{d.backend}</td>
                  <td className="px-3 py-3 hidden md:table-cell text-sm text-gray-400">{d.ram > 0 ? `${d.ram} GB` : '-'}</td>
                  <td className="px-3 py-3">
                    {d.models.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {d.models.map((m, i) => (
                          <span key={i} className="text-xs font-mono bg-spore/10 text-spore px-1.5 py-0.5 rounded">
                            {m.name || m}
                          </span>
                        ))}
                      </div>
                    ) : <span className="text-xs text-gray-600">none</span>}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`text-xs font-mono ${d.online ? 'text-spore' : 'text-compute'}`}>
                      {d.online ? 'online' : 'offline'}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Selected Device — Model Management */}
      {selectedDevice && (
        <div className="border border-spore/20 bg-[#111] rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <MonitorSmartphone size={16} className="text-spore" />
              <h2 className="font-mono text-sm font-medium text-white">{selectedDevice.name}</h2>
              {isRemote && <span className="text-xs font-mono text-gray-500">{selectedDevice.addr}</span>}
            </div>
            <span className="text-xs font-mono text-gray-500">{selectedDevice.gpu} / {selectedDevice.backend}</span>
          </div>

          {/* Loaded Models */}
          {models.length > 0 && (
            <div className="mb-5">
              <h3 className="font-mono text-xs text-gray-500 mb-2">Loaded Models</h3>
              <div className="space-y-1.5">
                {models.map((m, i) => (
                  <div key={i} className={`flex items-center justify-between p-2.5 rounded-lg border transition-colors ${
                    editingModel === m.name ? 'bg-relay/10 border-relay/30' : 'bg-black border-white/5'
                  }`}>
                    <div className="flex items-center space-x-3 cursor-pointer" onClick={() => handleEdit(m)}>
                      <Boxes size={13} className="text-spore" />
                      <span className="font-mono text-sm">{m.name}</span>
                      <span className="text-xs text-gray-600 bg-white/5 px-1.5 py-0.5 rounded">{m.backend}</span>
                    </div>
                    <div className="flex items-center space-x-1">
                      <button onClick={() => handleEdit(m)} title="Edit"
                        className="text-gray-600 hover:text-relay transition-colors p-1">
                        <RefreshCw size={13} />
                      </button>
                      <button onClick={() => handleUnload(m.name)} title="Unload"
                        className="text-gray-600 hover:text-compute transition-colors p-1">
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Load Model Form */}
          <div className="border-t border-white/5 pt-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-mono text-xs text-gray-500">
                {editingModel ? `Edit: ${editingModel}` : 'Load Model'}
              </h3>
              {editingModel && (
                <button onClick={() => { setEditingModel(null); setForm(f => ({ ...f, name: '' })); setResult(null) }}
                  className="text-xs text-gray-500 hover:text-gray-300">Cancel edit</button>
              )}
            </div>

            <div className="flex bg-black rounded-lg p-1 border border-white/10 mb-4">
              <button onClick={() => setBackendType('openai')}
                className={`flex-1 py-1.5 px-3 rounded-md text-xs font-medium transition-all ${
                  backendType === 'openai' ? 'bg-relay text-white' : 'text-gray-500 hover:text-gray-300'
                }`}>
                <Globe size={12} className="inline mr-1.5" />OpenAI API
              </button>
              <button onClick={() => setBackendType('llama.cpp')}
                className={`flex-1 py-1.5 px-3 rounded-md text-xs font-medium transition-all ${
                  backendType === 'llama.cpp' ? 'bg-compute text-white' : 'text-gray-500 hover:text-gray-300'
                }`}>
                <Cpu size={12} className="inline mr-1.5" />Local GGUF
              </button>
            </div>

            <div className="space-y-2.5">
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Model name (on network)"
                className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />

              {backendType === 'llama.cpp' ? (
                <input value={form.model_path} onChange={e => setForm(f => ({ ...f, model_path: e.target.value }))}
                  placeholder="GGUF path on target node"
                  className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
              ) : (
                <>
                  <input value={form.api_base} onChange={e => setForm(f => ({ ...f, api_base: e.target.value }))}
                    placeholder="API base URL"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                  <div className="relative">
                    <input type={showKey ? 'text' : 'password'} value={form.api_key}
                      onChange={e => setForm(f => ({ ...f, api_key: e.target.value }))}
                      placeholder={editingModel && form.api_key_hint ? `Current key: ${form.api_key_hint} (re-enter to update)` : 'API key'}
                      className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 pr-10 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                    <button onClick={() => setShowKey(!showKey)}
                      className="absolute right-2 top-2 text-gray-500 hover:text-gray-300">
                      {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  <div className="flex space-x-2">
                    <input value={form.api_model} onChange={e => setForm(f => ({ ...f, api_model: e.target.value }))}
                      placeholder="Upstream model ID"
                      className="flex-grow bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                    <input type="number" value={form.ctx_len}
                      onChange={e => setForm(f => ({ ...f, ctx_len: e.target.value }))}
                      placeholder="ctx"
                      className="w-24 bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                  </div>
                </>
              )}

              <button onClick={handleLoad} disabled={loading || !form.name}
                className="flex items-center space-x-2 bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                <span>{loading ? 'Loading...' : editingModel ? 'Update Model' : 'Load Model'}</span>
              </button>

              {result && (
                <div className={`flex items-center space-x-2 text-sm p-2.5 rounded-lg ${
                  result.error ? 'bg-compute/10 text-compute' : 'bg-spore/10 text-spore'
                }`}>
                  {result.error ? <AlertCircle size={14} /> : <Check size={14} />}
                  <span>{result.error || result.success}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Chat Tab ──

function ChatTab() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [sending, setSending] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    api('/v1/models').then(d => {
      const list = d.data || []
      setModels(list)
      if (list.length > 0 && !model) setModel(list[0].id)
    }).catch(() => {})
  }, [])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const send = async () => {
    if (!input.trim() || sending) return
    const userMsg = { role: 'user', content: input.trim() }
    const history = [...messages, userMsg]
    setMessages(history)
    setInput('')
    setSending(true)

    try {
      const resp = await api('/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: model || undefined,
          messages: history.map(m => ({ role: m.role, content: m.content })),
          max_tokens: 2048,
        }),
      })
      const text = resp.choices?.[0]?.message?.content || '[no response]'
      const usage = resp.usage || {}
      setMessages([...history, {
        role: 'assistant', content: text, model: resp.model,
        tokens: `${usage.prompt_tokens || 0}+${usage.completion_tokens || 0}`,
      }])
    } catch (e) {
      setMessages([...history, { role: 'assistant', content: `[Error: ${e.message}]` }])
    }
    setSending(false)
  }

  return (
    <div className="border border-white/10 bg-[#111] rounded-xl overflow-hidden flex flex-col h-[calc(100vh-220px)]">
      {/* Model selector */}
      <div className="h-12 border-b border-white/10 bg-black/50 flex items-center px-4 space-x-3">
        <MessageSquare size={14} className="text-spore" />
        <select value={model} onChange={e => setModel(e.target.value)}
          className="bg-black border border-white/10 rounded px-2 py-1 text-sm font-mono text-white focus:outline-none">
          {models.map(m => <option key={m.id} value={m.id}>{m.id} ({m.owned_by})</option>)}
          {models.length === 0 && <option value="">No models available</option>}
        </select>
        <button onClick={() => api('/v1/models').then(d => { setModels(d.data || []) }).catch(() => {})}
          className="text-gray-500 hover:text-gray-300 transition-colors">
          <RefreshCw size={12} />
        </button>
        <button onClick={() => setMessages([])}
          className="ml-auto text-xs text-gray-500 hover:text-gray-300 flex items-center space-x-1">
          <Trash2 size={12} /><span>Clear</span>
        </button>
      </div>

      {/* Messages */}
      <div className="flex-grow overflow-y-auto p-4 space-y-4 custom-scrollbar">
        {messages.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            <MessageSquare size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">Send a message to test inference routing.</p>
            <p className="text-xs mt-1 text-gray-600">Requests route through the network to the best available peer.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-xl px-4 py-3 text-sm ${
              m.role === 'user'
                ? 'bg-relay/20 text-white border border-relay/20'
                : 'bg-black border border-white/10 text-gray-200'
            }`}>
              <div className="whitespace-pre-wrap">{m.content}</div>
              {m.model && (
                <div className="mt-2 pt-2 border-t border-white/5 text-xs text-gray-500 flex space-x-3">
                  <span>via {m.model}</span>
                  {m.tokens && <span>{m.tokens} tokens</span>}
                </div>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-black border border-white/10 rounded-xl px-4 py-3">
              <Loader2 size={16} className="animate-spin text-spore" />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div className="border-t border-white/10 p-3 bg-black/50">
        <div className="flex space-x-2">
          <input value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
            placeholder="Type a message..."
            className="flex-grow bg-black border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:border-spore/50 focus:outline-none" />
          <button onClick={send} disabled={sending || !input.trim()}
            className="bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 transition-all">
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Credits Tab ──

function CreditsTab({ credits }) {
  const [history, setHistory] = useState([])

  useEffect(() => {
    const fetch_ = () => api('/v1/node/credits/history?limit=100')
      .then(d => setHistory(d.transactions || []))
      .catch(() => {})
    fetch_()
    const iv = setInterval(fetch_, 5000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard label="Balance" value={credits.balance?.toFixed(2)} icon={Key} color="text-ledger" />
        <StatCard label="Total Earned" value={`+${credits.earned?.toFixed(2) || '0.00'}`} icon={Zap} color="text-spore" />
        <StatCard label="Total Spent" value={`-${credits.spent?.toFixed(2) || '0.00'}`} icon={BarChart3} color="text-compute" />
      </div>

      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">Transaction History</h2>
        {history.length > 0 ? (
          <div className="space-y-1 max-h-[500px] overflow-y-auto custom-scrollbar">
            {history.map((tx, i) => (
              <div key={i} className="flex items-center justify-between text-sm py-2 border-b border-white/5">
                <div className="flex items-center space-x-3">
                  <div className={`w-1.5 h-1.5 rounded-full ${tx.amount >= 0 ? 'bg-spore' : 'bg-compute'}`} />
                  <span className="text-gray-400 font-mono text-xs w-16">{tx.timestamp || ''}</span>
                  <span className="text-gray-300">{tx.reason}</span>
                </div>
                <span className={`font-mono text-sm ${tx.amount >= 0 ? 'text-spore' : 'text-compute'}`}>
                  {tx.amount >= 0 ? '+' : ''}{tx.amount?.toFixed(4)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500 text-center py-8">No transactions yet.</p>
        )}
      </div>
    </div>
  )
}

// ── Logs Tab ──

function LogsTab({ logs }) {
  const endRef = useRef(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, autoScroll])

  return (
    <div className="border border-white/10 bg-black rounded-xl overflow-hidden flex flex-col h-[calc(100vh-220px)]">
      <div className="h-10 border-b border-white/10 bg-[#111] flex items-center px-4 justify-between">
        <div className="flex items-center space-x-2 text-xs font-mono text-gray-400">
          <Terminal size={14} />
          <span>mycellm-node.log</span>
          <span className="text-gray-600">({logs.length} entries)</span>
        </div>
        <button onClick={() => setAutoScroll(!autoScroll)}
          className={`text-xs font-mono px-2 py-0.5 rounded ${autoScroll ? 'text-spore bg-spore/10' : 'text-gray-500'}`}>
          {autoScroll ? 'auto-scroll on' : 'auto-scroll off'}
        </button>
      </div>
      <div className="p-4 font-mono text-xs leading-relaxed overflow-y-auto custom-scrollbar flex-grow bg-[#050505]">
        {logs.map((log, i) => (
          <div key={i} className="mb-0.5 flex hover:bg-white/[0.02]">
            <span className="text-gray-600 mr-3 w-16 shrink-0">{log.time}</span>
            <span className={`mr-2 w-6 shrink-0 ${log.level === 'ERROR' ? 'text-compute' : log.level === 'WARNING' ? 'text-ledger' : 'text-gray-600'}`}>
              {log.level === 'ERROR' ? 'ERR' : log.level === 'WARNING' ? 'WRN' : 'INF'}
            </span>
            <span className={`${tagColor(log.name)}`}>{log.message}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  )
}

// ── Auth Gate ──

function AuthGate({ onAuth }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)

  const submit = async () => {
    setChecking(true)
    setError('')
    setApiKey(key)
    try {
      await api('/v1/node/status')
      onAuth()
    } catch (e) {
      if (e.message === 'unauthorized') {
        setError('Invalid API key')
        setApiKey('')
      } else {
        setError(`Connection error: ${e.message}`)
      }
    }
    setChecking(false)
  }

  return (
    <div className="min-h-screen bg-void text-console font-mono flex items-center justify-center p-6">
      <div className="max-w-sm w-full border border-white/10 bg-[#111] p-6 rounded-xl">
        <div className="flex items-center space-x-3 mb-6">
          <Shield size={20} className="text-spore" />
          <h1 className="text-lg font-bold text-white">mycellm<span className="text-spore">.</span></h1>
        </div>
        <p className="text-sm text-gray-400 mb-4">This node requires an API key.</p>
        <input type="password" value={key} onChange={e => setKey(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          placeholder="MYCELLM_API_KEY"
          autoFocus
          className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none mb-3" />
        <button onClick={submit} disabled={checking || !key}
          className="w-full bg-spore text-black py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 transition-all">
          {checking ? 'Checking...' : 'Authenticate'}
        </button>
        {error && <p className="text-xs text-compute mt-3">{error}</p>}
      </div>
    </div>
  )
}

// ── Main App ──

export default function App() {
  const [appState, setAppState] = useState('checking') // checking → auth | booting → dashboard
  const [tab, _setTab] = useState(() => {
    const hash = window.location.hash.slice(1)
    return TABS.find(t => t.id === hash) ? hash : 'overview'
  })
  const setTab = (id) => { _setTab(id); window.location.hash = id }
  const [status, setStatus] = useState(null)
  const [credits, setCredits] = useState({ balance: 0, earned: 0, spent: 0 })
  const [logs, setLogs] = useState([])
  const [refreshTick, setRefreshTick] = useState(0)
  const [fleetCount, setFleetCount] = useState(0)
  const nodeRegistry = useManagedNodes()

  const triggerRefresh = useCallback(() => setRefreshTick(t => t + 1), [])

  // Check if auth is required on initial load
  useEffect(() => {
    if (appState !== 'checking') return
    fetch('/health').then(r => r.json()).then(d => {
      if (d.auth_required) {
        // Try stored key
        const stored = getApiKey()
        if (stored) {
          api('/v1/node/status').then(() => setAppState('booting')).catch(() => setAppState('auth'))
        } else {
          setAppState('auth')
        }
      } else {
        setAppState('booting')
      }
    }).catch(() => setAppState('booting'))  // node offline, skip auth
  }, [appState])

  // Poll status + credits
  useEffect(() => {
    if (appState !== 'dashboard') return
    const fetchData = async () => {
      try {
        const [s, c] = await Promise.all([
          api('/v1/node/status'),
          api('/v1/node/credits'),
        ])
        setStatus(s)
        setCredits(c)
      } catch { /* node offline */ }
    }
    fetchData()
    const iv = setInterval(fetchData, 3000)
    return () => clearInterval(iv)
  }, [appState, refreshTick])

  // Poll fleet count from registry
  useEffect(() => {
    if (appState !== 'dashboard') return
    const fetch_ = () => api('/v1/admin/nodes').then(d => setFleetCount((d.nodes || []).length)).catch(() => {})
    fetch_()
    const iv = setInterval(fetch_, 5000)
    return () => clearInterval(iv)
  }, [appState])

  // SSE log stream
  useEffect(() => {
    if (appState !== 'dashboard') return
    api('/v1/node/logs?limit=200').then(d => setLogs(d.logs || [])).catch(() => {})
    const es = new EventSource('/v1/node/logs/stream')
    es.onmessage = (event) => {
      try {
        const entry = JSON.parse(event.data)
        setLogs(prev => {
          const next = [...prev, entry]
          return next.length > 500 ? next.slice(-500) : next
        })
      } catch {}
    }
    return () => es.close()
  }, [appState])

  if (appState === 'checking') {
    return (
      <div className="min-h-screen bg-void flex items-center justify-center">
        <Loader2 size={24} className="animate-spin text-spore" />
      </div>
    )
  }

  if (appState === 'auth') {
    return <AuthGate onAuth={() => setAppState('booting')} />
  }

  if (appState === 'booting') {
    return <BootScreen onDone={() => setAppState('dashboard')} />
  }

  const nodeName = status?.node_name || 'mycellm-node'
  const peerId = status?.peer_id || ''

  return (
    <NodeRegistryContext.Provider value={nodeRegistry}>
      <div className="min-h-screen bg-void text-console font-sans">
        {/* Header */}
        <header className="border-b border-white/10 bg-void/80 backdrop-blur-md sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <pre className="text-[4px] leading-none text-compute drop-shadow-[0_0_5px_rgba(239,68,68,0.5)]">{ASCII_SHROOM}</pre>
              <span className="font-mono text-xl font-bold tracking-tighter text-white">
                mycellm<span className="text-spore">.</span>
              </span>
              <div className="h-4 w-px bg-white/20 mx-2" />
              <span className="font-mono text-xs bg-white/10 text-gray-300 px-2 py-1 rounded">{nodeName}</span>
              {peerId && <span className="font-mono text-xs text-gray-600 hidden md:inline">{peerId.slice(0, 12)}...</span>}
            </div>
            <div className="flex items-center space-x-5 font-mono text-sm">
              <div className="flex items-center space-x-1.5 text-relay">
                <Activity size={13} />
                <span>{status?.peers?.length || 0} peers</span>
              </div>
              <div className="flex items-center space-x-1.5 text-relay">
                <Radio size={13} />
                <span>{fleetCount} fleet</span>
              </div>
              <div className="flex items-center space-x-1.5 text-ledger drop-shadow-[0_0_8px_rgba(250,204,21,0.15)]">
                <Key size={13} />
                <span>{credits.balance?.toFixed(2)}</span>
              </div>
              <div className={`flex items-center space-x-1.5 ${status ? 'text-spore' : 'text-gray-600'}`}>
                <Shield size={13} />
                <span className="hidden sm:inline">{status ? 'Online' : 'Offline'}</span>
              </div>
            </div>
          </div>
        </header>

        {/* Tab nav */}
        <nav className="border-b border-white/5 bg-void/60">
          <div className="max-w-7xl mx-auto px-4 flex space-x-1 overflow-x-auto">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`flex items-center space-x-2 px-4 py-3 text-sm font-medium border-b-2 transition-all whitespace-nowrap ${
                  tab === t.id
                    ? 'border-spore text-white'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                }`}>
                <t.icon size={14} />
                <span>{t.label}</span>
              </button>
            ))}
          </div>
        </nav>

        {/* Content */}
        <main className="max-w-7xl mx-auto px-4 py-6">
          {tab === 'overview' && <OverviewTab status={status} credits={credits} />}
          {tab === 'network' && <NetworkTab status={status} />}
          {tab === 'models' && <ModelsTab status={status} onRefresh={triggerRefresh} />}
          {tab === 'chat' && <ChatTab />}
          {tab === 'credits' && <CreditsTab credits={credits} />}
          {tab === 'logs' && <LogsTab logs={logs} />}
        </main>
      </div>
    </NodeRegistryContext.Provider>
  )
}
