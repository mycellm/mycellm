export interface Particle {
  x: number
  y: number
  tx: number // target x
  ty: number // target y
  speed: number
  life: number // 0-1, decreases over time
  color: string
}

export interface AmbientSpore {
  x: number
  y: number
  vx: number
  vy: number
  r: number
  phase: number
}

/**
 * Create a particle that travels from one point to another.
 */
export function spawnParticle(
  from: { x: number; y: number },
  to: { x: number; y: number },
  color: string,
  speed = 0.03,
): Particle {
  return {
    x: from.x,
    y: from.y,
    tx: to.x,
    ty: to.y,
    speed,
    life: 1.0,
    color,
  }
}

/**
 * Update all particles: move toward target, decrease life.
 * Returns only particles that are still alive.
 */
export function updateParticles(particles: Particle[], _dt: number): Particle[] {
  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i]
    p.life -= p.speed
    if (p.life <= 0) {
      particles.splice(i, 1)
      continue
    }
    // Lerp toward target based on life (1 = start, 0 = arrived)
    const progress = 1 - p.life
    p.x = p.x + (p.tx - p.x) * p.speed * 2
    p.y = p.y + (p.ty - p.y) * p.speed * 2
    // Slight wobble for organic feel
    p.x += Math.sin(progress * Math.PI * 4) * 0.3
    p.y += Math.cos(progress * Math.PI * 4) * 0.3
  }
  return particles
}

/**
 * Draw a single particle on canvas with opacity based on remaining life.
 */
export function drawParticle(
  ctx: CanvasRenderingContext2D,
  p: Particle,
  offsetX: number,
  offsetY: number,
): void {
  const px = offsetX + p.x
  const py = offsetY + p.y
  const alpha = Math.min(p.life, 1 - (1 - p.life) * 0.5)
  ctx.beginPath()
  ctx.arc(px, py, 2, 0, Math.PI * 2)
  ctx.fillStyle = p.color
  ctx.globalAlpha = alpha
  ctx.fill()
  ctx.globalAlpha = 1
}

/**
 * Initialize ambient spores (decorative background particles).
 */
export function createAmbientSpores(
  count: number,
  width: number,
  height: number,
): AmbientSpore[] {
  return Array.from({ length: count }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * 0.15,
    vy: (Math.random() - 0.5) * 0.15,
    r: Math.random() * 1.2 + 0.3,
    phase: Math.random() * Math.PI * 2,
  }))
}

/**
 * Update and draw ambient spores with sine-wave pulsing opacity.
 */
export function drawAmbientSpores(
  ctx: CanvasRenderingContext2D,
  spores: AmbientSpore[],
  width: number,
  height: number,
  time: number,
): void {
  for (const s of spores) {
    s.x += s.vx
    s.y += s.vy
    // Wrap around edges
    if (s.x < 0) s.x = width
    if (s.x > width) s.x = 0
    if (s.y < 0) s.y = height
    if (s.y > height) s.y = 0

    const pulse = Math.sin(time + s.phase) * 0.3 + 0.7
    ctx.beginPath()
    ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(34, 197, 94, ${0.04 * pulse})`
    ctx.fill()
  }
}
