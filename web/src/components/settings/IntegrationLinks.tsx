import { useTranslation } from 'react-i18next'
import { Activity, HeartPulse, FileText, ExternalLink } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface LinkCard {
  title: string
  description: string
  href: string
  icon: LucideIcon
  iconColor: string
}

export function IntegrationLinks() {
  const { t } = useTranslation('settings')

  const links: LinkCard[] = [
    {
      title: t('prometheusMetrics', 'Prometheus Metrics'),
      description: t('metricsEndpoint', '/metrics endpoint'),
      href: '/metrics',
      icon: Activity,
      iconColor: 'text-relay',
    },
    {
      title: t('healthCheck', 'Health Check'),
      description: t('healthEndpoint', '/health endpoint'),
      href: '/health',
      icon: HeartPulse,
      iconColor: 'text-spore',
    },
    {
      title: t('apiDocs', 'API Documentation'),
      description: t('docsEndpoint', 'OpenAPI / Swagger'),
      href: '/docs',
      icon: FileText,
      iconColor: 'text-ledger',
    },
  ]

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <div className="flex items-center space-x-2 mb-4">
        <ExternalLink size={16} className="text-spore" />
        <h3 className="text-console font-medium text-sm">
          {t('integrations.title', 'Integrations')}
        </h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
        {links.map((link) => {
          const Icon = link.icon
          return (
            <a
              key={link.href}
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                'flex items-center space-x-2',
                'bg-black/40 border border-white/5 rounded-lg px-4 py-3',
                'hover:border-white/20 transition-colors'
              )}
            >
              <Icon size={14} className={link.iconColor} />
              <div>
                <div className="text-console">{link.title}</div>
                <div className="text-xs text-gray-600">{link.description}</div>
              </div>
            </a>
          )
        })}
      </div>
    </div>
  )
}
