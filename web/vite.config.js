import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/mycellm/web',
    emptyOutDir: false,
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8420',
      '/health': 'http://localhost:8420',
    },
  },
})
