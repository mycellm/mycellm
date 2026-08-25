import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, ListTodo, RefreshCw, Send, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { useModels } from '@/hooks/useModels'
import { ModelSelector } from '@/components/chat/ModelSelector'
import { parseSelection } from '@/lib/selection'
import type { QueuedJob, QueueListResponse } from '@/api/types'

/**
 * The queue view.
 *
 * ⚠️ THE WAITING REASON IS THE MOST IMPORTANT COLUMN ON THIS SCREEN, not the
 * state badge. A job sitting in `queued` tells the user nothing they did not
 * already know from the fact that no answer arrived; "no frontier-tier model
 * is reachable — 3 smaller models online" tells them to go wake the Mac. A
 * queue that cannot explain itself is indistinguishable from a hang, and that
 * mistake has already cost this project one long debugging session.
 */

const STATE_STYLE: Record<string, string> = {
  queued: 'border-ledger/30 bg-ledger/10 text-ledger',
  running: 'border-spore/30 bg-spore/10 text-spore',
  done: 'border-spore/30 bg-spore/5 text-spore',
  failed: 'border-compute/30 bg-compute/10 text-compute',
  expired: 'border-white/10 bg-white/5 text-gray-500',
  cancelled: 'border-white/10 bg-white/5 text-gray-500',
}

