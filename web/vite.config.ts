import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

// The API runs on http://localhost:5080 (`dotnet run` in server/Api). The production build goes into the API's
// wwwroot, so one process serves both.
export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.API_URL ?? 'http://localhost:5080', changeOrigin: false } },
  },
  build: { outDir: '../server/Api/wwwroot', emptyOutDir: true },
})
