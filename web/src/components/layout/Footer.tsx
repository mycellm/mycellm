import { useTranslation } from 'react-i18next'
import { useNodeStore } from '@/stores/node'

export function Footer() {
  const { t } = useTranslation('common')
  const versionInfo = useNodeStore((s) => s.versionInfo)

  const version = versionInfo?.current ?? ''
  const updateAvailable = versionInfo?.update_available ?? false
  const latestVersion = versionInfo?.latest

  return (
    <footer className="border-t border-white/10 px-4 py-2 text-xs font-mono">
      <div className="max-w-7xl mx-auto">
        {/* Desktop: single row */}
        <div className="hidden sm:flex items-center justify-between">
          <span className="text-gray-600">{version && `v${version}`}</span>

          <div>
            {updateAvailable && (
              <span className="text-ledger animate-pulse">
                {t('footer.updateAvailable', 'Update available')}
                {latestVersion ? `: v${latestVersion}` : ''}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <a href="/metrics" className="text-gray-600 hover:text-gray-400 transition-colors">/metrics</a>
            <a href="/health" className="text-gray-600 hover:text-gray-400 transition-colors">/health</a>
            <a href="/docs" className="text-gray-600 hover:text-gray-400 transition-colors">/docs</a>
          </div>
        </div>

        {/* Mobile: stacked */}
        <div className="flex sm:hidden flex-col items-center gap-1.5 py-1">
          <span className="text-gray-600">{version && `v${version}`}</span>
          {updateAvailable && (
            <span className="text-ledger animate-pulse">
              {t('footer.updateAvailable', 'Update available')}
              {latestVersion ? `: v${latestVersion}` : ''}
            </span>
          )}
          <div className="flex items-center gap-3">
            <a href="/metrics" className="text-gray-600 hover:text-gray-400 transition-colors">/metrics</a>
            <a href="/health" className="text-gray-600 hover:text-gray-400 transition-colors">/health</a>
            <a href="/docs" className="text-gray-600 hover:text-gray-400 transition-colors">/docs</a>
          </div>
        </div>
      </div>
    </footer>
  )
}
