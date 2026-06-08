# Chat Completions

## `POST /v1/chat/completions`

OpenAI-compatible chat completions with streaming support.

### Request

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "max_tokens": 2048,
  "stream": false
}
```

### mycellm routing (optional)

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "Write code"}],
  "mycellm": {
    "min_tier": "capable",
    "required_tags": ["code"],
    "routing": "quality",
    "fallback": "downgrade"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `min_tier` | string | `tiny`, `fast`, `capable`, `frontier` |
| `required_tags` | string[] | `code`, `reasoning`, `vision` |
| `routing` | string | `best` (quality) or `fastest` (latency) |
| `fallback` | string | `downgrade` or `reject` |

### Vision (multimodal)

Send images alongside text using OpenAI-style content parts. A `user` message's
`content` becomes an array of `text` and `image_url` parts; an image may be a
`data:` URL (base64) or an `https://` URL.

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": [
      {"type": "text", "text": "What's in this image?"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
    ]}
  ]
}
```

Image requests are routed to a **vision-capable** node (e.g. a Qwen2.5-VL or
Gemma 3 vision model); the public gateway will not silently fall back to a
text-only model — if no vision node is available you get a `503` rather than a
hallucinated answer. Nodes that load a vision model advertise the `vision` tag
automatically. Text-only backends flatten image parts out and answer the text.

### Response

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1711234567,
  "model": "Qwen2.5-3B-Instruct-Q8_0",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Hello!"},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 3,
    "total_tokens": 15
  }
}
```

### Streaming

Set `"stream": true`. Response is Server-Sent Events:

```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"Qwen2.5-3B","choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","model":"Qwen2.5-3B","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Reasoning ("thinking") control

Thinking-capable models (Qwen3 hybrid, DeepSeek-R1, GLM-4.x-Thinking,
Gemini Thinking, OpenAI o-series via relay) often emit step-by-step
reasoning before their final answer, traditionally wrapped in
`<think>...</think>` tags. mycellm gives clients three options:

```json
{
  "messages": [...],
  "reasoning": {"exclude": true}   // strip thinking, return clean answer
}
```

```json
{
  "messages": [...],
  "reasoning": {"exclude": false}  // include thinking in response
}
```

Omit `reasoning` to inherit the server-side default. The public bootstrap
demo sets `MYCELLM_HIDE_REASONING_BY_DEFAULT=true` so unauthenticated
visitors see clean answers; self-hosted nodes default to false.

When thinking is included, the assistant message carries it on a
separate `reasoning_content` field — content stays the user-facing
answer:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "The answer is 42.",
      "reasoning_content": "Let me work through this step by step..."
    }
  }]
}
```

Streaming responses emit reasoning and content as separate SSE deltas
so clients can render them in different visual tracks (e.g. an
ephemeral "Thinking…" panel):

```
data: {"choices":[{"delta":{"role":"assistant"}}]}
data: {"choices":[{"delta":{"reasoning_content":"Let me think"}}]}
data: {"choices":[{"delta":{"reasoning_content":" about this..."}}]}
data: {"choices":[{"delta":{"content":"The answer is"}}]}
data: {"choices":[{"delta":{"content":" 42."}}]}
data: [DONE]
```

To find out whether the network has any thinking-capable models loaded
right now, GET `/v1/models/capabilities` — each model entry includes a
`supports_thinking` boolean.

### Tool / function calling

Pass `tools` and (optionally) `tool_choice` exactly as you would to the
OpenAI API. mycellm forwards them to whichever backend ends up serving
the request — local llama.cpp, MLX, or an OpenAI-compatible relay — and
across QUIC peer routing if the answering node is remote.

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "What's the capital of France?"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_country_info",
      "description": "Look up facts about a country.",
      "parameters": {
        "type": "object",
        "properties": {"country": {"type": "string"}},
        "required": ["country"]
      }
    }
  }],
  "tool_choice": "auto"
}
```

Response contains either a normal text message or a `tool_calls` array
in OpenAI format (`function.arguments` is a JSON-encoded string).

Local Qwen-family models sometimes emit tool calls as `<tool_call>` XML
or ```` ```json ```` markdown fences in the content stream. mycellm
recognises both and normalises them into proper `tool_calls` JSON
before returning to the client — so clients always see the standard
OpenAI shape regardless of which model the request landed on.

When exactly one tool is provided and `tool_choice` is unset or
`"auto"`, the relay path coerces `tool_choice` to the named function
to coax JSON-structured output from models that would otherwise emit
inline text.
