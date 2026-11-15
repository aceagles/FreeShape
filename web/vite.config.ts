import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// `npm run build` writes the client where `python -m freeshape serve` hosts it (git-ignored).
// serve never writes there; it lists the folder and serves the files it finds.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../freeshape/static/app', emptyOutDir: true },
  // `npm run dev`: the page is served by Vite, the API by `freeshape serve` (default port 8765).
  server: { proxy: { '/health': 'http://127.0.0.1:8765' } },
  preview: { port: 4173, strictPort: true },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
})
