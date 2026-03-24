import { useMemo } from 'react'
import { marked } from 'marked'
import hljs from 'highlight.js'
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
