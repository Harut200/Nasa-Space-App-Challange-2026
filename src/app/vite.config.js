import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Data is read in place from ../app/public/data through the symlink public/data.
export default defineConfig({
  base: '/Nasa-Space-App-Challange-2026/',
  plugins: [react()],
})
