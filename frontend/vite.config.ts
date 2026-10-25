/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, searchForWorkspaceRoot } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const apiTarget = 'http://localhost:8000'
// Contract fixtures live outside frontend/. Only mock mode (VITE_USE_MOCK=1) imports them.
const fixturesDir = fileURLToPath(new URL('../contracts/fixtures', import.meta.url))
// Recorded backend evidence (read-only), e.g. the saved planted-errors eval jobs the API no longer serves.
const evidenceDir = fileURLToPath(new URL('../backend/docs', import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@fixtures': fixturesDir,
      '@evidence': evidenceDir,
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), fixturesDir, evidenceDir] },
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // SSE: never buffer or compress the stream, and keep the socket open.
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.headers.accept?.includes('text/event-stream')) {
              proxyReq.setHeader('Accept-Encoding', 'identity')
            }
          })
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform'
              proxyRes.headers['x-accel-buffering'] = 'no'
              delete proxyRes.headers['content-length']
            }
          })
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
