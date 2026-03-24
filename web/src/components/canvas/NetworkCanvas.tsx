import { useRef, useEffect } from 'react'

const CONNECTION_DIST = 150
const PARTICLE_COLOR = [74, 222, 128] // green-400 / spore-ish

export default function NetworkCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let W = 0
    let H = 0
    let animId = 0

    interface Particle {
      x: number; y: number
      vx: number; vy: number
      r: number; phase: number
    }

    const particles: Particle[] = []

    function resize() {
      W = canvas!.width = window.innerWidth
      H = canvas!.height = window.innerHeight
    }

    function createParticles() {
      const count = Math.min(Math.floor(window.innerWidth / 16), 90)
      particles.length = 0
      for (let i = 0; i < count; i++) {
        particles.push({
          x: Math.random() * W,
          y: Math.random() * H,
          vx: (Math.random() - 0.5) * 0.35,
          vy: (Math.random() - 0.5) * 0.35,
          r: Math.random() * 2 + 0.5,
          phase: Math.random() * Math.PI * 2,
        })
      }
    }

    function loop() {
      ctx!.clearRect(0, 0, W, H)
      const [r, g, b] = PARTICLE_COLOR

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]

        // Move
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0 || p.x > W) p.vx *= -1
        if (p.y < 0 || p.y > H) p.vy *= -1
        p.phase += 0.012

        // Draw particle
        const radius = Math.max(0.2, p.r + Math.sin(p.phase) * 0.6)
        const alpha = 0.5 + Math.sin(p.phase) * 0.3
        ctx!.beginPath()
        ctx!.arc(p.x, p.y, radius, 0, Math.PI * 2)
        ctx!.fillStyle = `rgba(${r},${g},${b},${alpha})`
        ctx!.fill()

        // Draw connections to nearby particles
        for (let j = i + 1; j < particles.length; j++) {
          const q = particles[j]
          const dx = p.x - q.x
          const dy = p.y - q.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < CONNECTION_DIST) {
            ctx!.beginPath()
            ctx!.moveTo(p.x, p.y)
            ctx!.lineTo(q.x, q.y)
            ctx!.strokeStyle = `rgba(${r},${g},${b},${(1 - dist / CONNECTION_DIST) * 0.25})`
            ctx!.lineWidth = 1
            ctx!.stroke()
          }
        }
      }

      animId = requestAnimationFrame(loop)
    }

    resize()
    createParticles()
    loop()

    const onResize = () => {
      resize()
      // Re-create particles if count changed significantly
      const targetCount = Math.min(Math.floor(window.innerWidth / 16), 90)
      if (Math.abs(particles.length - targetCount) > 10) {
        createParticles()
      }
    }

    window.addEventListener('resize', onResize)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('resize', onResize)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-0 opacity-[0.35] pointer-events-none"
    />
  )
}
