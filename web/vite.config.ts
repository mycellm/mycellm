import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../src/mycellm/web',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1000,
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8420',
      '/health': 'http://localhost:8420',
      '/metrics': 'http://localhost:8420',
    },
  },
})
