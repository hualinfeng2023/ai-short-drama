import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const apiTarget = loadEnv(mode, '.', '').VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [tailwindcss()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': apiTarget,
        '/health': apiTarget,
        '/meta': apiTarget,
      },
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
    },
  }
})
