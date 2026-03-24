import { useRef, useCallback, useEffect } from 'react'
import { Send, Square } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSend: (text: string) => void
  onAbort: () => void
  sending: boolean
  disabled?: boolean
}

export function ChatInput({ onSend, onAbort, sending, disabled }: ChatInputProps) {
  const { t } = useTranslation('chat')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const valueRef = useRef('')

  const resize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    // Max 4 lines (~96px at 24px line height)
    el.style.height = `${Math.min(el.scrollHeight, 96)}px`
  }, [])

  useEffect(() => {
    resize()
  }, [resize])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = () => {
    const text = valueRef.current.trim()
    if (!text || sending || disabled) return
    onSend(text)
    valueRef.current = ''
    if (textareaRef.current) {
      textareaRef.current.value = ''
    }
    resize()
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    valueRef.current = e.target.value
    resize()
  }

  return (
    <div className="border-t border-white/10 bg-black/50 p-3">
      <div className="flex items-end space-x-2">
        <textarea
          ref={textareaRef}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={t('input.placeholder')}
          rows={1}
          disabled={disabled}
          className={cn(
            'max-h-24 min-h-[40px] flex-1 resize-none rounded-xl border border-white/10 bg-black',
            'px-3.5 py-2.5 text-sm text-white',
            'placeholder:text-gray-600',
            'focus:border-spore/40 focus:outline-none',
            'transition-colors',
            'disabled:opacity-40',
            // Mobile: slightly less padding
            'sm:px-4 sm:py-2.5'
          )}
        />

        {sending ? (
          <button
            onClick={onAbort}
            title={t('input.stop')}
            className={cn(
              'flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl',
              'bg-compute text-white',
              'transition-colors hover:bg-compute/80'
            )}
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={disabled}
            title={t('input.send')}
            className={cn(
              'flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl',
              'bg-spore text-black',
              'transition-colors hover:bg-spore/90',
              'disabled:opacity-40'
            )}
          >
            <Send size={14} />
          </button>
        )}
      </div>
    </div>
  )
}
