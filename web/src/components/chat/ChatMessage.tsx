import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarkdownRenderer } from '@/components/common/MarkdownRenderer'
import { ExecutionPlanCard } from './ExecutionPlanCard'
import type { ChatMessage as ChatMessageType } from '@/api/types'

interface ChatMessageProps {
  message: ChatMessageType
  onRetry?: (content: string) => void
}

function ReasoningPanel({ reasoning }: { reasoning: string }) {
  const [open, setOpen] = useState(false)
  const lineCount = reasoning.split('\n').length
  return (
    <div className="mb-2 rounded-lg border border-poison/20 bg-poison/5">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-xs text-poison hover:bg-poison/10"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Sparkles size={11} />
        <span className="font-medium">Reasoning</span>
        <span className="text-poison/60">({lineCount} lines)</span>
      </button>
      {open && (
        <div className="border-t border-poison/15 px-3 py-2">
          <MarkdownRenderer
            content={reasoning}
            className="text-xs text-gray-400 whitespace-pre-wrap"
          />
        </div>
      )}
    </div>
  )
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function ChatMessage({ message, onRetry }: ChatMessageProps) {
  const { role, content, model, routed_to, tokens, timestamp, reasoning_content, plan,
          served_by, streaming, phase } = message

  // System messages
  if (role === 'system') {
    return (
      <div className="flex justify-center px-4 py-1">
        <div className="max-w-[90%] text-center">
          <MarkdownRenderer
            content={content}
            className="text-sm italic text-gray-500"
          />
          {timestamp > 0 && (
            <span className="text-xs text-gray-700">{formatTime(timestamp)}</span>
          )}
        </div>
      </div>
    )
  }

  // Error messages
  if (role === 'error') {
    return (
      <div className="flex justify-start">
        <div
          className={cn(
            'max-w-[85%] rounded-2xl rounded-bl-md border border-compute/30 bg-compute/5 px-4 py-3',
            'md:max-w-[70%]'
          )}
        >
          <MarkdownRenderer content={content} className="text-sm text-compute" />
          {/* A refused swarm returns the plan with the error. Showing it here
              is the difference between "it failed" and "these two targets were
              blocked by egress policy, for this reason". */}
          {plan && <ExecutionPlanCard plan={plan} />}
          {onRetry && (
            <div className="mt-2">
              <button
                onClick={() => onRetry(content)}
                className={cn(
                  'flex items-center space-x-1 rounded-lg border border-white/10 bg-white/5 px-3 py-1',
                  'text-xs text-gray-400 transition-colors hover:bg-white/10 hover:text-white'
                )}
              >
                <RefreshCw size={10} />
                <span>Retry</span>
              </button>
            </div>
          )}
          {timestamp > 0 && (
            <div className="mt-1.5 text-xs text-gray-700">{formatTime(timestamp)}</div>
          )}
        </div>
      </div>
    )
  }

  // User messages
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            'max-w-[85%] rounded-2xl rounded-br-md border border-white/10 bg-relay/20 px-4 py-3',
            'md:max-w-[70%]'
          )}
        >
          <div className="whitespace-pre-wrap text-sm text-white">{content}</div>
          {timestamp > 0 && (
            <div className="mt-1.5 text-right text-xs text-gray-700">
              {formatTime(timestamp)}
            </div>
          )}
        </div>
      </div>
    )
  }

  // Assistant messages
  return (
    <div className="flex justify-start">
      <div
        className={cn(
          'max-w-[85%] rounded-2xl rounded-bl-md border border-white/10 bg-surface px-4 py-3',
          'md:max-w-[70%]'
        )}
      >
        {reasoning_content && <ReasoningPanel reasoning={reasoning_content} />}

        {/* Live swarm phase. Sits above the answer because it is what the user
            is waiting on: a swarm spends its first seconds fanning out, and
            "Asking 3 models on aurora, hokulea…" is the fact that makes a
            distributed fabric visible instead of a slow spinner. */}
        {phase && (
          <div className="mb-1.5 flex items-center gap-1.5 text-xs text-ledger">
            <Loader2 size={11} className="animate-spin" />
            <span>{phase}</span>
          </div>
        )}

        {/* ⚠️ PLAIN TEXT WHILE STREAMING, MARKDOWN ONCE FINISHED.
            Re-parsing the whole answer on every token is quadratic work on the
            render thread. On iOS that froze the app outright (send dimmed, the
            keyboard went black); in a browser it "only" stutters on long
            answers, which is still a bug — and the fix is the same one. */}
        {streaming ? (
          <div className="whitespace-pre-wrap text-sm text-gray-200">
            {content}
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-spore/70 align-text-bottom" />
          </div>
        ) : (
          <MarkdownRenderer content={content} className="text-sm text-gray-200" />
        )}

        {plan && <ExecutionPlanCard plan={plan} />}

        {!streaming && (model || tokens) && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 border-t border-white/5 pt-2 text-xs text-gray-600">
            {model && routed_to && routed_to !== model && (
              <span>
                via {model} &rarr; {routed_to}
              </span>
            )}
            {model && (!routed_to || routed_to === model) && <span>via {model}</span>}
            {tokens && (
              <span>
                {tokens.prompt}+{tokens.completion} tokens
              </span>
            )}
            {served_by && (
              // Purple: this came off the network. Matches iOS, where the same
              // fact is the same colour.
              <span className="text-poison">node:{served_by}</span>
            )}
          </div>
        )}

        {timestamp > 0 && (
          <div className="mt-1 text-xs text-gray-700">{formatTime(timestamp)}</div>
        )}
      </div>
    </div>
  )
}
