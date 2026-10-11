import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5180,
    strictPort: true,
    // Forward API + WebRTC signaling to the ZENO server during development.
    proxy: { '/api': 'http://localhost:7860' },
  },
})
