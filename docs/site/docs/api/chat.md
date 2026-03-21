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
