import { useMemo } from 'react'
import { marked } from 'marked'
// ⚠️ CORE + EXPLICIT LANGUAGES, NOT THE DEFAULT BUILD. `from 'highlight.js'`
// pulls all ~190 grammars and was, on its own, more than half the dashboard's
// JavaScript: the bundle measured 1,477 kB with it and 641 kB without
// (gzip 467 kB → 188 kB). Registering the languages that actually appear in a
// chat about this project costs a few KB and keeps highlighting for all of
// them; anything unregistered renders as plain code rather than failing.
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import go from 'highlight.js/lib/languages/go'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import swift from 'highlight.js/lib/languages/swift'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

for (const [name, lang] of Object.entries({
  bash, css, diff, go, javascript, json, markdown, python,
  rust, sql, swift, typescript, xml, yaml,
})) {
  hljs.registerLanguage(name, lang)
}
import 'highlight.js/styles/github-dark-dimmed.css'
import { cn } from '@/lib/utils'

interface MarkdownRendererProps {
  content: string
  className?: string
}

// Configure marked with highlight.js
marked.setOptions({
  gfm: true,
  breaks: true,
})

const renderer = new marked.Renderer()
renderer.code = function ({ text, lang }: { text: string; lang?: string }) {
  if (lang && hljs.getLanguage(lang)) {
    const highlighted = hljs.highlight(text, { language: lang }).value
    return `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`
  }
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return `<pre><code class="hljs">${escaped}</code></pre>`
}

export function MarkdownRenderer({ content, className }: MarkdownRendererProps) {
  const html = useMemo(() => {
    return marked.parse(content, { renderer }) as string
  }, [content])

  return (
    <div
      className={cn('chat-md', className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
