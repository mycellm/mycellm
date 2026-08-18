import { SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { RoutingOptions as RoutingOpts } from '@/api/types'

interface RoutingOptionsProps {
  options: RoutingOpts
  onChange: (opts: RoutingOpts) => void
  open: boolean
  onToggle: () => void
}

interface RoutingPanelProps {
  options: RoutingOpts
  onChange: (opts: RoutingOpts) => void
  open: boolean
  /** Swarm controls only appear when a swarm can actually use them. */
  swarm?: boolean
}

const TIERS = ['any', 'tiny', 'fast', 'capable', 'frontier'] as const
const TAGS = ['code', 'reasoning', 'vision'] as const
const TRUST = ['', 'local', 'trusted', 'any'] as const
const FANOUTS = [0, 2, 3, 4, 5] as const
const BUDGETS = [0, 512, 2048, 8192] as const

// ⚠️ THERE IS NO "MODE" SELECTOR HERE ANY MORE, AND THAT IS THE FIX.
// This panel used to offer Best Quality / Fastest, and sent whichever was
// picked as `mycellm.routing`. The node implements exactly one mode: 0.8 made
// it refuse the rest with HTTP 400 rather than accept-and-ignore them, which
// turned the Fastest button into a chat request that could only fail. Offering
// a control the server rejects is worse than offering none — the user believes
// they changed the routing. When a second mode is implemented, it comes back
// here and in the `routing` literal in types.ts together.

function OptionButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded-md border px-2 py-1 text-xs font-medium transition-colors',
        active
          ? 'border-spore/30 bg-spore/10 text-spore'
          : 'border-white/10 text-gray-500 hover:text-gray-300'
      )}
    >
      {children}
    </button>
  )
}

/// The toggle. Lives in the chat toolbar next to the model selector.
export function RoutingOptions({ options, onChange, open, onToggle }: RoutingOptionsProps) {
  const { t } = useTranslation('chat')
  const active =
    options.min_tier !== 'any' ||
    options.required_tags.length > 0 ||
    options.trust !== '' ||
    options.fanout > 0 ||
    options.token_budget > 0

  // `onChange` is unused by the button itself but kept on the props so the two
  // halves take the same shape and a caller cannot wire one without the other.
  void onChange

  return (
    <button
      onClick={onToggle}
      className={cn(
        'flex items-center space-x-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
        open || active
          ? 'border-spore/30 bg-spore/5 text-spore'
          : 'border-white/10 text-gray-500 hover:text-gray-300'
      )}
    >
      <SlidersHorizontal size={12} />
      <span className="hidden sm:inline">{t('routing.title')}</span>
    </button>
  )
}

/// ⚠️ THIS RENDERS BELOW THE TOOLBAR, NOT INSIDE IT — and that is a fix, not a
/// preference. The panel used to be a sibling of the toggle inside the toolbar's
/// flex row. With four option groups it happened to fit on one line; adding
/// trust, proposer count and token budget made it wrap, and the second row drew
/// over the model selector and the "N models on network" label. A real browser
/// showed it; type-checking never could.
export function RoutingOptionsPanel({
  options,
  onChange,
  open,
  swarm = false,
}: RoutingPanelProps) {
  const { t } = useTranslation('chat')

  if (!open) return null

  const toggleTag = (tag: string) => {
    const tags = options.required_tags.includes(tag)
      ? options.required_tags.filter((x) => x !== tag)
      : [...options.required_tags, tag]
    onChange({ ...options, required_tags: tags })
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-white/10 bg-black/30 px-4 py-2.5 text-xs">
      {/* Min quality */}
      <div className="flex items-center gap-1.5">
        <span className="text-gray-500">{t('routing.minQuality')}:</span>
        <div className="flex gap-1">
          {TIERS.map((tier) => (
            <OptionButton
              key={tier}
              active={options.min_tier === tier}
              onClick={() => onChange({ ...options, min_tier: tier })}
            >
              {t(`tiers.${tier}`)}
            </OptionButton>
          ))}
        </div>
      </div>

      {/* Tags */}
      <div className="flex items-center gap-1.5">
        <span className="text-gray-500">{t('routing.tags')}:</span>
        <div className="flex gap-1">
          {TAGS.map((tag) => (
            <OptionButton
              key={tag}
              active={options.required_tags.includes(tag)}
              onClick={() => toggleTag(tag)}
            >
              {t(`tags.${tag}`)}
            </OptionButton>
          ))}
        </div>
      </div>

      {/* Trust floor — where a prompt is allowed to go, not how fast. */}
      <div className="flex items-center gap-1.5">
        <span className="text-gray-500">{t('routing.trust')}:</span>
        <div className="flex gap-1">
          {TRUST.map((level) => (
            <OptionButton
              key={level || 'default'}
              active={options.trust === level}
              onClick={() => onChange({ ...options, trust: level })}
            >
              {t(`trust.${level || 'default'}`)}
            </OptionButton>
          ))}
        </div>
      </div>

      {/* Fallback */}
      <div className="flex items-center gap-1.5">
        <span className="text-gray-500">{t('routing.fallback')}:</span>
        <div className="flex gap-1">
          <OptionButton
            active={options.fallback === 'downgrade'}
            onClick={() => onChange({ ...options, fallback: 'downgrade' })}
          >
            {t('routing.useNextBest')}
          </OptionButton>
          <OptionButton
            active={options.fallback === 'reject'}
            onClick={() => onChange({ ...options, fallback: 'reject' })}
          >
            {t('routing.reject')}
          </OptionButton>
        </div>
      </div>

      {swarm && (
        <>
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500">{t('routing.fanout')}:</span>
            <div className="flex gap-1">
              {FANOUTS.map((n) => (
                <OptionButton
                  key={n}
                  active={options.fanout === n}
                  onClick={() => onChange({ ...options, fanout: n })}
                >
                  {n === 0 ? t('routing.auto') : n}
                </OptionButton>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-gray-500">{t('routing.tokenBudget')}:</span>
            <div className="flex gap-1">
              {BUDGETS.map((n) => (
                <OptionButton
                  key={n}
                  active={options.token_budget === n}
                  onClick={() => onChange({ ...options, token_budget: n })}
                >
                  {n === 0 ? t('routing.noLimit') : n}
                </OptionButton>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
