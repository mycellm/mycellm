import { useRef, useEffect, useState, useCallback } from 'react'
import { MessageSquare, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { API } from '@/api/endpoints'
import { useChatStore } from '@/stores/chat'
import { useNodeStore } from '@/stores/node'
import { useCreditsStore } from '@/stores/credits'
import { useModels } from '@/hooks/useModels'
import { useSensitiveDetect } from '@/hooks/useSensitiveDetect'
import { ChatMessage as ChatMessageComponent } from './ChatMessage'
import { ChatInput } from './ChatInput'
import { ModelSelector } from './ModelSelector'
import { RoutingOptions } from './RoutingOptions'
import { SensitiveWarning } from './SensitiveWarning'
import type { ChatMessage } from '@/api/types'

const MAX_RETRIES = 5
const RETRY_DELAYS = [2, 4, 8, 15, 30]

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function ChatTab() {
  const { t } = useTranslation('chat')
  const { models } = useModels()
  const detectSensitive = useSensitiveDetect()

  const messages = useChatStore((s) => s.messages)
  const model = useChatStore((s) => s.model)
  const routingOpts = useChatStore((s) => s.routingOpts)
  const sending = useChatStore((s) => s.sending)
  const addMessage = useChatStore((s) => s.addMessage)
  const removeMessages = useChatStore((s) => s.removeMessages)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const setModel = useChatStore((s) => s.setModel)
  const setRoutingOpts = useChatStore((s) => s.setRoutingOpts)
  const setSending = useChatStore((s) => s.setSending)

  const nodeStatus = useNodeStore((s) => s.status)
  const creditBalance = useCreditsStore((s) => s.balance)
  const creditEarned = useCreditsStore((s) => s.earned)
  const creditSpent = useCreditsStore((s) => s.spent)

  const [showRouting, setShowRouting] = useState(false)
  const [sensitiveWarning, setSensitiveWarning] = useState<{
    types: string[]
    pendingText: string
  } | null>(null)

  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Auto-scroll on new messages
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  // Slash command definitions
  const slashCommands: Record<
    string,
    { help: string; fn: () => Promise<string> }
  > = {
    help: {
      help: t('slash.help'),
      fn: async () => {
        const lines = Object.entries(slashCommands)
          .map(([k, v]) => `**/${k}** -- ${v.help}`)
          .join('\n')
        return `### Commands\n${lines}\n\nType normally to chat.`
      },
    },
    status: {
      help: t('slash.status'),
      fn: async () => {
        const d = await api.get<{
          node_name: string
          peer_id: string
          uptime_seconds: number
          mode: string
          models: string[]
          peers: unknown[]
          hardware: { gpu?: string; vram_gb?: number; backend?: string }
        }>(API.node.status)
        const hw = d.hardware || {}
        return [
          `**${d.node_name}** (${d.mode})`,
          `- Peer: \`${(d.peer_id || '').slice(0, 20)}...\``,
          `- Uptime: ${formatUptime(d.uptime_seconds)}`,
          `- Models: ${(d.models || []).length}`,
          `- Peers: ${(d.peers || []).length}`,
          `- Hardware: ${hw.gpu || 'CPU'} (${hw.vram_gb || 0}GB ${hw.backend || 'cpu'})`,
        ].join('\n')
      },
    },
    models: {
      help: t('slash.models'),
      fn: async () => {
        const d = await api.get<{ data: { id: string; owned_by: string }[] }>(API.models.list)
        const m = d.data || []
        if (!m.length) return '*No models loaded.*'
        return (
          `**${m.length} model(s):**\n` +
          m.map((x) => `- \`${x.id}\` (${x.owned_by || 'local'})`).join('\n')
        )
      },
    },
    credits: {
      help: t('slash.credits'),
      fn: async () => {
        const d = await api.get<{ balance: number; earned: number; spent: number }>(
          API.node.credits
        )
        return [
          '**Credits**',
          `- Balance: **${(d.balance || 0).toFixed(2)}**`,
          `- Earned: +${(d.earned || 0).toFixed(2)}`,
          `- Spent: -${(d.spent || 0).toFixed(2)}`,
        ].join('\n')
      },
    },
    fleet: {
      help: t('slash.fleet'),
      fn: async () => {
        const d = await api.get<{
          nodes: {
            node_name: string
            online: boolean
            status: string
            api_addr: string
            capabilities?: { models?: { name: string }[] }
          }[]
        }>(API.admin.nodes)
        const nodes = d.nodes || []
        if (!nodes.length) return '*No fleet nodes registered.*'
        return (
          `**${nodes.length} fleet node(s):**\n` +
          nodes
            .map((n) => {
              const st = n.online ? 'ONLINE' : n.status === 'pending' ? 'PENDING' : 'OFFLINE'
              const mods = (n.capabilities?.models || []).map((m) => m.name || m).join(', ')
              return `- [${st}] **${n.node_name}** \`${n.api_addr}\` ${mods ? '-- ' + mods : ''}`
            })
            .join('\n')
        )
      },
    },
    config: {
      help: t('slash.config'),
      fn: async () => {
        const d = await api.get<Record<string, unknown>>(API.node.config)
        return (
          '**Configuration**\n' +
          Object.entries(d)
            .map(([k, v]) => `- ${k}: \`${v}\``)
            .join('\n')
        )
      },
    },
    clear: {
      help: t('slash.clear'),
      fn: async () => '__clear__',
    },
  }

  const handleSlashCommand = useCallback(
    async (text: string) => {
      const [cmdName] = text.slice(1).split(/\s+/)
      const cmd = slashCommands[cmdName?.toLowerCase()]
      if (!cmd) return false

      // Add user message
      addMessage({
        id: generateId(),
        role: 'user',
        content: text,
        timestamp: Date.now(),
      })

      setSending(true)
      try {
        const result = await cmd.fn()
        if (result === '__clear__') {
          clearMessages()
          setSending(false)
          return true
        }
        addMessage({
          id: generateId(),
          role: 'system',
          content: result,
          timestamp: Date.now(),
        })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Unknown error'
        addMessage({
          id: generateId(),
          role: 'error',
          content: `*Error: ${msg}*`,
          timestamp: Date.now(),
        })
      }
      setSending(false)
      return true
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [addMessage, clearMessages, setSending, nodeStatus, creditBalance, creditEarned, creditSpent, models]
  )

  const sendChatMessage = useCallback(
    async (text: string) => {
      // Add user message
      const userMsg: ChatMessage = {
        id: generateId(),
        role: 'user',
        content: text,
        timestamp: Date.now(),
      }
      addMessage(userMsg)
      setSending(true)

      const controller = new AbortController()
      abortRef.current = controller

      // Build message history from non-system/error messages
      const history = [...useChatStore.getState().messages]
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content }))

      const reqBody = {
        model: model || '',
        messages: history,
        max_tokens: 2048,
        ...(model === '' &&
        (routingOpts.min_tier !== 'any' || routingOpts.required_tags.length > 0)
          ? {
              mycellm: {
                min_tier: routingOpts.min_tier !== 'any' ? routingOpts.min_tier : undefined,
                required_tags:
                  routingOpts.required_tags.length > 0 ? routingOpts.required_tags : undefined,
                routing: routingOpts.routing,
                fallback: routingOpts.fallback,
              },
            }
          : {}),
      }

      for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        try {
          const resp = await fetch(
            `${window.location.origin}${API.chat.completions}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(reqBody),
              signal: controller.signal,
            }
          )

          if (!resp.ok) {
            const status = resp.status
            const body = await resp.text().catch(() => '')

            // Retryable errors
            if ((status === 429 || status === 503) && attempt < MAX_RETRIES) {
              const delay = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)]
              // Show retry indicator
              const retryId = `retry-${Date.now()}`
              addMessage({
                id: retryId,
                role: 'system',
                content: t('retry.retrying', {
                  seconds: delay,
                  attempt: attempt + 1,
                  max: MAX_RETRIES,
                }),
                timestamp: Date.now(),
              })
              await new Promise((r) => setTimeout(r, delay * 1000))
              // Remove retry indicator
              removeMessages((m) => m.id === retryId)
              continue
            }

            // Non-retryable or retries exhausted
            let errContent: string
            if (status === 429) {
              errContent = t('retry.exhausted')
            } else if (status === 503) {
              errContent = t('retry.busy')
            } else {
              errContent = `Error ${status}: ${body}`
            }
            addMessage({
              id: generateId(),
              role: 'error',
              content: errContent,
              timestamp: Date.now(),
            })
            break
          }

          const data = await resp.json()
          const respText = data.choices?.[0]?.message?.content || '[no response]'
          const usage = data.usage || {}
          const routedTo = data.model || 'unknown'

          addMessage({
            id: generateId(),
            role: 'assistant',
            content: respText,
            model: routedTo,
            routed_to: data.routed_to || routedTo,
            tokens: {
              prompt: usage.prompt_tokens || 0,
              completion: usage.completion_tokens || 0,
            },
            timestamp: Date.now(),
          })
          break
        } catch (e: unknown) {
          if (e instanceof DOMException && e.name === 'AbortError') break

          const isNetworkError =
            e instanceof TypeError &&
            (e.message.includes('fetch') || e.message.includes('Failed'))

          if (isNetworkError) {
            addMessage({
              id: generateId(),
              role: 'error',
              content: 'Cannot reach the node. Check that `mycellm serve` is running.',
              timestamp: Date.now(),
            })
          } else {
            const msg = e instanceof Error ? e.message : 'Unknown error'
            addMessage({
              id: generateId(),
              role: 'error',
              content: msg,
              timestamp: Date.now(),
            })
          }
          break
        }
      }

      abortRef.current = null
      setSending(false)
    },
    [model, routingOpts, addMessage, removeMessages, setSending, t]
  )

  const handleSend = useCallback(
    async (text: string) => {
      // Slash commands
      if (text.startsWith('/')) {
        const handled = await handleSlashCommand(text)
        if (handled) return
      }

      // Sensitive content check
      const detected = detectSensitive(text)
      if (detected) {
        setSensitiveWarning({ types: detected.types, pendingText: text })
        return
      }

      await sendChatMessage(text)
    },
    [handleSlashCommand, detectSensitive, sendChatMessage]
  )

  const handleSensitiveConfirm = useCallback(() => {
    if (!sensitiveWarning) return
    const text = sensitiveWarning.pendingText
    setSensitiveWarning(null)
    sendChatMessage(text)
  }, [sensitiveWarning, sendChatMessage])

  const handleSensitiveCancel = useCallback(() => {
    setSensitiveWarning(null)
  }, [])

  const handleAbort = useCallback(() => {
    abortRef.current?.abort()
    setSending(false)
  }, [setSending])

  const handleRetry = useCallback(
    (_content: string) => {
      // Find the last user message before the error and re-send
      const msgs = useChatStore.getState().messages
      const lastUserIdx = msgs.reduce(
        (acc, m, i) => (m.role === 'user' ? i : acc),
        -1
      )
      if (lastUserIdx >= 0) {
        const retryText = msgs[lastUserIdx].content
        // Remove the error and the user message that caused it
        const lastUserMsg = msgs[lastUserIdx]
        removeMessages(
          (m) =>
            m.id === lastUserMsg.id ||
            (msgs.indexOf(m) > lastUserIdx && (m.role === 'error' || m.role === 'system'))
        )
        sendChatMessage(retryText)
      }
    },
    [removeMessages, sendChatMessage]
  )

  return (
    <div
      className={cn(
        'flex flex-col overflow-hidden rounded-xl border border-white/10 bg-[#111]',
        'h-[calc(100vh-220px)]'
      )}
    >
      {/* Header: model selector + routing toggle + clear */}
      <div className="flex h-12 items-center space-x-3 border-b border-white/10 bg-black/50 px-4">
        <MessageSquare size={14} className="flex-shrink-0 text-spore" />
        <ModelSelector models={models} selected={model} onSelect={setModel} />
        <span className="hidden text-xs text-gray-600 sm:inline">
          {models.length} model{models.length !== 1 ? 's' : ''} on network
        </span>
        <RoutingOptions
          options={routingOpts}
          onChange={(opts) => setRoutingOpts(opts)}
          open={showRouting}
          onToggle={() => setShowRouting((r) => !r)}
        />
        <button
          onClick={clearMessages}
          className="ml-auto flex items-center space-x-1 text-xs text-gray-500 transition-colors hover:text-gray-300"
        >
          <Trash2 size={12} />
          <span className="hidden sm:inline">{t('slash.clear')}</span>
        </button>
      </div>

      {/* Routing options panel (rendered by RoutingOptions when open) */}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        className="custom-scrollbar flex-1 space-y-4 overflow-y-auto p-4"
      >
        {messages.length === 0 && (
          <div className="py-12 text-center text-gray-500">
            <MessageSquare size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">Send a message to start a conversation.</p>
            <p className="mt-1 text-xs text-gray-600">
              {model === ''
                ? 'Automatic mode -- routes to the best available model on the network.'
                : `Using ${model}. The network handles routing and failover.`}
            </p>
            {models.length === 0 && (
              <p className="mt-2 text-xs text-compute">
                No models available. Load a model on the Models tab first.
              </p>
            )}
          </div>
        )}

        {messages.map((m) => (
          <ChatMessageComponent
            key={m.id}
            message={m}
            onRetry={m.role === 'error' ? handleRetry : undefined}
          />
        ))}

        {/* Typing indicator */}
        {sending && (
          <div className="flex justify-start">
            <div className="flex items-center space-x-1.5 rounded-xl border border-white/10 bg-black px-4 py-3">
              <span className="h-2 w-2 animate-pulse rounded-full bg-spore" />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-spore"
                style={{ animationDelay: '0.2s' }}
              />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-spore"
                style={{ animationDelay: '0.4s' }}
              />
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        onAbort={handleAbort}
        sending={sending}
        disabled={false}
      />

      {/* Sensitive content warning modal */}
      {sensitiveWarning && (
        <SensitiveWarning
          types={sensitiveWarning.types}
          onCancel={handleSensitiveCancel}
          onConfirm={handleSensitiveConfirm}
        />
      )}
    </div>
  )
}

export default ChatTab