function relative(ts: number): string {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

function firstPrompt(job: QueuedJob): string {
  const msg = [...(job.messages || [])].reverse().find((m) => m.role === 'user')
  const content = msg?.content
  return typeof content === 'string' ? content : ''
}

export function QueueTab() {
  const { t } = useTranslation('queue')
  const { models } = useModels()

  const [data, setData] = useState<QueueListResponse | null>(null)
  const [error, setError] = useState('')
  const [prompt, setPrompt] = useState('')
  const [selection, setSelection] = useState('')
  const [stake, setStake] = useState(0)
  const [submitting, setSubmitting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setData(await api.get<QueueListResponse>(API.jobs.list))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
    // 4s: fast enough that a job starting feels immediate, slow enough that a
    // dashboard left open overnight is not a background load of its own.
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [refresh])

  const submit = async () => {
    if (!prompt.trim() || submitting) return
    setSubmitting(true)
    try {
      const { model, minTier } = parseSelection(selection)
      await api.post(API.jobs.submit, {
        messages: [{ role: 'user', content: prompt.trim() }],
        // Sent as one or the other, never both — the endpoint rejects the
        // contradiction, and `parseSelection` makes it unrepresentable here.
        ...(model ? { model } : {}),
        ...(minTier ? { min_tier: minTier } : {}),
        ...(stake > 0 ? { stake } : {}),
      })
      setPrompt('')
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = async (id: string) => {
    try {
      await api.delete(API.jobs.cancel(id))
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const jobs = data?.jobs || []
  const counts = data?.counts || {}
  const schedulerReason = data?.scheduler?.reason || ''

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ListTodo size={18} className="text-spore" />
          <h2 className="text-lg font-medium text-white">{t('title')}</h2>
        </div>
        <button
          onClick={refresh}
          className="rounded-lg border border-white/10 p-1.5 text-gray-500 hover:text-white"
          title={t('refresh')}
        >
          <RefreshCw size={14} />
        </button>
      </div>

      <p className="text-sm text-gray-500">{t('blurb')}</p>

      {/* Why the queue is or is not moving. Device-level, so it is true even
          when every job looks individually fine. */}
      {schedulerReason && (
        <div className="flex items-center gap-2 rounded-lg border border-ledger/20 bg-ledger/5 px-3 py-2 text-xs text-ledger">
          <Clock size={12} />
          <span>{schedulerReason}</span>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-compute/30 bg-compute/5 px-3 py-2 text-sm text-compute">
          {error}
        </div>
      )}

      {/* Submit */}
      <div className="space-y-2 rounded-xl border border-white/10 bg-surface p-3">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder={t('promptPlaceholder')}
          className={cn(
            'w-full resize-none rounded-lg border border-white/10 bg-black px-3 py-2',
            'text-sm text-white outline-none focus:border-spore/40'
          )}
        />
        <div className="flex flex-wrap items-center gap-2">
          <ModelSelector models={models} selected={selection} onSelect={setSelection} />
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            {t('stake')}
            <input
              type="number"
              min={0}
              value={stake}
              onChange={(e) => setStake(Math.max(0, Number(e.target.value) || 0))}
              className="w-20 rounded-lg border border-white/10 bg-black px-2 py-1.5 font-mono text-sm text-white outline-none focus:border-spore/40"
            />
          </label>
          <button
            onClick={submit}
            disabled={!prompt.trim() || submitting}
            className={cn(
              'ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
              prompt.trim() && !submitting
                ? 'bg-spore text-black hover:bg-spore/90'
                : 'cursor-not-allowed bg-white/5 text-gray-600'
            )}
          >
            <Send size={13} />
            {t('queueIt')}
          </button>
        </div>
        {/* Credits buy position, never a place in front of the owner's own
            work — say so here rather than letting someone discover it by
            staking and not moving. */}
        <p className="text-xs text-gray-600">{t('stakeHint')}</p>
      </div>

      {/* Counts */}
      <div className="flex flex-wrap gap-2 text-xs">
        {Object.entries(counts).map(([state, n]) => (
          <span
            key={state}
            className={cn('rounded-md border px-2 py-1', STATE_STYLE[state] || 'border-white/10 text-gray-500')}
          >
            {n} {t(`states.${state}`, state)}
          </span>
        ))}
      </div>

      {/* Jobs */}
      <div className="space-y-2">
        {jobs.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-surface p-6 text-center text-sm text-gray-600">
            {t('empty')}
          </div>
        )}
        {jobs.map((job) => (
          <div key={job.job_id} className="rounded-xl border border-white/10 bg-surface p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={cn(
                      'rounded-md border px-1.5 py-0.5 text-xs font-medium',
                      STATE_STYLE[job.state] || 'border-white/10 text-gray-500'
                    )}
                  >
                    {t(`states.${job.state}`, job.state)}
                  </span>
                  {job.min_tier && (
                    <span className="text-xs text-gray-500">≥ {job.min_tier}</span>
                  )}
                  {job.model && <span className="font-mono text-xs text-gray-500">{job.model}</span>}
                  {job.stake > 0 && (
                    <span className="text-xs text-ledger">{job.stake} staked</span>
                  )}
                  <span className="text-xs text-gray-700">{relative(job.created_at)} ago</span>
                </div>
                <p className="mt-1.5 truncate text-sm text-gray-300">{firstPrompt(job)}</p>

                {/* The column that matters. */}
                {job.waiting_reason && job.state === 'queued' && (
                  <p className="mt-1 text-xs text-ledger">{job.waiting_reason}</p>
                )}
                {job.error && <p className="mt-1 text-xs text-compute">{job.error}</p>}
                {job.state === 'done' && (
                  <>
                    <p className="mt-1.5 whitespace-pre-wrap text-sm text-gray-200">
                      {job.result_text}
                    </p>
                    {(job.served_by || job.served_model) && (
                      // Same attribution as a chat reply, for the same reason:
                      // a job may run hours later on a different machine than
                      // the one that would have taken it at submit time.
                      <p className="mt-1 text-xs text-poison">
                        {job.served_model}
                        {job.served_by ? ` · node:${job.served_by}` : ''}
                      </p>
                    )}
                  </>
                )}
              </div>

              {(job.state === 'queued' || job.state === 'running') && (
                <button
                  onClick={() => cancel(job.job_id)}
                  className="rounded-lg border border-white/10 p-1.5 text-gray-600 hover:text-compute"
                  title={t('cancel')}
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
