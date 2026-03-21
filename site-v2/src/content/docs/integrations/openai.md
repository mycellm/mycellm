---
title: "OpenAI SDK"
---


mycellm is a drop-in replacement for the OpenAI API. Any tool or library that uses the OpenAI SDK works without code changes.

## Python

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8420/v1",
    api_key="your-mycellm-api-key",  # or omit if no auth
)

response = client.chat.completions.create(
    model="auto",  # routes to best available
    messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Explain P2P networks"},
    ],
)
print(response.choices[0].message.content)
```

## Streaming

```python
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Write a haiku"}],
    stream=True,
)
for chunk in stream:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
```

## Environment variables

Most tools respect these:

```bash
export OPENAI_BASE_URL=http://localhost:8420/v1
export OPENAI_API_KEY=your-key
```

## LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    openai_api_base="http://localhost:8420/v1",
    model="auto",
)
```

## LlamaIndex

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    api_base="http://localhost:8420/v1",
    model="auto",
)
```
