/**
 * Shared colour conventions.
 *
 * ⚠️ TWO AXES USED TO SHARE COLOURS. Green meant both "GGUF" and "on device";
 * blue meant both "MLX" and "network". A green badge could not tell you which
 * of two unrelated things it was asserting, which is worse than no colour.
 *
 * They are disjoint now, and the same on iOS:
 *
 *   FORMAT   — what the weights are:  MLX = blue,   GGUF = orange
 *   LOCATION — where it runs:         device = green, network = purple
 *
 * Purple is free for location because nothing ever used it for errors —
 * failures are red on both surfaces, whatever the palette comment claimed.
 */

/** Tailwind classes for a model format. */
export function formatStyle(backend: string | undefined): {
  text: string
  badge: string
  label: string
} {
  const mlx = (backend ?? '').toLowerCase().includes('mlx')
  return mlx
    ? { text: 'text-relay', badge: 'bg-relay/10 text-relay', label: 'MLX' }
    : { text: 'text-format', badge: 'bg-format/10 text-format', label: 'GGUF' }
}

/** Tailwind classes for where a model runs. */
export function locationStyle(ownedBy: string): {
  text: string
  badge: string
  label: string
} {
  if (ownedBy === 'local') {
    return { text: 'text-spore', badge: 'bg-spore/10 text-spore', label: 'device' }
  }
  if (ownedBy === 'mycellm') {
    // Strategy models (`auto`, `mycellm/swarm`) are not anywhere in
    // particular — they select a strategy, so neither location colour applies.
    return { text: 'text-gray-400', badge: 'bg-white/5 text-gray-400', label: 'strategy' }
  }
  return { text: 'text-poison', badge: 'bg-poison/10 text-poison', label: 'network' }
}
