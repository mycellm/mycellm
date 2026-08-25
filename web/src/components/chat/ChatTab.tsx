import { useRef, useEffect, useState, useCallback } from 'react'
import { MessageSquare, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { parseSelection } from '@/lib/selection'
import { StreamHttpError } from '@/api/client'
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
import { RoutingOptions, RoutingOptionsPanel } from './RoutingOptions'
import { SensitiveWarning } from './SensitiveWarning'
import type { ChatMessage, ExecutionMeta, SwarmProgress } from '@/api/types'
import { SWARM_MODEL } from '@/api/types'

const MAX_RETRIES = 5
const RETRY_DELAYS = [2, 4, 8, 15, 30]

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

// setTimeout wrapped so an aborted signal rejects immediately instead of
// waiting out the retry delay -- lets handleAbort cut a backoff short.
function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

/**
 * Turn a swarm progress frame into one short line.
 *
 * Node names, not counts alone: "3 proposers on aurora, hokulea and an iPhone"
 * is the fact that makes a distributed fabric visible. Counts alone read like
 * a generic spinner.
 */
function describePhase(p: SwarmProgress): string {
  if (p.phase === 'proposing') {
    const n = p.planned ?? p.targets?.length ?? 0
    const where = p.targets?.length
      ? ` on ${[...new Set(p.targets.map(shortTarget))].join(', ')}`
      : ''
    return `Asking ${n} model${n === 1 ? '' : 's'}${where}…`
  }
  const from = p.from_proposals ?? 0
  const on = p.target ? ` on ${shortTarget(p.target)}` : ''
  return `Synthesising ${from} answer${from === 1 ? '' : 's'}${on}…`
}

/**
 * A target string rendered as something a person recognises.
 *
 * ⚠️ NEVER RETURN THE PLACEHOLDER. A group whose id isn't known prints as
 * `group:external:<model>`, and naively taking the second segment showed
 * "Asking 3 models on external" — three different models collapsed into one
 * meaningless word. When the group has no id the MODEL is the identifying
 * fact, so fall through to it.
 *
 *   local:qwen3-9b            → this node
 *   peer:1a2b3c4d:qwen3-9b    → 1a2b3c4d
 *   group:abc123:qwen3-9b     → abc123
 *   group:external:qwen3-9b   → qwen3-9b
 */
function shortTarget(target: string): string {
  const parts = target.split(':')
  const [kind, second, ...rest] = parts
  if (kind === 'local') return 'this node'
  const model = rest.join(':') || second || target
  if (kind === 'group' && (!second || second === 'external')) return model
  return second ? second.slice(0, 8) : model
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

      // The `mycellm` block carries two different kinds of option, and they
      // apply under different conditions:
      //   - resolution constraints (min_tier, tags, fallback) only mean
      //     something when the node is choosing the model, i.e. model === ''
      //   - execution options (trust, fanout, token_budget) apply to whatever
      //     was chosen, including an explicitly-selected swarm
      // `routing` is deliberately never sent: only "best" is implemented, the
      // node rejects anything else with HTTP 400, and "best" is already the
      // server-side default — so sending it adds a way to fail and nothing else.
      // The selector's value carries BOTH the model and any tier floor, so the
      // request can no longer express "this exact model, but at least that
      // tier" — a contradiction the UI used to allow and the code then had to
      // quietly drop. See `lib/selection.ts`.
      const { model: wireModel, minTier } = parseSelection(model)
      const isSwarm = wireModel === SWARM_MODEL
      const resolving = wireModel === ''
      const constraints =
        resolving && (minTier !== '' || routingOpts.required_tags.length > 0)
          ? {
              min_tier: minTier || undefined,
              required_tags:
                routingOpts.required_tags.length > 0 ? routingOpts.required_tags : undefined,
              fallback: routingOpts.fallback,
            }
          : {}
      const execution = {
        trust: routingOpts.trust || undefined,
        fanout: isSwarm && routingOpts.fanout > 0 ? routingOpts.fanout : undefined,
        token_budget: routingOpts.token_budget > 0 ? routingOpts.token_budget : undefined,
      }
      const mycellm = { ...constraints, ...execution }
      const hasOptions = Object.values(mycellm).some((v) => v !== undefined)

      const reqBody = {
        stream: true,
        model: wireModel,
        messages: history,
        max_tokens: 2048,
        // OpenAI-o-series style reasoning control. Toggle off → server strips
        // <think> blocks and asks Qwen3 templates to suppress thinking. Toggle
        // on → server returns reasoning_content for UI to render.
        reasoning: { exclude: !routingOpts.show_reasoning },
        ...(hasOptions ? { mycellm } : {}),
      }

      for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        // The assistant bubble is created BEFORE the first token so the phase
        // line has somewhere to live: a swarm spends its first seconds fanning
        // out, and "3 proposers on 2 nodes" is exactly what the user wants
        // during that wait rather than an undifferentiated spinner.
        const responseId = generateId()
        let opened = false
        let text = ''

        const open = () => {
          if (opened) return
          opened = true
          addMessage({
            id: responseId,
            role: 'assistant',
            content: '',
            streaming: true,
            timestamp: Date.now(),
          })
        }
        const patch = (fn: (m: ChatMessage) => ChatMessage) =>
          useChatStore.getState().updateMessage(responseId, fn)

        try {
          const frames = api.postStream(API.chat.completions, reqBody, {
            signal: controller.signal,
          })

          let usage: { prompt_tokens?: number; completion_tokens?: number } = {}
          let routedTo = ''
          let servedBy = ''
          let plan: ChatMessage['plan']

          for await (const raw of frames) {
            if (raw === '[DONE]') break
            let data: Record<string, unknown>
            try {
              data = JSON.parse(raw)
            } catch {
              continue // a keepalive or a frame we don't understand
            }

            const meta = data.mycellm as (SwarmProgress & ExecutionMeta) | undefined

            // Progress frames carry an empty delta by contract. Rendering the
            // phase here is the whole reason the server distinguishes them
            // from content — see api/openai.py::_stream_swarm.
            if (meta?.type === 'progress') {
              open()
              patch((m) => ({ ...m, phase: describePhase(meta) }))
              continue
            }
            if (meta) plan = meta as ExecutionMeta

            const choice = (data.choices as Record<string, unknown>[] | undefined)?.[0]
            const delta = (choice?.delta ?? {}) as Record<string, string>
            if (data.model) routedTo = String(data.model)
            if (data.usage) usage = data.usage as typeof usage
            if (meta?.node || meta?.served_by) servedBy = String(meta.node ?? meta.served_by)

            if (delta.content) {
              open()
              text += delta.content
              // ⚠️ PLAIN TEXT WHILE STREAMING, MARKDOWN ONLY AT THE END.
              // Re-parsing the whole answer on every token is quadratic work
              // on the render thread; on iOS that froze the app outright, and
              // the same shape here just makes long answers stutter.
              patch((m) => ({ ...m, content: text, phase: undefined }))
            }
            if (delta.reasoning_content) {
              open()
              patch((m) => ({
                ...m,
                reasoning_content: (m.reasoning_content ?? '') + delta.reasoning_content,
              }))
            }
          }

          if (!opened) {
            // A clean empty stream and a successful empty answer look
            // identical on the wire; say which one this was.
            addMessage({
              id: generateId(),
              role: 'error',
              content: t('errors.emptyStream'),
              timestamp: Date.now(),
            })
            break
          }

          patch((m) => ({
            ...m,
            streaming: false,
            phase: undefined,
            model: routedTo || undefined,
            routed_to: routedTo || undefined,
            served_by: servedBy || undefined,
            plan,
            tokens:
              usage.prompt_tokens || usage.completion_tokens
                ? {
                    prompt: usage.prompt_tokens ?? 0,
                    completion: usage.completion_tokens ?? 0,
                  }
                : undefined,
          }))
          break
        } catch (e: unknown) {
          if (e instanceof DOMException && e.name === 'AbortError') {
            // Stop cleanly on the partial answer rather than deleting it —
            // the tokens already on screen were real.
            if (opened) patch((m) => ({ ...m, streaming: false, phase: undefined }))
            break
          }

          // A failed request must not leave a half-open bubble behind.
          if (opened) removeMessages((m) => m.id === responseId)

          // ⚠️ RETRIES ARE ONLY SAFE BEFORE THE FIRST TOKEN. Re-sending after
          // partial output would render the answer twice; `opened` is the
          // commit point, exactly as on the node's own failover path.
          if (e instanceof StreamHttpError) {
            const retryable = e.status === 429 || e.status === 503
            if (retryable && !opened && attempt < MAX_RETRIES) {
              const delay = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)]
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
              try {
                await abortableSleep(delay * 1000, controller.signal)
              } finally {
                removeMessages((m) => m.id === retryId)
              }
              continue
            }

            let errContent: string
            let errPlan: ChatMessage['plan']
            if (e.status === 429) {
              errContent = t('retry.exhausted')
            } else if (e.status === 503) {
              errContent = t('retry.busy')
            } else {
              // A swarm refusal (422) carries the plan that produced it. The
              // refusals are the whole point: a target blocked by egress
              // policy is otherwise indistinguishable from one never there.
              try {
                const parsed = JSON.parse(e.body)
                errContent = parsed?.error?.message || `Error ${e.status}: ${e.body}`
                errPlan = parsed?.error?.plan
              } catch {
                errContent = `Error ${e.status}: ${e.body}`
              }
            }
            addMessage({
              id: generateId(),
              role: 'error',
              content: errContent,
              plan: errPlan,
              timestamp: Date.now(),
            })
            break
          }

          const isNetworkError =
            e instanceof TypeError &&
            (e.message.includes('fetch') || e.message.includes('Failed'))
          addMessage({
            id: generateId(),
            role: 'error',
            content: isNetworkError
              ? 'Cannot reach the node. Check that `mycellm serve` is running.'
              : e instanceof Error
                ? e.message
                : 'Unknown error',
            timestamp: Date.now(),
          })
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

      {/* Routing options panel — below the toolbar, never inside it. */}
      <RoutingOptionsPanel
        options={routingOpts}
        onChange={(opts) => setRoutingOpts(opts)}
        open={showRouting}
        swarm={model === SWARM_MODEL}
      />

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
