import { useTranslation } from 'react-i18next'
import { Boxes } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useServingGroups } from '@/hooks/useServingGroups'
import { EmptyState } from '@/components/common/EmptyState'

/**
 * Serving groups and their deployments.
 *
 * ⚠️ ONLINE AND HEALTHY ARE SHOWN SEPARATELY, ON PURPOSE. A group is *online*
 * when its endpoint answers and *healthy* when it currently owns at least one
 * registered deployment. Those came apart in a real incident: after a restart
 * an auto-loaded model blocked the relay's claim on its own name, so the node
 * kept serving the group while reporting zero deployments. Collapsing the two
 * into one green dot is what made that invisible for as long as it was.
 *
 * A deployment id is shown per model because two groups may serve the same
 * model name — that is the whole reason deployments have ids rather than
 * being keyed by model.
 */
export function ServingGroups() {
  const { t } = useTranslation('network')
  const { groups, healthyCount, deploymentCount } = useServingGroups()

  return (
    <div className="rounded-xl border border-white/10 bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-baseline gap-x-3">
        <h2 className="font-mono text-xs uppercase tracking-widest text-gray-500">
          {t('groups.title', 'Serving Groups')} ({groups.length})
        </h2>
        {groups.length > 0 && (
          <span className="font-mono text-xs text-gray-600">
            {t('groups.summary', {
              healthy: healthyCount,
              deployments: deploymentCount,
              defaultValue: '{{healthy}} healthy · {{deployments}} deployments',
            })}
          </span>
        )}
      </div>

      {groups.length > 0 ? (
        <div className="space-y-3">
          {groups.map((g) => (
            <div
              key={g.group_id}
              className="rounded-lg border border-white/5 bg-black p-4"
            >
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center space-x-2">
                  <div
                    className={cn(
                      'h-2 w-2 shrink-0 rounded-full',
                      g.healthy ? 'bg-spore' : g.online ? 'bg-ledger' : 'bg-compute'
                    )}
                  />
                  <span className="truncate font-mono text-sm">{g.name}</span>
                  <span className="truncate font-mono text-xs text-gray-600">
                    {g.endpoint}
                  </span>
                </div>
                <span
                  className={cn(
                    'shrink-0 rounded px-2 py-0.5 font-mono text-xs',
                    g.healthy
                      ? 'bg-spore/10 text-spore'
                      : g.online
                        ? 'bg-ledger/10 text-ledger'
                        : 'bg-compute/10 text-compute'
                  )}
                >
                  {g.healthy
                    ? t('groups.healthy', 'healthy')
                    : g.online
                      ? t('groups.noDeployments', 'online · 0 deployments')
                      : t('groups.offline', 'offline')}
                </span>
              </div>

              {g.error && (
                <div className="mb-2 font-mono text-xs text-compute">{g.error}</div>
              )}

              {g.deployments.length > 0 ? (
                <ul className="space-y-0.5 font-mono text-xs text-gray-400">
                  {g.deployments.map((d) => (
                    <li key={d.deployment_id} className="flex gap-2">
                      <span className="truncate">{d.model}</span>
                      {/* Upstream name shown only when it differs — a relay
                          registers `relay:<id>` locally, and the two names are
                          what makes "which one did I route to" answerable. */}
                      {d.upstream_model && d.upstream_model !== d.model && (
                        <span className="truncate text-gray-600">→ {d.upstream_model}</span>
                      )}
                      <span className="ml-auto truncate text-gray-700">
                        {d.deployment_id}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="font-mono text-xs text-gray-600">
                  {t('groups.empty', 'no deployments registered')}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Boxes}
          message={t(
            'groups.none',
            'No serving groups. Add an OpenAI-compatible endpoint from the Models tab.'
          )}
        />
      )}
    </div>
  )
}
