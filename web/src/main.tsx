import { StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@/i18n'
import './index.css'
import App from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function LoadingFallback() {
  return (
    <div className="min-h-screen bg-void flex items-center justify-center">
      <div className="flex items-center gap-3">
        <div className="w-2 h-2 bg-spore rounded-full animate-ping" />
        <span className="font-mono text-spore text-sm">Loading...</span>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={<LoadingFallback />}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </Suspense>
  </StrictMode>
)
