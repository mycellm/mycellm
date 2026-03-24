import { useRef, useEffect } from 'react'
import { useNodeStore } from '../../stores/node'
import { useFleetStore } from '../../stores/fleet'
import { useActivityStore } from '../../stores/activity'
import type { PhysicsNode } from './physics'
import { repulse, dampen, updatePositions, attractToTarget } from './physics'
import type { Particle, AmbientSpore } from './particles'
import {
  spawnParticle,
  updateParticles,
  drawParticle,
  createAmbientSpores,
  drawAmbientSpores,
} from './particles'

// Colors
const COLOR_SELF = '#22C55E'
const COLOR_PEER = '#3B82F6'
const COLOR_FLEET = '#FACC15'
const COLOR_INFERENCE = '#EF4444'
const COLOR_CREDIT = '#FACC15'

interface CanvasNode extends PhysicsNode {
  color: string
  pulse: number
  connectedTo?: string
}

/**
 * Full-viewport force-directed network visualization.
 * Renders behind all dashboard content as a living background.
 *
 * All mutable state is stored in refs to avoid React re-renders
 * during the animation loop. Store data is read via subscriptions.
 */
export default function NetworkCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const nodesRef = useRef<CanvasNode[]>([])
  const particlesRef = useRef<Particle[]>([])
  const sporesRef = useRef<AmbientSpore[]>([])
  const frameRef = useRef<number>(0)
  const liveEventCountRef = useRef<number>(0)

  // Subscribe to store changes without causing re-renders
  useEffect(() => {
    function buildNodes() {
      const status = useNodeStore.getState().status
      const fleetNodes = useFleetStore.getState().fleetNodes
      const peers = status?.peers ?? []
      const prevNodes = nodesRef.current

      const nodes: CanvasNode[] = []

      // Self node: fixed at center, green, pulsing
      const prevSelf = prevNodes.find((n) => n.id === 'self')
      nodes.push({
        id: 'self',
        type: 'self',
        name: status?.node_name || 'self',
        x: 0,
        y: 0,
        vx: 0,
        vy: 0,
        radius: 6,
        fixed: true,
        color: COLOR_SELF,
        pulse: prevSelf?.pulse ?? 0,
      })

      // QUIC peers: blue, repelling
      for (const p of peers) {
        const id = p.peer_id || `peer-${Math.random().toString(36).slice(2)}`
        const existing = prevNodes.find((n) => n.id === id)
        nodes.push({
          id,
          type: 'peer',
          name: (p.peer_id || '').slice(0, 8),
          x: existing?.x ?? (Math.random() - 0.5) * 300,
          y: existing?.y ?? (Math.random() - 0.5) * 200,
          vx: 0,
          vy: 0,
          radius: 4,
          color: COLOR_PEER,
          pulse: existing?.pulse ?? 0,
          connectedTo: 'self',
        })
      }

      // Fleet nodes: gold, connected to self
      for (const f of fleetNodes) {
        if (f.status !== 'approved' || !f.online) continue
        const id = f.peer_id || f.node_name || `fleet-${Math.random().toString(36).slice(2)}`
        const existing = prevNodes.find((n) => n.id === id)
        nodes.push({
          id,
          type: 'fleet',
          name: f.node_name || (f.peer_id || '').slice(0, 8),
          x: existing?.x ?? (Math.random() - 0.5) * 400,
          y: existing?.y ?? (Math.random() - 0.5) * 300,
          vx: 0,
          vy: 0,
          radius: 4,
          color: COLOR_FLEET,
          pulse: existing?.pulse ?? 0,
          connectedTo: 'self',
        })
      }

      nodesRef.current = nodes
    }

    // Initial build
    buildNodes()

    // Subscribe to store changes (plain subscribe, no selector middleware)
    let prevStatus = useNodeStore.getState().status
    let prevFleet = useFleetStore.getState().fleetNodes
    const unsubNode = useNodeStore.subscribe((state) => {
      if (state.status !== prevStatus) {
        prevStatus = state.status
        buildNodes()
      }
    })
    const unsubFleet = useFleetStore.subscribe((state) => {
      if (state.fleetNodes !== prevFleet) {
        prevFleet = state.fleetNodes
        buildNodes()
      }
    })

    return () => {
      unsubNode()
      unsubFleet()
    }
  }, [])

  // Subscribe to live activity events for particle spawning
  useEffect(() => {
    const unsub = useActivityStore.subscribe((state) => {
      const liveEvents = state.liveEvents
      if (liveEvents.length <= liveEventCountRef.current) {
        liveEventCountRef.current = liveEvents.length
        return
      }
      // Process new events since last check
      const newEvents = liveEvents.slice(liveEventCountRef.current)
      liveEventCountRef.current = liveEvents.length

      const selfNode = nodesRef.current.find((n) => n.id === 'self')
      if (!selfNode) return

      for (const evt of newEvents) {
        // Pulse self on any activity
        selfNode.pulse = 1.0

        // Find target and spawn particle
        const routedTo = evt.routed_to || evt.peer_id || ''
        if (!routedTo) continue
        const target = nodesRef.current.find(
          (n) => n.id === routedTo || n.name === routedTo.slice(0, 8),
        )
        if (!target) continue

        target.pulse = 1.0
        const color = evt.type?.includes('credit') ? COLOR_CREDIT : COLOR_INFERENCE
        particlesRef.current.push(
          spawnParticle(
            { x: selfNode.x, y: selfNode.y },
            { x: target.x, y: target.y },
            color,
          ),
        )
      }
    })

    return unsub
  }, [])

  // Main animation loop
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const resize = () => {
      canvas.width = window.innerWidth
      canvas.height = window.innerHeight
      // Re-initialize spores on resize to fill new dimensions
      sporesRef.current = createAmbientSpores(15, canvas.width, canvas.height)
    }
    resize()
    window.addEventListener('resize', resize)

    function draw() {
      const w = canvas!.width
      const h = canvas!.height
      const cx = w / 2
      const cy = h / 2
      ctx!.clearRect(0, 0, w, h)

      const nodes = nodesRef.current
      if (nodes.length === 0) {
        frameRef.current = requestAnimationFrame(draw)
        return
      }

      // --- Physics ---

      // Repulsion between all nodes
      repulse(nodes)

      // Spring attraction for connected nodes toward their target
      for (const node of nodes) {
        if (!node.connectedTo) continue
        const target = nodes.find((n) => n.id === node.connectedTo)
        if (target) attractToTarget(node, target, 150, 0.001)
      }

      // Damping for smooth settling
      dampen(nodes, 0.9)

      // Integrate positions with boundary clamping
      updatePositions(nodes, 1, { halfW: w * 0.4, halfH: h * 0.35 })

      // --- Draw connections ---
      for (const node of nodes) {
        if (!node.connectedTo) continue
        const target = nodes.find((n) => n.id === node.connectedTo)
        if (!target) continue

        const isFleet = node.type === 'fleet'
        const alpha = (isFleet ? 0.1 : 0.05) + Math.max(node.pulse, target.pulse) * 0.2

        ctx!.beginPath()
        ctx!.moveTo(cx + node.x, cy + node.y)
        ctx!.lineTo(cx + target.x, cy + target.y)
        ctx!.strokeStyle = `rgba(255, 255, 255, ${alpha})`
        ctx!.lineWidth = isFleet ? 1.2 : 0.5
        ctx!.stroke()
      }

      // --- Draw particles ---
      updateParticles(particlesRef.current, 1)
      for (const p of particlesRef.current) {
        drawParticle(ctx!, p, cx, cy)
      }

      // --- Draw nodes ---
      for (const node of nodes) {
        const nx = cx + node.x
        const ny = cy + node.y

        // Pulse decay
        if (node.pulse > 0) node.pulse *= 0.95

        // Glow on pulse
        if (node.pulse > 0.1) {
          ctx!.beginPath()
          ctx!.arc(nx, ny, node.radius + 8 * node.pulse, 0, Math.PI * 2)
          ctx!.fillStyle = `rgba(34, 197, 94, ${node.pulse * 0.2})`
          ctx!.fill()
        }

        // Self node pulsing aura
        if (node.type === 'self') {
          const t = Date.now() * 0.002
          const breathe = Math.sin(t) * 0.08 + 0.15
          ctx!.beginPath()
          ctx!.arc(nx, ny, node.radius + 4, 0, Math.PI * 2)
          ctx!.fillStyle = `rgba(34, 197, 94, ${breathe})`
          ctx!.fill()
        }

        // Node circle
        const baseAlpha = node.type === 'self' ? 0.45 : node.type === 'fleet' ? 0.35 : 0.12
        ctx!.beginPath()
        ctx!.arc(nx, ny, node.radius, 0, Math.PI * 2)
        ctx!.fillStyle = node.color
        ctx!.globalAlpha = baseAlpha + node.pulse * 0.5
        ctx!.fill()
        ctx!.globalAlpha = 1

        // Border
        const borderAlpha =
          node.type === 'self' ? 0.6 : node.type === 'fleet' ? 0.5 : 0.2
        ctx!.beginPath()
        ctx!.arc(nx, ny, node.radius, 0, Math.PI * 2)
        ctx!.strokeStyle = node.color
        ctx!.lineWidth = node.type === 'peer' ? 0.5 : 1.5
        ctx!.globalAlpha = borderAlpha + node.pulse * 0.5
        ctx!.stroke()
        ctx!.globalAlpha = 1

        // Labels on fleet nodes only
        if (node.type === 'fleet' && node.name) {
          ctx!.font = '10px JetBrains Mono, ui-monospace, monospace'
          ctx!.fillStyle = `rgba(229, 229, 229, ${0.4 + node.pulse * 0.4})`
          ctx!.textAlign = 'center'
          ctx!.fillText(node.name, nx, ny + node.radius + 14)
        }
      }

      // --- Ambient spores ---
      const now = Date.now() * 0.001
      drawAmbientSpores(ctx!, sporesRef.current, w, h, now)

      frameRef.current = requestAnimationFrame(draw)
    }

    frameRef.current = requestAnimationFrame(draw)

    return () => {
      window.removeEventListener('resize', resize)
      cancelAnimationFrame(frameRef.current)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-0 pointer-events-none"
      aria-hidden="true"
    />
  )
}
