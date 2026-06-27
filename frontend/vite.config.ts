import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PACKAGE_DIR = path.resolve(__dirname, '../package')

const MIME: Record<string, string> = {
  '.js':    'application/javascript',
  '.css':   'text/css',
  '.json':  'application/json',
  '.html':  'text/html',
  '.woff':  'font/woff',
  '.woff2': 'font/woff2',
  '.ttf':   'font/ttf',
  '.png':   'image/png',
  '.svg':   'image/svg+xml',
}

export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  plugins: [
    react(),
    {
      // Serves ../package/* at /charting_library/* during dev
      name: 'serve-charting-library',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (!req.url?.startsWith('/charting_library/')) return next()
          const rel = req.url.slice('/charting_library/'.length).split('?')[0]
          const filePath = path.join(PACKAGE_DIR, rel)
          if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
            const ext = path.extname(filePath)
            res.setHeader('Content-Type', MIME[ext] ?? 'application/octet-stream')
            res.setHeader('Cache-Control', 'public, max-age=3600')
            fs.createReadStream(filePath).pipe(res)
          } else {
            next()
          }
        })
      },
    },
  ],
  server: {
    port: 5173,
    open: true,
    watch: {
      usePolling: true,   // Windows host → Linux container không có inotify
      interval: 300,
    },
    proxy: {
      '/api': 'http://server:8000',
      '/ws':  { target: 'ws://server:8000', ws: true },
    },
  },
})
