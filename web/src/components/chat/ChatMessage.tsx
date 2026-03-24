import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarkdownRenderer } from '@/components/common/MarkdownRenderer'
import type { ChatMessage as ChatMessageType } from '@/api/types'

interface ChatMessageProps {
  message: ChatMessageType
  onRetry?: (content: string) => void
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function ChatMessage({ message, onRetry }: ChatMessageProps) {
  const { role, content, model, routed_to, tokens, timestamp } = message

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
        <MarkdownRenderer content={content} className="text-sm text-gray-200" />

        {(model || tokens) && (
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
          </div>
        )}

        {timestamp > 0 && (
          <div className="mt-1 text-xs text-gray-700">{formatTime(timestamp)}</div>
        )}
      </div>
    </div>
  )
}
