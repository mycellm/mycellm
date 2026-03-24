export interface PhysicsNode {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
  fixed?: boolean
  id: string
  type: 'self' | 'peer' | 'fleet'
  name?: string
}

/**
 * Apply repulsion force between all node pairs to prevent overlap.
 * Uses inverse-square law: F = strength / dist^2
 */
export function repulse(nodes: PhysicsNode[], minDist = 30): void {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].fixed) continue
    for (let j = 0; j < nodes.length; j++) {
      if (i === j) continue
      const dx = nodes[i].x - nodes[j].x
      const dy = nodes[i].y - nodes[j].y
      const distSq = dx * dx + dy * dy
      const dist = Math.sqrt(distSq) || 1
      if (dist > 500) continue // skip distant nodes for perf
      const force = 5000 / (distSq || minDist * minDist)
      const fx = (dx / dist) * force * 0.01
      const fy = (dy / dist) * force * 0.01
      nodes[i].vx += fx
      nodes[i].vy += fy
    }
  }
}

/**
 * Pull non-fixed nodes toward a center point (or toward a connected target).
 * Strength controls how aggressively nodes are pulled.
 */
export function attract(
  nodes: PhysicsNode[],
  center: { x: number; y: number },
  strength = 0.001,
): void {
  for (const node of nodes) {
    if (node.fixed) continue
    const dx = center.x - node.x
    const dy = center.y - node.y
    const dist = Math.sqrt(dx * dx + dy * dy) || 1
    // Spring force toward center, rest length 0
    node.vx += (dx / dist) * dist * strength
    node.vy += (dy / dist) * dist * strength
  }
}

/**
 * Apply velocity damping to all non-fixed nodes for smooth settling.
 */
export function dampen(nodes: PhysicsNode[], factor = 0.95): void {
  for (const node of nodes) {
    if (node.fixed) continue
    node.vx *= factor
    node.vy *= factor
  }
}

/**
 * Integrate velocity into position, clamped to bounds.
 */
export function updatePositions(
  nodes: PhysicsNode[],
  dt: number,
  bounds?: { halfW: number; halfH: number },
): void {
  for (const node of nodes) {
    if (node.fixed) continue
    node.x += node.vx * dt
    node.y += node.vy * dt
    if (bounds) {
      node.x = Math.max(-bounds.halfW, Math.min(bounds.halfW, node.x))
      node.y = Math.max(-bounds.halfH, Math.min(bounds.halfH, node.y))
    }
  }
}

/**
 * Apply spring attraction toward a specific target node (for connected edges).
 * Rest length determines the ideal distance between connected nodes.
 */
export function attractToTarget(
  node: PhysicsNode,
  target: PhysicsNode,
  restLength = 150,
  strength = 0.001,
): void {
  if (node.fixed) return
  const dx = target.x - node.x
  const dy = target.y - node.y
  const dist = Math.sqrt(dx * dx + dy * dy) || 1
  const force = (dist - restLength) * strength
  node.vx += (dx / dist) * force
  node.vy += (dy / dist) * force
}
