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
