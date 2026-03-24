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

const TIERS = ['any', 'tiny', 'fast', 'capable', 'frontier'] as const
const TAGS = ['code', 'reasoning', 'vision'] as const

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

export function RoutingOptions({ options, onChange, open, onToggle }: RoutingOptionsProps) {
  const { t } = useTranslation('chat')

  const toggleTag = (tag: string) => {
    const tags = options.required_tags.includes(tag)
      ? options.required_tags.filter((t) => t !== tag)
      : [...options.required_tags, tag]
    onChange({ ...options, required_tags: tags })
  }

  return (
    <>
      <button
        onClick={onToggle}
        className={cn(
          'flex items-center space-x-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
          open
            ? 'border-spore/30 bg-spore/5 text-spore'
            : 'border-white/10 text-gray-500 hover:text-gray-300'
        )}
      >
        <SlidersHorizontal size={12} />
        <span className="hidden sm:inline">{t('routing.title')}</span>
      </button>

      {open && (
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

          {/* Mode */}
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500">{t('routing.mode')}:</span>
            <div className="flex gap-1">
              <OptionButton
                active={options.routing === 'best'}
                onClick={() => onChange({ ...options, routing: 'best' })}
              >
                {t('routing.bestQuality')}
              </OptionButton>
              <OptionButton
                active={options.routing === 'fastest'}
                onClick={() => onChange({ ...options, routing: 'fastest' })}
              >
                {t('routing.fastest')}
              </OptionButton>
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
        </div>
      )}
    </>
  )
}
