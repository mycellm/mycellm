import React, { useState, useEffect, useRef, useCallback, useMemo, createContext, useContext } from 'react'
import {
  Terminal, Activity, Server, Globe, Cpu, Database, Zap, Shield, Key,
  Send, Plus, Trash2, RefreshCw, MessageSquare, BarChart3, Network,
  Boxes, ChevronRight, Loader2, AlertCircle, Check, X, Eye, EyeOff,
  Radio, MonitorSmartphone, ChevronDown, ArrowUpDown, ArrowUp, ArrowDown,
  Wifi, WifiOff, Clock, TrendingUp, Heart, Gauge,
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

// ── Network Canvas (replaces SporeBackground) ──

function NetworkCanvas({ selfNode, peers, fleetNodes, activityEvents }) {
  const canvasRef = useRef(null)
  const nodesRef = useRef([])
  const particlesRef = useRef([])
  const frameRef = useRef(null)

  // Build node list from real data
  useEffect(() => {
    const nodes = []

    // Self node at center
    nodes.push({
      id: 'self',
      label: selfNode?.node_name || 'self',
      role: 'bootstrap',
      x: 0, y: 0,
      vx: 0, vy: 0,
      r: 6,
      color: '#22C55E', // spore
      models: selfNode?.models?.length || 0,
      fixed: true,
      pulse: 0,
    })

    // QUIC peers
    for (const p of (peers || [])) {
      const existing = nodesRef.current.find(n => n.id === p.peer_id)
      nodes.push({
        id: p.peer_id || `peer-${Math.random()}`,
        label: (p.peer_id || '').slice(0, 8),
        role: p.role || 'seeder',
        x: existing?.x || (Math.random() - 0.5) * 300,
        y: existing?.y || (Math.random() - 0.5) * 200,
        vx: 0, vy: 0,
        r: 4,
        color: p.status === 'routable' ? '#3B82F6' : '#666',
        models: p.models?.length || 0,
        connectedTo: 'self',
        pulse: 0,
      })
    }

    // Fleet nodes
    for (const f of (fleetNodes || [])) {
      if (f.status !== 'approved') continue
      const existing = nodesRef.current.find(n => n.id === f.peer_id)
      nodes.push({
        id: f.peer_id || f.node_name || `fleet-${Math.random()}`,
        label: f.node_name || (f.peer_id || '').slice(0, 8),
        role: f.capabilities?.role || 'seeder',
        x: existing?.x || (Math.random() - 0.5) * 400,
        y: existing?.y || (Math.random() - 0.5) * 300,
        vx: 0, vy: 0,
        r: 4,
        color: '#FACC15', // ledger (fleet = gold)
        models: (f.capabilities?.models || []).length,
        connectedTo: 'self',
        pulse: 0,
      })
    }

    nodesRef.current = nodes
  }, [selfNode, peers, fleetNodes])

  // Spawn particles on activity events
  useEffect(() => {
    if (!activityEvents || activityEvents.length === 0) return
    const latest = activityEvents[activityEvents.length - 1]
    if (!latest) return

    // Pulse self node on any activity
    const self = nodesRef.current.find(n => n.id === 'self')
    if (self) self.pulse = 1.0

    // Find target node and spawn particle
    const routedTo = latest.routed_to || latest.peer_id || latest.peer || ''
    if (routedTo) {
      const target = nodesRef.current.find(n => n.id === routedTo || n.label === routedTo.slice(0, 8))
      if (target) {
        target.pulse = 1.0
        if (self) {
          particlesRef.current.push({
            x: self.x, y: self.y,
            tx: target.x, ty: target.y,
            progress: 0,
            speed: 0.03,
            color: latest.type?.includes('credit') ? '#FACC15' : '#EF4444',
          })
        }
      }
    }
  }, [activityEvents?.length])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')

    const resize = () => {
      canvas.width = window.innerWidth
      canvas.height = window.innerHeight
    }
    resize()
    window.addEventListener('resize', resize)

    function draw() {
      const w = canvas.width
      const h = canvas.height
      const cx = w / 2
      const cy = h / 2
      ctx.clearRect(0, 0, w, h)

      const nodes = nodesRef.current
      if (nodes.length === 0) {
        frameRef.current = requestAnimationFrame(draw)
        return
      }

      // Simple force-directed layout
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].fixed) continue
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue
          const dx = nodes[i].x - nodes[j].x
          const dy = nodes[i].y - nodes[j].y
          const dist = Math.sqrt(dx * dx + dy * dy) || 1

          // Repulsion
          const repulse = 5000 / (dist * dist)
          nodes[i].vx += (dx / dist) * repulse * 0.01
          nodes[i].vy += (dy / dist) * repulse * 0.01
        }

        // Attraction to connected node
        if (nodes[i].connectedTo) {
          const target = nodes.find(n => n.id === nodes[i].connectedTo)
          if (target) {
            const dx = target.x - nodes[i].x
            const dy = target.y - nodes[i].y
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            const attract = (dist - 150) * 0.001
            nodes[i].vx += (dx / dist) * attract
            nodes[i].vy += (dy / dist) * attract
          }
        }

        // Damping + boundary
        nodes[i].vx *= 0.9
        nodes[i].vy *= 0.9
        nodes[i].x += nodes[i].vx
        nodes[i].y += nodes[i].vy
        nodes[i].x = Math.max(-w * 0.4, Math.min(w * 0.4, nodes[i].x))
        nodes[i].y = Math.max(-h * 0.35, Math.min(h * 0.35, nodes[i].y))
      }

      // Draw connections
      for (const node of nodes) {
        if (!node.connectedTo) continue
        const target = nodes.find(n => n.id === node.connectedTo)
        if (!target) continue

        const alpha = 0.08 + Math.max(node.pulse, target.pulse) * 0.15
        ctx.beginPath()
        ctx.moveTo(cx + node.x, cy + node.y)
        ctx.lineTo(cx + target.x, cy + target.y)
        ctx.strokeStyle = `rgba(34, 197, 94, ${alpha})`
        ctx.lineWidth = 0.8
        ctx.stroke()
      }

      // Draw particles (traveling along connections)
      const particles = particlesRef.current
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i]
        p.progress += p.speed
        if (p.progress >= 1) {
          particles.splice(i, 1)
          continue
        }
        const px = cx + p.x + (p.tx - p.x) * p.progress
        const py = cy + p.y + (p.ty - p.y) * p.progress
        ctx.beginPath()
        ctx.arc(px, py, 2, 0, Math.PI * 2)
        ctx.fillStyle = p.color
        ctx.globalAlpha = 1 - p.progress * 0.5
        ctx.fill()
        ctx.globalAlpha = 1
      }

      // Draw nodes
      for (const node of nodes) {
        const nx = cx + node.x
        const ny = cy + node.y

        // Pulse decay
        if (node.pulse > 0) node.pulse *= 0.95

        // Glow on pulse
        if (node.pulse > 0.1) {
          ctx.beginPath()
          ctx.arc(nx, ny, node.r + 8 * node.pulse, 0, Math.PI * 2)
          ctx.fillStyle = `rgba(34, 197, 94, ${node.pulse * 0.2})`
          ctx.fill()
        }

        // Node circle
        ctx.beginPath()
        ctx.arc(nx, ny, node.r, 0, Math.PI * 2)
        ctx.fillStyle = node.color
        ctx.globalAlpha = 0.15 + node.pulse * 0.5
        ctx.fill()
        ctx.globalAlpha = 1

        // Border
        ctx.beginPath()
        ctx.arc(nx, ny, node.r, 0, Math.PI * 2)
        ctx.strokeStyle = node.color
        ctx.lineWidth = 1
        ctx.globalAlpha = 0.3 + node.pulse * 0.5
        ctx.stroke()
        ctx.globalAlpha = 1

        // Label (only if enough nodes to be useful)
        if (nodes.length > 1) {
          ctx.font = '9px JetBrains Mono, monospace'
          ctx.fillStyle = `rgba(229, 229, 229, ${0.15 + node.pulse * 0.3})`
          ctx.textAlign = 'center'
          ctx.fillText(node.label, nx, ny + node.r + 12)
        }
      }

      // Ambient spores (decorative, fewer than before)
      if (!draw._ambientSpores) {
        draw._ambientSpores = Array.from({ length: 15 }, () => ({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.15,
          vy: (Math.random() - 0.5) * 0.15,
          r: Math.random() * 1.2 + 0.3,
          phase: Math.random() * Math.PI * 2,
        }))
      }
      const now = Date.now() * 0.001
      for (const s of draw._ambientSpores) {
        s.x += s.vx
        s.y += s.vy
        if (s.x < 0) s.x = w
        if (s.x > w) s.x = 0
        if (s.y < 0) s.y = h
        if (s.y > h) s.y = 0
        const pulse = Math.sin(now + s.phase) * 0.3 + 0.7
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(34, 197, 94, ${0.04 * pulse})`
        ctx.fill()
      }

      frameRef.current = requestAnimationFrame(draw)
    }

    frameRef.current = requestAnimationFrame(draw)
    return () => {
      window.removeEventListener('resize', resize)
      if (frameRef.current) cancelAnimationFrame(frameRef.current)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-0"
    />
  )
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

// ── Helpers ──

function formatUptime(seconds) {
  if (!seconds || seconds <= 0) return '0s'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function connectionStateColor(state) {
  switch (state) {
    case 'routable': return 'text-spore'
    case 'connecting': case 'handshaking': return 'text-ledger'
    case 'disconnected': return 'text-compute'
    default: return 'text-gray-500'
  }
}

function connectionStateDot(state) {
  switch (state) {
    case 'routable': return 'bg-spore'
    case 'connecting': case 'handshaking': return 'bg-ledger animate-pulse'
    case 'disconnected': return 'bg-compute'
    default: return 'bg-gray-600'
  }
}

// ── Network Health Bar ──

function NetworkHealthBar({ connections, peers, fleetNodes }) {
  const routableConns = connections.filter(c => c.state === 'routable').length
  const totalConns = connections.length
  const approvedFleet = fleetNodes.filter(n => n.status === 'approved').length
  const totalFleet = fleetNodes.length
  const totalPeers = peers.length

  // Adaptive health: use whatever connectivity data is available
  const hasConnections = totalConns > 0
  const hasFleet = totalFleet > 0
  const hasPeers = totalPeers > 0

  let score = 0
  let weights = 0

  if (hasConnections) {
    score += (routableConns / totalConns) * 50
    weights += 50
  }
  if (hasFleet) {
    score += (approvedFleet / Math.max(totalFleet, 1)) * 40
    weights += 40
  }
  if (hasPeers) {
    score += 30 // peers exist = good
    weights += 30
  }

  // If nothing is connected, base score on whether we have local models
  if (weights === 0) {
    score = 20 // node is alive but isolated
    weights = 100
  } else {
    score = Math.round((score / weights) * 100)
  }

  const barColor = score >= 70 ? 'bg-spore' : score >= 40 ? 'bg-ledger' : 'bg-compute'
  const label = score >= 70 ? 'Healthy' : score >= 40 ? 'Degraded' : score > 0 ? 'Limited' : 'Offline'
  const labelColor = score >= 70 ? 'text-spore' : score >= 40 ? 'text-ledger' : 'text-compute'

  // Build status summary
  const parts = []
  if (hasConnections) parts.push(`${routableConns}/${totalConns} QUIC`)
  if (hasFleet) parts.push(`${approvedFleet}/${totalFleet} fleet`)
  if (hasPeers) parts.push(`${totalPeers} peers`)
  if (parts.length === 0) parts.push('No connections')

  return (
    <div className="border border-white/10 bg-[#111] rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest flex items-center space-x-2">
          <Heart size={12} />
          <span>Network Health</span>
        </h2>
        <div className="flex items-center space-x-2">
          <span className={`font-mono text-2xl font-bold ${labelColor}`}>{score}</span>
          <span className={`text-xs ${labelColor}`}>{label}</span>
        </div>
      </div>
      <div className="w-full bg-void rounded-full h-2 overflow-hidden border border-white/5">
        <div className={`h-full ${barColor} transition-all duration-500`} style={{ width: `${score}%` }} />
      </div>
      <div className="flex justify-between mt-3 text-xs text-gray-500">
        {parts.map((p, i) => <span key={i}>{p}</span>)}
      </div>
    </div>
  )
}

// ── Sparkline & Activity Feed ──

function Sparkline({ data, color = '#22C55E', height = 32, width = 120 }) {
  if (!data || data.length === 0) return <div style={{ width, height }} />
  const max = Math.max(...data, 1)
  const points = data.map((v, i) => {
    const x = (i / Math.max(data.length - 1, 1)) * width
    const y = height - (v / max) * (height - 4) - 2
    return `${x},${y}`
  }).join(' ')

  return (
    <svg width={width} height={height} className="opacity-80">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function ActivityFeed({ events }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const typeColors = {
    inference_complete: 'text-compute',
    inference_start: 'text-compute/50',
    inference_failed: 'text-red-400',
    route_decision: 'text-relay',
    peer_connected: 'text-spore',
    peer_disconnected: 'text-gray-500',
    model_loaded: 'text-spore',
    model_unloaded: 'text-gray-500',
    credit_earned: 'text-ledger',
    credit_spent: 'text-ledger/70',
    announce_ok: 'text-spore/50',
    announce_failed: 'text-compute/50',
    fleet_node_joined: 'text-relay',
  }

  const typeIcons = {
    inference_complete: '\u26A1',
    inference_start: '\u2192',
    inference_failed: '\u2717',
    route_decision: '\u2197',
    peer_connected: '\u25CF',
    peer_disconnected: '\u25CB',
    model_loaded: '+',
    model_unloaded: '\u2212',
    credit_earned: '\u2191',
    credit_spent: '\u2193',
    announce_ok: '\uD83D\uDCE1',
    announce_failed: '\u26A0',
    fleet_node_joined: '\uD83C\uDF10',
  }

  function eventLabel(e) {
    switch (e.type) {
      case 'inference_complete':
        return `${e.model || '?'} \u2192 ${e.source || '?'} (${e.tokens || 0} tok, ${e.latency_ms ? e.latency_ms + 'ms' : '?'})`
      case 'inference_failed':
        return `${e.model || '?'} failed: ${e.error || 'unknown'}`
      case 'route_decision':
        return `Routed ${e.model || '?'} \u2192 ${(e.routed_to || '').slice(0, 12) || e.target || '?'}`
      case 'peer_connected':
        return `Peer ${(e.peer_id || '').slice(0, 12)}... (${e.role || 'unknown'})`
      case 'peer_disconnected':
        return `Peer ${(e.peer_id || '').slice(0, 12)}... disconnected`
      case 'model_loaded':
        return `Loaded ${e.model || '?'} (${e.backend || '?'})`
      case 'model_unloaded':
        return `Unloaded ${e.model || '?'}`
      case 'credit_earned':
        return `+${(e.amount || 0).toFixed(4)} from ${(e.peer || '').slice(0, 12) || 'local'}`
      case 'credit_spent':
        return `-${(e.amount || 0).toFixed(4)} to ${(e.peer || '').slice(0, 12) || 'network'}`
      case 'announce_ok':
        return `Announced to ${e.target || 'bootstrap'}`
      case 'announce_failed':
        return `Announce failed: ${e.target || 'bootstrap'}`
      case 'fleet_node_joined':
        return `${e.node_name || 'node'} joined fleet`
      default:
        return e.type.replace(/_/g, ' ')
    }
  }

  return (
    <div className="space-y-1 max-h-[250px] overflow-y-auto custom-scrollbar text-xs font-mono">
      {events.length === 0 && (
        <div className="text-gray-600 text-center py-4">No activity yet. Send an inference request to see events.</div>
      )}
      {events.map((e, i) => (
        <div key={i} className="flex items-center space-x-2 py-1 hover:bg-white/[0.02] rounded px-1">
          <span className="text-gray-600 w-14 shrink-0">{e.time || ''}</span>
          <span className={`w-4 shrink-0 ${typeColors[e.type] || 'text-gray-500'}`}>
            {typeIcons[e.type] || '\u00B7'}
          </span>
          <span className={typeColors[e.type] || 'text-gray-400'}>
            {eventLabel(e)}
          </span>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

// ── Network Topology (structured node view) ──

function NetworkTopology({ status, fleetNodes, onNodeClick }) {
  const peers = status?.peers || []
  const allNodes = [
    { id: 'self', name: status?.node_name || 'This node', role: 'bootstrap', status: 'online', models: status?.models || [], type: 'self' },
    ...peers.map(p => ({
      id: p.peer_id, name: (p.peer_id || '').slice(0, 12) + '...', role: p.role,
      status: p.status, models: p.models || [], type: 'quic',
    })),
    ...(fleetNodes || []).filter(f => f.status === 'approved').map(f => ({
      id: f.peer_id || f.node_name, name: f.node_name || (f.peer_id || '').slice(0, 12),
      role: f.capabilities?.role || 'seeder', status: 'fleet',
      models: (f.capabilities?.models || []).map(m => m.name || m),
      type: 'fleet', system: f.system,
    })),
  ]

  const roleIcons = { bootstrap: '\u{1F451}', seeder: '\u{1F5A5}\uFE0F', consumer: '\u{1F4BB}', relay: '\u{1F500}' }
  const statusColors = {
    online: 'border-spore', routable: 'border-spore', fleet: 'border-ledger',
    disconnected: 'border-compute', discovered: 'border-gray-600',
  }
  const statusDots = {
    online: 'bg-spore', routable: 'bg-spore', fleet: 'bg-ledger',
    disconnected: 'bg-compute', discovered: 'bg-gray-600',
  }

  if (allNodes.length <= 1) return null

  return (
    <div className="border border-white/10 bg-[#111] rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4 flex items-center space-x-2">
        <Network size={12} />
        <span>Topology ({allNodes.length} nodes)</span>
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {allNodes.map((node) => (
          <button key={node.id}
            onClick={() => onNodeClick?.(node)}
            className={`text-left bg-black border ${statusColors[node.status] || 'border-white/10'} rounded-lg p-3 hover:bg-white/[0.03] transition-colors`}>
            <div className="flex items-center space-x-2 mb-2">
              <span className="text-sm">{roleIcons[node.role] || '\u{1F5A5}\uFE0F'}</span>
              <span className="font-mono text-xs text-white truncate">{node.name}</span>
              <div className={`w-2 h-2 rounded-full ml-auto ${statusDots[node.status] || 'bg-gray-600'}`} />
            </div>
            <div className="text-xs text-gray-500">
              {node.models.length > 0 ? (
                <span>{node.models.length} model{node.models.length !== 1 ? 's' : ''}</span>
              ) : (
                <span className="text-gray-600">no models</span>
              )}
              <span className="mx-1">&middot;</span>
              <span className={node.type === 'self' ? 'text-spore' : node.type === 'fleet' ? 'text-ledger' : 'text-relay'}>
                {node.type}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Node Detail Panel (click-to-inspect) ──

function NodeDetailPanel({ node, onClose }) {
  if (!node) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="bg-[#111] border border-white/10 rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-3">
            <span className="text-xl">{node.role === 'bootstrap' ? '\u{1F451}' : '\u{1F5A5}\uFE0F'}</span>
            <div>
              <h3 className="text-white font-mono font-bold">{node.name}</h3>
              <p className="text-xs text-gray-500">{node.id === 'self' ? 'This node' : node.id}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">Role</div>
              <div className="text-white mt-1">{node.role}</div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">Status</div>
              <div className={`mt-1 ${node.status === 'routable' || node.status === 'online' ? 'text-spore' : node.status === 'fleet' ? 'text-ledger' : 'text-compute'}`}>
                {node.status}
              </div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">Transport</div>
              <div className="text-white mt-1">{node.type}</div>
            </div>
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500">Models</div>
              <div className="text-white mt-1">{node.models?.length || 0}</div>
            </div>
          </div>

          {node.models && node.models.length > 0 && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">Available Models</div>
              <div className="flex flex-wrap gap-1">
                {node.models.map((m, i) => (
                  <span key={i} className="bg-white/5 text-xs text-gray-300 px-2 py-0.5 rounded font-mono">
                    {typeof m === 'string' ? m : m.name || m}
                  </span>
                ))}
              </div>
            </div>
          )}

          {node.system && (
            <div className="bg-black rounded-lg p-3">
              <div className="text-xs text-gray-500 mb-2">Hardware</div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-400">
                {node.system.cpu?.name && <div>CPU: {node.system.cpu.name}</div>}
                {node.system.memory?.total_gb && <div>RAM: {node.system.memory.total_gb}GB</div>}
                {node.system.gpu?.gpu && node.system.gpu.gpu !== 'CPU' && <div>GPU: {node.system.gpu.gpu}</div>}
                {node.system.os?.hostname && <div>Host: {node.system.os.hostname}</div>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Overview Tab ──

function OverviewTab({ status, credits, fleetNodes }) {
  const [sysInfo, setSysInfo] = useState(null)
  const [connections, setConnections] = useState([])
  const [activity, setActivity] = useState({ events: [], stats: {}, sparklines: {} })
  const [liveEvents, setLiveEvents] = useState([])
  const [federation, setFederation] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const peers = status?.peers || []
  const models = status?.models || []
  const uptime = status?.uptime_seconds || 0

  useEffect(() => { api('/v1/node/system').then(setSysInfo).catch(() => {}) }, [])
  useEffect(() => {
    const f = () => api('/v1/node/connections').then(d => setConnections(d.connections || [])).catch(() => {})
    f(); const iv = setInterval(f, 5000); return () => clearInterval(iv)
  }, [])
  useEffect(() => {
    const f = () => api('/v1/node/activity?limit=100').then(setActivity).catch(() => {})
    f(); const iv = setInterval(f, 3000); return () => clearInterval(iv)
  }, [])
  useEffect(() => { api('/v1/node/federation').then(setFederation).catch(() => {}) }, [])
  useEffect(() => {
    const es = new EventSource('/v1/node/activity/stream')
    es.onmessage = (e) => { try { setLiveEvents(p => [...p.slice(-199), JSON.parse(e.data)]) } catch {} }
    return () => es.close()
  }, [])

  const allEvents = [...(activity.events || []), ...liveEvents]
  const stats = activity.stats || {}
  const sparklines = activity.sparklines || {}
  const approvedFleet = (fleetNodes || []).filter(n => n.status === 'approved')

  // Build node list for fleet grid
  const fleetGrid = [
    {
      id: 'self', name: status?.node_name || 'This node', role: status?.role || 'bootstrap',
      status: 'online', models: models, type: 'self',
      system: sysInfo, modelCount: models.length,
    },
    ...peers.map(p => ({
      id: p.peer_id, name: (p.peer_id || '').slice(0, 12) + '...', role: p.role,
      status: p.status, models: p.models || [], type: 'quic', modelCount: (p.models || []).length,
    })),
    ...approvedFleet.map(f => ({
      id: f.peer_id || f.node_name, name: f.node_name || (f.peer_id || '').slice(0, 12),
      role: f.capabilities?.role || 'seeder', status: 'fleet', type: 'fleet',
      models: (f.capabilities?.models || []).map(m => m.name || m),
      modelCount: (f.capabilities?.models || []).length, system: f.system,
    })),
  ]

  const roleIcons = { bootstrap: '\u{1F451}', seeder: '\u{1F5A5}\uFE0F', consumer: '\u{1F4BB}', relay: '\u{1F500}' }
  const statusDots = { online: 'bg-spore', routable: 'bg-spore', fleet: 'bg-ledger', disconnected: 'bg-compute' }

  return (
    <div className="space-y-5">
      {/* Network Identity */}
      <div className="flex items-center justify-between bg-[#111] border border-white/10 rounded-xl p-4">
        <div className="flex items-center space-x-4">
          <div className="w-10 h-10 rounded-lg bg-spore/10 border border-spore/20 flex items-center justify-center text-spore text-lg">
            {roleIcons[status?.role] || '\u{1F5A5}\uFE0F'}
          </div>
          <div>
            <h2 className="text-white font-mono font-bold text-sm">{status?.node_name || 'mycellm-node'}</h2>
            <div className="flex items-center space-x-3 text-xs text-gray-500 mt-0.5">
              <span>{federation?.network_name || 'Standalone'}</span>
              {federation?.network_id && <span className="font-mono">{federation.network_id.slice(0, 8)}...</span>}
              <span>&middot;</span>
              <span>{formatUptime(uptime)} uptime</span>
            </div>
          </div>
        </div>
        <div className="flex items-center space-x-4 text-xs">
          <div className="text-center">
            <div className="text-xl font-mono text-white">{fleetGrid.length}</div>
            <div className="text-gray-500">nodes</div>
          </div>
          <div className="text-center">
            <div className="text-xl font-mono text-spore">{models.length}</div>
            <div className="text-gray-500">models</div>
          </div>
          <div className="text-center">
            <div className="text-xl font-mono text-ledger">{credits.balance?.toFixed(1)}</div>
            <div className="text-gray-500">credits</div>
          </div>
        </div>
      </div>

      {/* Stats Row with Sparklines */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="border border-white/10 bg-[#111] rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-gray-500 font-mono">REQUESTS</div>
              <div className="text-2xl font-mono text-compute mt-1">{stats.total_requests || 0}</div>
              <div className="text-xs text-gray-600">{stats.requests_per_min || 0}/min</div>
            </div>
            <Sparkline data={sparklines.requests || []} color="#EF4444" height={32} width={70} />
          </div>
        </div>
        <div className="border border-white/10 bg-[#111] rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-gray-500 font-mono">TOKENS</div>
              <div className="text-2xl font-mono text-poison mt-1">{stats.total_tokens || 0}</div>
              <div className="text-xs text-gray-600">{stats.tokens_per_min || 0}/min</div>
            </div>
            <Sparkline data={sparklines.tokens || []} color="#A855F7" height={32} width={70} />
          </div>
        </div>
        <div className="border border-white/10 bg-[#111] rounded-xl p-4">
          <div className="text-xs text-gray-500 font-mono">CREDITS</div>
          <div className="text-2xl font-mono text-ledger mt-1">{credits.balance?.toFixed(1)}</div>
          <div className="text-xs text-gray-600">+{credits.earned?.toFixed(1) || '0'} / -{credits.spent?.toFixed(1) || '0'}</div>
        </div>
        <div className="border border-white/10 bg-[#111] rounded-xl p-4">
          <div className="text-xs text-gray-500 font-mono">INFERENCE</div>
          <div className={`text-2xl font-mono mt-1 ${(status?.inference?.active || 0) > 0 ? 'text-compute animate-pulse' : 'text-gray-400'}`}>
            {status?.inference?.active || 0}/{status?.inference?.max_concurrent || 2}
          </div>
          <div className="text-xs text-gray-600">{(status?.inference?.active || 0) > 0 ? 'processing' : 'idle'}</div>
        </div>
      </div>

      {/* Network Health */}
      <NetworkHealthBar connections={connections} peers={peers} fleetNodes={fleetNodes || []} />

      {/* Fleet Grid */}
      {fleetGrid.length > 1 && (
        <div className="border border-white/10 bg-[#111] rounded-xl p-5">
          <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-3">Fleet ({fleetGrid.length} nodes)</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {fleetGrid.map(node => (
              <button key={node.id} onClick={() => setSelectedNode(node)}
                className={`text-left bg-black border rounded-lg p-3 hover:bg-white/[0.03] transition-colors ${
                  node.type === 'self' ? 'border-spore/30' : node.status === 'fleet' ? 'border-ledger/20' : 'border-white/10'
                }`}>
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center space-x-2">
                    <span className="text-sm">{roleIcons[node.role] || '\u{1F5A5}\uFE0F'}</span>
                    <span className="font-mono text-xs text-white truncate max-w-[100px]">{node.name}</span>
                  </div>
                  <div className={`w-2 h-2 rounded-full ${statusDots[node.status] || 'bg-gray-600'}`} />
                </div>
                <div className="text-xs text-gray-500">
                  {node.modelCount} model{node.modelCount !== 1 ? 's' : ''}
                  <span className="mx-1">&middot;</span>
                  <span className={node.type === 'self' ? 'text-spore' : node.type === 'fleet' ? 'text-ledger' : 'text-relay'}>{node.type}</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Activity Feed */}
      <div className="border border-white/10 bg-[#111] rounded-xl p-5">
        <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-3 flex items-center space-x-2">
          <Activity size={12} />
          <span>Activity</span>
          {stats.requests_per_min > 0 && <span className="text-compute animate-pulse">&bull; {stats.requests_per_min}/min</span>}
        </h2>
        <ActivityFeed events={allEvents.slice(-80)} />
      </div>

      {selectedNode && <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />}
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
  const [selected, _setSelected] = useState('local') // 'local' or api_addr
  const setSelected = (v) => {
    _setSelected(v)
    // Reset search state when switching nodes
    setSearchResults([])
    setRepoFiles(null)
    setHasSearched(false)
    setSuggestions(null)
  }
  const [sortBy, setSortBy] = useState('name')
  const [sortDir, setSortDir] = useState('asc')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [remoteStatus, setRemoteStatus] = useState(null)
  const [addMode, setAddMode] = useState('browse') // 'browse' | 'local' | 'api'
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [filterCompatible, setFilterCompatible] = useState(true)
  const [suggestions, setSuggestions] = useState(null)
  const [nodeResources, setNodeResources] = useState({ ram_gb: 0, disk_free_gb: 0 })
  const [localFiles, setLocalFiles] = useState([])  // GGUF files on disk
  const [savedConfigs, setSavedConfigs] = useState([])  // persisted API model configs
  const [loadStatuses, setLoadStatuses] = useState([])  // in-progress loads
  const [repoFiles, setRepoFiles] = useState(null) // { repo_id, files }
  const [downloadStatus, setDownloadStatus] = useState({})

  const [form, setForm] = useState({
    name: '', model_path: '',
    api_base: 'https://openrouter.ai/api/v1', api_key: '', api_model: '', ctx_len: 4096,
  })
  const [showKey, setShowKey] = useState(false)

  // Build device list: local node + approved fleet nodes
  useEffect(() => {
    const fetchDevices = async () => {
      const localHw = status?.hardware || {}
      const selfAddr = window.location.hostname + ':' + (window.location.port || '8420')
      const localDevice = {
        id: 'local', name: status?.node_name || 'this node', addr: selfAddr,
        gpu: localHw.gpu || 'CPU', backend: localHw.backend || 'cpu',
        ram: localHw.vram_gb || 0, models: status?.models || [],
        online: true, role: status?.role || 'bootstrap', isSelf: true,
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
  const isRemote = selected !== 'local' && !selectedDevice?.isSelf

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
    if (a.isSelf) return -1
    if (b.isSelf) return 1
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

  const handleUnload = async (modelName) => {
    try {
      await doApi('/v1/node/models/unload', { model: modelName })
      onRefresh()
    }
    catch (e) { setResult({ error: e.message }) }
  }

  // Search HuggingFace
  // Load suggestions + node resources + local files on mount
  useEffect(() => {
    const target = isRemote && selectedDevice?.addr ? selectedDevice.addr : ''
    const doFetch = (path) => target ? remoteApi(target, path) : api(path)
    doFetch('/v1/node/models/suggested').then(d => {
      setSuggestions(d.suggestions || [])
      setNodeResources({ ram_gb: d.node_ram_gb || 0, disk_free_gb: d.node_disk_free_gb || 0 })
    }).catch(() => {})
    doFetch('/v1/node/models/local').then(d => setLocalFiles(d.files || [])).catch(() => {})
    doFetch('/v1/node/models/saved').then(d => setSavedConfigs(d.configs || [])).catch(() => {})
  }, [selected, isRemote, selectedDevice?.addr])

  // Poll load status for in-progress loads
  useEffect(() => {
    const poll = () => nodeApi('/v1/node/models/load-status').then(d => {
      setLoadStatuses(d.statuses || [])
      // If any loads just completed, refresh models
      const justFinished = (d.statuses || []).some(s => s.status === 'ready' || s.status === 'failed')
      if (justFinished) {
        onRefresh()
        nodeApi('/v1/node/models/local').then(d2 => setLocalFiles(d2.files || [])).catch(() => {})
        nodeApi('/v1/node/models/saved').then(d2 => setSavedConfigs(d2.configs || [])).catch(() => {})
      }
    }).catch(() => {})
    poll()
    const iv = setInterval(poll, 2000)
    return () => clearInterval(iv)
  }, [selected, isRemote, selectedDevice?.addr])

  // Helper: fetch from selected node
  const nodeApi = (path) => isRemote && selectedDevice?.addr ? remoteApi(selectedDevice.addr, path) : api(path)

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    setHasSearched(true)
    try {
      const data = await nodeApi(`/v1/node/models/search?q=${encodeURIComponent(searchQuery)}&limit=12`)
      setSearchResults(data.models || [])
      if (data.node_ram_gb) setNodeResources({ ram_gb: data.node_ram_gb, disk_free_gb: data.node_disk_free_gb || 0 })
    } catch (e) {
      setSearchResults([])
    }
    setSearching(false)
  }

  // Browse repo files
  const handleBrowseRepo = async (repoId) => {
    try {
      const data = await nodeApi(`/v1/node/models/search/${repoId}/files`)
      setRepoFiles(data)
    } catch {}
  }

  // Download a GGUF file
  const handleDownload = async (repoId, filename, fileMeta) => {
    try {
      const data = await doApi('/v1/node/models/download', {
        repo_id: repoId, filename,
        quant: fileMeta?.quant || '',
        param_b: repoFiles?.param_b || 0,
        context_length: repoFiles?.context_length || 4096,
        size_gb: fileMeta?.size_gb || 0,
      })
      if (data.download_id) {
        setDownloadStatus(prev => ({ ...prev, [data.download_id]: data }))
        const fetchDl = () => isRemote && selectedDevice?.addr
          ? remoteApi(selectedDevice.addr, '/v1/node/models/downloads')
          : api('/v1/node/models/downloads')
        const pollId = setInterval(async () => {
          try {
            const st = await fetchDl()
            const dl = (st.downloads || []).find(d => d.download_id === data.download_id)
            if (dl) {
              setDownloadStatus(prev => ({ ...prev, [data.download_id]: dl }))
              if (dl.status === 'complete' || dl.status === 'failed') {
                clearInterval(pollId)
                onRefresh()
                // Refresh local files list
                const doFetch = isRemote && selectedDevice?.addr ? (p) => remoteApi(selectedDevice.addr, p) : api
                doFetch('/v1/node/models/local').then(d => setLocalFiles(d.files || [])).catch(() => {})
              }
            }
          } catch {}
        }, 2000)
      }
    } catch {}
  }

  // Load local GGUF file
  const handleLoadLocal = async () => {
    if (!form.model_path) return
    setLoading(true)
    try {
      const data = await doApi('/v1/node/models/load', {
        model_path: form.model_path,
        name: form.name || undefined,
        backend: 'llama.cpp',
        ctx_len: form.ctx_len || 4096,
      })
      setResult(data)
      if (!data.error) onRefresh()
    } catch (e) { setResult({ error: e.message }) }
    setLoading(false)
  }

  // Connect remote API
  const handleLoadApi = async () => {
    if (!form.name || !form.api_base) return
    setLoading(true)
    try {
      const data = await doApi('/v1/node/models/load', {
        name: form.name,
        backend: 'openai',
        api_base: form.api_base,
        api_key: form.api_key,
        api_model: form.api_model || form.name,
        ctx_len: form.ctx_len || 4096,
      })
      setResult(data)
      if (!data.error) onRefresh()
    } catch (e) { setResult({ error: e.message }) }
    setLoading(false)
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

      {/* Models — single unified reactive table */}
      {(() => {
        // Merge all sources into one list, one entry per model name
        const merged = new Map() // name -> unified entry

        // 1. Load statuses (highest priority for loading/failed)
        for (const s of loadStatuses) {
          if (s.status === 'loading' || s.status === 'failed') {
            merged.set(s.model, {
              name: s.model, state: s.status, backend: s.backend || 'llama.cpp',
              phase: s.phase, error: s.error, elapsed: s.elapsed,
              quant: '', size: '', ctx: 0,
            })
          }
        }

        // 2. Active loaded models
        for (const m of models) {
          const existing = merged.get(m.name)
          if (existing?.state === 'loading') continue // loading takes precedence
          const onDisk = localFiles.find(f => f.model_name === m.name)
          merged.set(m.name, {
            name: m.name, state: 'active', backend: m.backend || 'llama.cpp',
            quant: m.quant || '', ctx: m.ctx_len || 4096,
            size: onDisk ? `${onDisk.size_gb}GB` : m.param_count_b ? `~${(m.param_count_b * 0.5).toFixed(1)}GB` : '',
            hasFile: !!onDisk, filename: onDisk?.filename, filePath: onDisk?.path,
          })
        }

        // 3. On-disk GGUF files (not already in merged)
        for (const f of localFiles) {
          if (!merged.has(f.model_name)) {
            merged.set(f.model_name, {
              name: f.model_name, state: 'on-disk', backend: 'llama.cpp',
              quant: f.quant || '', ctx: f.ctx_len || 0,
              size: `${f.size_gb}GB`, hasFile: true, filename: f.filename, filePath: f.path,
            })
          }
        }

        // 4. Saved API configs (not already in merged)
        for (const c of savedConfigs) {
          if (!merged.has(c.name) && c.backend !== 'llama.cpp') {
            merged.set(c.name, {
              name: c.name, state: 'disabled', backend: c.backend,
              quant: '', ctx: c.ctx_len || 4096, size: '',
            })
          }
        }

        const allModels = [...merged.values()]

        // Sort: loading first, then active, then failed, then on-disk, then disabled
        const stateOrder = { loading: 0, active: 1, failed: 2, 'on-disk': 3, disabled: 4 }
        allModels.sort((a, b) => (stateOrder[a.state] ?? 9) - (stateOrder[b.state] ?? 9))

        // Shape indicators by state
        const stateIndicator = {
          active:   <svg width="10" height="10" className="inline-block"><circle cx="5" cy="5" r="4" fill="#22C55E" /></svg>,
          loading:  <svg width="10" height="10" className="inline-block animate-pulse"><polygon points="5,1 9,9 1,9" fill="#FACC15" /></svg>,
          'on-disk': <svg width="10" height="10" className="inline-block"><rect x="1" y="1" width="8" height="8" fill="#666" rx="1" /></svg>,
          disabled: <svg width="10" height="10" className="inline-block"><polygon points="5,0 10,5 5,10 0,5" fill="none" stroke="#666" strokeWidth="1.5" /></svg>,
          failed:   <svg width="10" height="10" className="inline-block"><line x1="2" y1="2" x2="8" y2="8" stroke="#EF4444" strokeWidth="2" /><line x1="8" y1="2" x2="2" y2="8" stroke="#EF4444" strokeWidth="2" /></svg>,
        }

        const stateBadge = {
          active:   <span className="text-xs px-1.5 py-0.5 rounded bg-spore/10 text-spore">active</span>,
          loading:  <span className="text-xs px-1.5 py-0.5 rounded bg-ledger/10 text-ledger animate-pulse">loading</span>,
          'on-disk': <span className="text-xs px-1.5 py-0.5 rounded bg-white/5 text-gray-500">on disk</span>,
          disabled: <span className="text-xs px-1.5 py-0.5 rounded bg-white/5 text-gray-500">disabled</span>,
          failed:   <span className="text-xs px-1.5 py-0.5 rounded bg-compute/10 text-compute">failed</span>,
        }

        const stateNameColor = {
          active: 'text-white', loading: 'text-ledger', 'on-disk': 'text-gray-400',
          disabled: 'text-gray-400', failed: 'text-compute',
        }

        const activeCount = allModels.filter(m => m.state === 'active').length
        const loadingCount = allModels.filter(m => m.state === 'loading').length

        // Refresh helper
        const refreshAll = () => {
          onRefresh()
          nodeApi('/v1/node/models/local').then(d => setLocalFiles(d.files || [])).catch(() => {})
          nodeApi('/v1/node/models/saved').then(d => setSavedConfigs(d.configs || [])).catch(() => {})
        }

        return (
          <div className="border border-white/10 bg-[#111] rounded-xl overflow-hidden">
            <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
              <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest flex items-center space-x-3">
                <span>Models</span>
                {activeCount > 0 && <span className="text-spore">● {activeCount} active</span>}
                {loadingCount > 0 && <span className="text-ledger animate-pulse">▲ {loadingCount} loading</span>}
              </h2>
            </div>
            {allModels.length > 0 ? (
              <table className="w-full text-sm">
                <thead className="bg-black/30">
                  <tr className="text-xs text-gray-500 font-mono uppercase">
                    <th className="text-left py-2 px-4 w-7"></th>
                    <th className="text-left py-2 px-4">Name</th>
                    <th className="text-left py-2 px-4 hidden md:table-cell">Backend</th>
                    <th className="text-left py-2 px-4 hidden md:table-cell">Quant</th>
                    <th className="text-left py-2 px-4 hidden md:table-cell">Size</th>
                    <th className="text-left py-2 px-4 hidden lg:table-cell">Status</th>
                    <th className="text-right py-2 px-4">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {allModels.map((m) => (
                    <tr key={m.name}
                      className={`border-t border-white/5 transition-all duration-300 ${
                        m.state === 'loading' ? 'bg-ledger/[0.03]' :
                        m.state === 'failed' ? 'bg-compute/[0.03]' :
                        m.state === 'active' ? 'hover:bg-white/[0.02]' :
                        'hover:bg-white/[0.02] opacity-70'
                      }`}>
                      <td className="py-2.5 px-4" title={m.state}>{stateIndicator[m.state]}</td>
                      <td className={`py-2.5 px-4 font-mono ${stateNameColor[m.state]}`}>
                        {m.name}
                        {m.state === 'loading' && m.phase && (
                          <div className="text-xs text-ledger/70 font-sans mt-0.5">{m.phase}{m.elapsed ? ` · ${m.elapsed}s` : ''}</div>
                        )}
                        {m.state === 'failed' && m.error && (
                          <div className="text-xs text-compute/70 font-sans mt-0.5 truncate max-w-[250px]" title={m.error}>{m.error}</div>
                        )}
                      </td>
                      <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell">{m.backend}</td>
                      <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell font-mono">{m.quant || '-'}</td>
                      <td className="py-2.5 px-4 text-gray-500 hidden md:table-cell">{m.size || '-'}</td>
                      <td className="py-2.5 px-4 hidden lg:table-cell">{stateBadge[m.state]}</td>
                      <td className="py-2.5 px-4 text-right space-x-2 whitespace-nowrap">
                        {m.state === 'active' && (
                          <>
                            <button onClick={async () => { await doApi('/v1/node/models/unload', { model: m.name }); refreshAll() }}
                              className="text-xs text-gray-500 hover:text-ledger transition-colors">unload</button>
                            {m.hasFile && (
                              <button onClick={async () => {
                                if (confirm(`Delete ${m.filename}? This will unload and remove the file.`)) {
                                  await doApi('/v1/node/models/delete-file', { filename: m.filename }); refreshAll()
                                }
                              }} className="text-xs text-gray-600 hover:text-compute transition-colors">delete</button>
                            )}
                          </>
                        )}
                        {m.state === 'on-disk' && (
                          <>
                            <button onClick={async () => {
                              await doApi('/v1/node/models/load', { model_path: m.filePath, name: m.name, backend: 'llama.cpp', ctx_len: m.ctx || 4096 })
                              refreshAll()
                            }} className="text-xs text-spore hover:text-spore/80 transition-colors">load</button>
                            <button onClick={async () => {
                              if (confirm(`Delete ${m.filename}?`)) { await doApi('/v1/node/models/delete-file', { filename: m.filename }); refreshAll() }
                            }} className="text-xs text-gray-600 hover:text-compute transition-colors">delete</button>
                          </>
                        )}
                        {m.state === 'disabled' && (
                          <>
                            <button onClick={async () => { await doApi('/v1/node/models/reload', { model: m.name }); refreshAll() }}
                              className="text-xs text-spore hover:text-spore/80 transition-colors">enable</button>
                            <button onClick={async () => {
                              if (confirm(`Remove config for ${m.name}?`)) { await doApi('/v1/node/models/remove-config', { model: m.name }); refreshAll() }
                            }} className="text-xs text-gray-600 hover:text-compute transition-colors">remove</button>
                          </>
                        )}
                        {m.state === 'loading' && (
                          <span className="text-xs text-gray-600 font-mono">{m.elapsed ? `${m.elapsed}s` : '...'}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="px-5 py-8 text-center text-sm text-gray-600">
                No models on this node. Search HuggingFace below to get started.
              </div>
            )}
          </div>
        )
      })()}

      {/* Add Model */}
      <div className="border border-white/10 bg-[#111] rounded-xl overflow-hidden">
        <div className="flex border-b border-white/10">
          {[
            { id: 'browse', label: 'Browse HuggingFace', icon: '\u{1F917}' },
            { id: 'local', label: 'Local File', icon: '\u{1F4C1}' },
            { id: 'api', label: 'Remote API', icon: '\u{1F517}' },
          ].map(tab => (
            <button key={tab.id} onClick={() => setAddMode(tab.id)}
              className={`flex items-center space-x-2 px-4 py-3 text-xs font-medium border-b-2 transition-all ${
                addMode === tab.id ? 'border-spore text-white bg-black/20' : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}>
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        <div className="p-5">
          {addMode === 'browse' && (
            <div className="space-y-4">
              <div className="flex space-x-2 items-center">
                <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSearch()}
                  placeholder="Search models... (e.g. llama 7b, mistral, phi)"
                  className="flex-1 bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                <button onClick={handleSearch} disabled={searching}
                  className="bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40 whitespace-nowrap">
                  {searching ? 'Searching...' : 'Search'}
                </button>
                <button onClick={() => setFilterCompatible(f => !f)}
                  className={`text-xs px-2 py-1.5 rounded border transition-colors whitespace-nowrap ${filterCompatible ? 'border-spore/30 text-spore bg-spore/5' : 'border-white/10 text-gray-500'}`}>
                  {filterCompatible ? 'Compatible' : 'All sizes'}
                </button>
              </div>

              {/* Repo file picker */}
              {repoFiles && (
                <div className="bg-black border border-white/10 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="font-mono text-sm text-white">{repoFiles.repo_id}</span>
                      <div className="flex items-center space-x-3 mt-1 text-xs text-gray-500">
                        {repoFiles.param_b > 0 && <span>{repoFiles.param_b}B params</span>}
                        {repoFiles.architecture && <span>{repoFiles.architecture}</span>}
                        {repoFiles.context_length > 0 && <span>{repoFiles.context_length.toLocaleString()} ctx</span>}
                      </div>
                    </div>
                    <div className="flex items-center space-x-3">
                      {repoFiles.disk_free_gb > 0 && <span className="text-xs text-gray-600">{repoFiles.disk_free_gb}GB free</span>}
                      <button onClick={() => setFilterCompatible(f => !f)}
                        className={`text-xs px-2 py-0.5 rounded border transition-colors ${filterCompatible ? 'border-spore/30 text-spore bg-spore/5' : 'border-white/10 text-gray-500'}`}>
                        {filterCompatible ? 'Compatible only' : 'Show all'}
                      </button>
                      <button onClick={() => setRepoFiles(null)} className="text-xs text-gray-500 hover:text-white">&times;</button>
                    </div>
                  </div>
                  <div className="overflow-x-auto max-h-[250px] overflow-y-auto custom-scrollbar">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-gray-600 font-mono">
                          <th className="text-left py-1 pr-3">File</th>
                          <th className="text-left py-1 pr-3">Quant</th>
                          <th className="text-right py-1 pr-3">Size</th>
                          <th className="text-right py-1 pr-3">RAM est.</th>
                          <th className="text-right py-1"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {(repoFiles.files || []).filter(f => !filterCompatible || !f.warnings || f.warnings.length === 0).map((f, i) => {
                          // Match download_id: sha256(repo/filename)[:16] — same as server
                          const dlIdSrc = `${repoFiles.repo_id}/${f.filename}`
                          // Find matching download by filename (more reliable than hash matching)
                          const dl = Object.values(downloadStatus).find(d => d.filename === f.filename && d.repo_id === repoFiles.repo_id)
                          const hasWarnings = f.warnings && f.warnings.length > 0
                          const isOnDisk = localFiles.some(lf => lf.filename === f.filename)
                          const isLoaded = models.some(m => f.filename.replace('.gguf', '') === m.name)

                          return (
                            <tr key={i} className={`border-t border-white/5 hover:bg-white/[0.02] ${hasWarnings && !isOnDisk ? 'opacity-60' : ''}`}>
                              <td className="py-1.5 pr-3 font-mono text-gray-300 text-xs truncate max-w-[200px]" title={f.filename}>{f.filename}</td>
                              <td className="py-1.5 pr-3">
                                <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                                  f.quant?.startsWith('Q4') ? 'bg-spore/10 text-spore' :
                                  f.quant?.startsWith('Q5') || f.quant?.startsWith('Q6') ? 'bg-relay/10 text-relay' :
                                  f.quant?.startsWith('Q8') || f.quant === 'F16' ? 'bg-poison/10 text-poison' :
                                  'bg-white/5 text-gray-500'
                                }`}>{f.quant || '?'}</span>
                              </td>
                              <td className="py-1.5 pr-3 text-right text-xs text-gray-400">{f.size_gb}GB</td>
                              <td className="py-1.5 pr-3 text-right text-xs text-gray-500">{f.est_ram_gb ? `~${f.est_ram_gb}GB` : '?'}</td>
                              <td className="py-1.5 text-right min-w-[140px]">
                                {hasWarnings && !isOnDisk && (
                                  <span className="text-compute text-xs mr-2" title={f.warnings.join('; ')}>&#9888;</span>
                                )}
                                {dl && dl.status === 'downloading' ? (
                                  <div className="inline-flex flex-col items-end gap-0.5">
                                    <div className="w-24 bg-void rounded-full h-1.5 overflow-hidden border border-white/5">
                                      <div className="h-full bg-ledger transition-all" style={{ width: `${dl.progress || 0}%` }} />
                                    </div>
                                    <span className="text-xs font-mono text-ledger">
                                      {dl.progress?.toFixed(0)}%
                                      {dl.speed_mbps > 0 && <span className="text-gray-500 ml-1">{dl.speed_mbps}MB/s</span>}
                                      {dl.eta_seconds > 0 && <span className="text-gray-600 ml-1">{dl.eta_seconds > 60 ? `${Math.floor(dl.eta_seconds/60)}m` : `${dl.eta_seconds}s`}</span>}
                                    </span>
                                  </div>
                                ) : dl && dl.status === 'complete' || isOnDisk ? (
                                  <div className="inline-flex items-center space-x-2">
                                    {isLoaded && <span className="text-xs text-spore">loaded</span>}
                                    {!isLoaded && <span className="text-xs text-gray-500">on disk</span>}
                                    <button onClick={async () => {
                                      if (confirm(`Delete ${f.filename}?`)) {
                                        await doApi('/v1/node/models/delete-file', { filename: f.filename })
                                        const doFetch = isRemote && selectedDevice?.addr ? (p) => remoteApi(selectedDevice.addr, p) : api
                                        doFetch('/v1/node/models/local').then(d => setLocalFiles(d.files || [])).catch(() => {})
                                        onRefresh()
                                      }
                                    }} className="text-xs text-gray-600 hover:text-compute" title="Delete file">&#128465;</button>
                                  </div>
                                ) : dl && dl.status === 'failed' ? (
                                  <span className="text-xs text-compute font-mono">failed</span>
                                ) : (
                                  <button onClick={() => handleDownload(repoFiles.repo_id, f.filename, f)}
                                    className={`text-xs ${hasWarnings ? 'text-ledger hover:text-ledger/80' : 'text-spore hover:text-spore/80'}`}>
                                    {hasWarnings ? 'Download anyway' : 'Download'}
                                  </button>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                    {filterCompatible && (repoFiles.files || []).some(f => f.warnings?.length > 0) && (
                      <div className="text-xs text-gray-600 mt-2 px-1">
                        {(repoFiles.files || []).filter(f => f.warnings?.length > 0).length} variant(s) hidden (exceed node resources).
                        <button onClick={() => setFilterCompatible(false)} className="text-gray-400 hover:text-white ml-1 underline">Show all</button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Search results */}
              {searchResults.length > 0 && !repoFiles && (() => {
                const isCompat = (m) => !m.est_min_ram_gb || !nodeResources.ram_gb || m.est_min_ram_gb <= nodeResources.ram_gb
                const filtered = filterCompatible ? searchResults.filter(isCompat) : searchResults
                const hiddenCount = searchResults.length - filtered.length
                return (
                  <>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {filtered.map((m, i) => {
                        const compat = isCompat(m)
                        return (
                          <button key={i} onClick={() => handleBrowseRepo(m.repo_id)}
                            className={`text-left bg-black border rounded-lg p-3 hover:border-spore/30 transition-colors ${compat ? 'border-white/10' : 'border-white/5 opacity-50'}`}>
                            <div className="flex items-center justify-between">
                              <div className="font-mono text-sm text-white truncate">{m.repo_id}</div>
                              {!compat && <span className="text-compute text-xs ml-1" title="May exceed node resources">&#9888;</span>}
                            </div>
                            <div className="flex items-center flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-xs text-gray-500">
                              {m.param_b > 0 && <span className="text-gray-300 font-medium">{m.param_b}B</span>}
                              {m.architecture && <span>{m.architecture}</span>}
                              {m.context_length > 0 && <span>{(m.context_length / 1000).toFixed(0)}k ctx</span>}
                              {m.est_min_size_gb > 0 && <span>~{m.est_min_size_gb}GB</span>}
                              <span>&darr;{m.downloads?.toLocaleString()}</span>
                              {m.license && <span>{m.license}</span>}
                            </div>
                          </button>
                        )
                      })}
                    </div>
                    {hiddenCount > 0 && (
                      <div className="text-xs text-gray-600 mt-2">
                        {hiddenCount} model(s) hidden (too large for {nodeResources.ram_gb}GB RAM).
                        <button onClick={() => setFilterCompatible(false)} className="text-gray-400 hover:text-white ml-1 underline">Show all</button>
                      </div>
                    )}
                  </>
                )
              })()}

              {searchResults.length === 0 && !searching && hasSearched && (
                <div className="text-center text-sm text-gray-600 py-4">No GGUF models found for &ldquo;{searchQuery}&rdquo;</div>
              )}

              {/* Suggested models — show when no search active */}
              {!hasSearched && !repoFiles && suggestions && suggestions.length > 0 && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-xs text-gray-500 font-mono uppercase tracking-wider">Suggested for this node</h3>
                    {nodeResources.ram_gb > 0 && <span className="text-xs text-gray-600">{nodeResources.ram_gb}GB RAM · {nodeResources.disk_free_gb}GB disk free</span>}
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {suggestions.filter(s => !filterCompatible || s.compatible).map((s, i) => (
                      <button key={i} onClick={() => handleBrowseRepo(s.repo_id)}
                        className={`text-left bg-black border rounded-lg p-3 hover:border-spore/30 transition-colors ${s.compatible ? 'border-spore/20' : 'border-white/5 opacity-50'}`}>
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-sm text-white truncate">{s.repo_id.split('/').pop()}</span>
                          {s.compatible
                            ? <span className="text-spore text-xs">&#10003;</span>
                            : <span className="text-compute text-xs">&#9888;</span>
                          }
                        </div>
                        <div className="text-xs text-gray-500 mt-1">{s.description}</div>
                        <div className="flex items-center space-x-3 mt-1 text-xs text-gray-600">
                          <span>{s.param_b}B</span>
                          <span>~{s.est_size_gb}GB (Q4)</span>
                          <span>needs {s.min_ram_gb}GB+ RAM</span>
                        </div>
                      </button>
                    ))}
                  </div>
                  {filterCompatible && suggestions.some(s => !s.compatible) && (
                    <div className="text-xs text-gray-600 mt-2">
                      {suggestions.filter(s => !s.compatible).length} model(s) hidden.
                      <button onClick={() => setFilterCompatible(false)} className="text-gray-400 hover:text-white ml-1 underline">Show all</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {addMode === 'local' && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">Load a GGUF model file from the local filesystem.</p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="md:col-span-2">
                  <label className="text-xs text-gray-500 block mb-1">Model path (.gguf)</label>
                  <input value={form.model_path} onChange={e => setForm(f => ({...f, model_path: e.target.value}))}
                    placeholder="/path/to/model.gguf"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Name (optional)</label>
                  <input value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))}
                    placeholder="my-model"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
              </div>
              <button onClick={() => handleLoadLocal()} disabled={loading}
                className="bg-spore text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-spore/90 disabled:opacity-40">
                {loading ? 'Loading...' : 'Load Model'}
              </button>
            </div>
          )}

          {addMode === 'api' && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">Connect to an OpenAI-compatible API endpoint (OpenRouter, Ollama, vLLM, etc.)</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Model name</label>
                  <input value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))}
                    placeholder="claude-sonnet"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">API Base URL</label>
                  <input value={form.api_base} onChange={e => setForm(f => ({...f, api_base: e.target.value}))}
                    placeholder="https://openrouter.ai/api/v1"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">API Key</label>
                  <input type={showKey ? 'text' : 'password'} value={form.api_key} onChange={e => setForm(f => ({...f, api_key: e.target.value}))}
                    placeholder="sk-..."
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Upstream model</label>
                  <input value={form.api_model} onChange={e => setForm(f => ({...f, api_model: e.target.value}))}
                    placeholder="anthropic/claude-sonnet-4"
                    className="w-full bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none" />
                </div>
              </div>
              <button onClick={() => handleLoadApi()} disabled={loading}
                className="bg-white/10 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-white/20 disabled:opacity-40">
                {loading ? 'Connecting...' : 'Connect API'}
              </button>
            </div>
          )}

          {result && (
            <div className={`flex items-center space-x-2 text-sm p-2.5 rounded-lg mt-3 ${
              result.error ? 'bg-compute/10 text-compute' : 'bg-spore/10 text-spore'
            }`}>
              {result.error ? <AlertCircle size={14} /> : <Check size={14} />}
              <span>{result.error || result.success}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Chat Tab ──

function ChatTab() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [model, setModel] = useState('auto')  // 'auto' = best available
  const [models, setModels] = useState([])
  const [sending, setSending] = useState(false)
  const endRef = useRef(null)

  // Fetch models on mount + poll (includes fleet)
  useEffect(() => {
    const fetch_ = () => api('/v1/models').then(d => setModels(d.data || [])).catch(() => {})
    fetch_()
    // Poll faster initially to catch fleet announcements, then slow down
    const fast = setInterval(fetch_, 3000)
    const slowDown = setTimeout(() => { clearInterval(fast); setInterval(fetch_, 10000) }, 15000)
    return () => { clearInterval(fast); clearTimeout(slowDown) }
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
          model: model === 'auto' ? '' : model,  // empty = auto-route
          messages: history.map(m => ({ role: m.role, content: m.content })),
          max_tokens: 2048,
        }),
      })
      const text = resp.choices?.[0]?.message?.content || '[no response]'
      const usage = resp.usage || {}
      const routedTo = resp.model || 'unknown'
      setMessages([...history, {
        role: 'assistant', content: text, model: routedTo,
        tokens: `${usage.prompt_tokens || 0}+${usage.completion_tokens || 0}`,
      }])
    } catch (e) {
      setMessages([...history, { role: 'assistant', content: `[Error: ${e.message}]` }])
    }
    setSending(false)
  }

  // Group models by source
  const localModels = models.filter(m => m.owned_by === 'local')
  const peerModels = models.filter(m => m.owned_by?.startsWith('peer:'))
  const fleetModels = models.filter(m => m.owned_by?.startsWith('fleet:'))

  return (
    <div className="border border-white/10 bg-[#111] rounded-xl overflow-hidden flex flex-col h-[calc(100vh-220px)]">
      {/* Model selector */}
      <div className="h-12 border-b border-white/10 bg-black/50 flex items-center px-4 space-x-3">
        <MessageSquare size={14} className="text-spore" />
        <select value={model} onChange={e => setModel(e.target.value)}
          className="bg-black border border-white/10 rounded px-2 py-1 text-sm font-mono text-white focus:outline-none min-w-[200px]">
          <option value="auto">Automatic (best available)</option>
          {localModels.length > 0 && <optgroup label="Local">
            {localModels.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
          </optgroup>}
          {fleetModels.length > 0 && <optgroup label="Fleet">
            {fleetModels.map(m => <option key={m.id} value={m.id}>{m.id} ({m.owned_by.replace('fleet:', '')})</option>)}
          </optgroup>}
          {peerModels.length > 0 && <optgroup label="Peers (QUIC)">
            {peerModels.map(m => <option key={m.id} value={m.id}>{m.id} ({m.owned_by.replace('peer:', '')})</option>)}
          </optgroup>}
        </select>
        <span className="text-xs text-gray-600">{models.length} model{models.length !== 1 ? 's' : ''} on network</span>
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
            <p className="text-sm">Send a message to start a conversation.</p>
            <p className="text-xs mt-1 text-gray-600">
              {model === 'auto'
                ? 'Automatic mode — routes to the best available model on the network.'
                : `Using ${model}. The network handles routing and failover.`
              }
            </p>
            {models.length === 0 && (
              <p className="text-xs mt-2 text-compute">No models available. Load a model on the Models tab first.</p>
            )}
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
          <div className="overflow-x-auto max-h-[500px] overflow-y-auto custom-scrollbar">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-[#111]">
                <tr className="text-xs text-gray-500 font-mono uppercase">
                  <th className="text-left py-2 pr-4 w-8"></th>
                  <th className="text-left py-2 pr-4">Time</th>
                  <th className="text-left py-2 pr-4">Type</th>
                  <th className="text-left py-2 pr-4">Counterparty</th>
                  <th className="text-right py-2">Amount</th>
                </tr>
              </thead>
              <tbody>
                {history.map((tx, i) => {
                  const isCredit = tx.direction === 'credit'
                  const ts = tx.timestamp ? new Date(tx.timestamp * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
                  const reason = (tx.reason || '').replace(/_/g, ' ')
                  const counterparty = tx.counterparty_id ? tx.counterparty_id.slice(0, 12) + '...' : ''
                  return (
                    <tr key={i} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="py-2 pr-2">
                        <div className={`w-1.5 h-1.5 rounded-full ${isCredit ? 'bg-spore' : 'bg-compute'}`} />
                      </td>
                      <td className="py-2 pr-4 text-gray-500 font-mono text-xs whitespace-nowrap">{ts}</td>
                      <td className="py-2 pr-4 text-gray-300 whitespace-nowrap">{reason}</td>
                      <td className="py-2 pr-4 text-gray-500 font-mono text-xs">{counterparty}</td>
                      <td className={`py-2 text-right font-mono whitespace-nowrap ${isCredit ? 'text-spore' : 'text-compute'}`}>
                        {isCredit ? '+' : '-'}{tx.amount?.toFixed(4)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
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
    const path = window.location.pathname.slice(1)
    return TABS.find(t => t.id === path) ? path : 'overview'
  })
  const setTab = (id) => { _setTab(id); window.history.pushState(null, '', `/${id}`) }

  // Handle browser back/forward
  useEffect(() => {
    const onPop = () => {
      const path = window.location.pathname.slice(1)
      _setTab(TABS.find(t => t.id === path) ? path : 'overview')
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const [status, setStatus] = useState(null)
  const [credits, setCredits] = useState({ balance: 0, earned: 0, spent: 0 })
  const [logs, setLogs] = useState([])
  const [refreshTick, setRefreshTick] = useState(0)
  const [fleetCount, setFleetCount] = useState(0)
  const [liveActivityEvents, setLiveActivityEvents] = useState([])
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

  // Poll fleet nodes from registry
  const [fleetNodes, setFleetNodes] = useState([])
  useEffect(() => {
    if (appState !== 'dashboard') return
    const fetch_ = () => api('/v1/admin/nodes').then(d => {
      const nodes = d.nodes || []
      setFleetNodes(nodes)
      setFleetCount(nodes.length)
    }).catch(() => {})
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

  // SSE activity stream for canvas
  useEffect(() => {
    if (appState !== 'dashboard') return
    const es = new EventSource('/v1/node/activity/stream')
    es.onmessage = (event) => {
      try {
        const e = JSON.parse(event.data)
        setLiveActivityEvents(prev => {
          const next = [...prev, e]
          return next.length > 50 ? next.slice(-50) : next
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
      <div className="min-h-screen bg-void text-console font-sans relative">
        <NetworkCanvas
          selfNode={status}
          peers={status?.peers || []}
          fleetNodes={fleetNodes}
          activityEvents={liveActivityEvents}
        />
        {/* Header */}
        <header className="border-b border-white/10 bg-void/80 backdrop-blur-md sticky top-0 z-50 relative">
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
        <nav className="border-b border-white/5 bg-void/60 relative z-10">
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
        <main className="max-w-7xl mx-auto px-4 py-6 relative z-10">
          {tab === 'overview' && <OverviewTab status={status} credits={credits} fleetNodes={fleetNodes} />}
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
