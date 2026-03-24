import { useTranslation } from 'react-i18next'
import { Network } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNodeStore } from '@/stores/node'
import { EmptyState } from '@/components/common/EmptyState'

export function QuicPeers() {
  const { t } = useTranslation('network')
  const status = useNodeStore((s) => s.status)
  const peers = status?.peers || []

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
        {t('quic.title', 'QUIC Peers')} ({peers.length})
      </h2>

      {peers.length > 0 ? (
        <div className="space-y-3">
          {peers.map((p, i) => (
            <div
              key={p.peer_id || i}
              className="bg-black border border-white/5 p-4 rounded-lg"
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center space-x-2">
                  <div
                    className={cn(
                      'w-2 h-2 rounded-full',
                      'bg-spore'
                    )}
                  />
                  <span className="font-mono text-sm">
                    {p.peer_id?.slice(0, 24)}...
                  </span>
                </div>
                <span
                  className={cn(
                    'text-xs px-2 py-0.5 rounded font-mono',
                    'bg-spore/10 text-spore'
                  )}
                >
                  {p.transport || 'quic'}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Network}
          message={t('quic.empty', 'No QUIC peers connected yet.')}
        />
      )}
    </div>
  )
}
