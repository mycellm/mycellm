/**
 * One control, one value — what the user picked in the model selector.
 *
 * ⚠️ TIER AND MODEL ARE MUTUALLY EXCLUSIVE, AND THIS FILE IS WHY THAT IS NOW
 * STRUCTURAL RATHER THAN A RULE. A resolution constraint like `min_tier` only
 * means something while the node is still choosing the model; once the caller
 * names one, there is nothing left to constrain. The dashboard always knew
 * that and dropped `min_tier` whenever a model was named — correct, and
 * invisible, so the tier controls in the routing panel looked like they had
 * simply been ignored.
 *
 * Collapsing both into a single selector value removes the failure instead of
 * explaining it: choosing "Frontier" IS choosing `model: ''` with a floor, and
 * choosing a model cannot express a tier because there is no control left to
 * do it with.
 */

/** Tier options are encoded into the selector's value with this prefix. */
export const TIER_PREFIX = 'tier:'

/** Highest first — the order the selector renders, and the order of quality. */
export const TIERS = ['frontier', 'capable', 'fast', 'tiny'] as const
export type Tier = (typeof TIERS)[number]

export interface Selection {
  /** Sent as `model`. Empty means the node resolves. */
  model: string
  /** Sent as `mycellm.min_tier`. Empty means no floor. */
  minTier: string
}

/** Decode a selector value into what the request should actually carry. */
export function parseSelection(value: string): Selection {
  if (value.startsWith(TIER_PREFIX)) {
    return { model: '', minTier: value.slice(TIER_PREFIX.length) }
  }
  return { model: value, minTier: '' }
}

/** Encode a tier choice as a selector value. */
export function tierValue(tier: string): string {
  return `${TIER_PREFIX}${tier}`
}

/**
 * Is the node choosing the model? True for Auto and for any tier floor —
 * both leave `model` empty, which is exactly the condition under which
 * resolution constraints apply.
 */
export function isResolving(value: string): boolean {
  return parseSelection(value).model === ''
}

const RANK: Record<string, number> = { frontier: 4, capable: 3, fast: 2, tiny: 1 }

/**
 * How many reachable models satisfy a tier floor.
 *
 * Mirrors the server's comparison in `model_resolver._apply_constraints`: a
 * floor admits its own tier and everything above it. Models whose tier the
 * node could not derive are counted as unknown rather than assumed to
 * qualify — overstating availability here would promise a route that the
 * resolver then refuses, which is the failure this whole change removes.
 */
export function countAtTier(models: { tier?: string }[], tier: string): number {
  const floor = RANK[tier] ?? 0
  if (!floor) return models.length
  return models.filter((m) => (RANK[m.tier ?? ''] ?? 0) >= floor).length
}
