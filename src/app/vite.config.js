import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  base: '/Nasa-Space-App-Challange-2026/', // repository name: GitHub Pages serves from this path
  plugins: [react()],
  build: {
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: 'maplibre', test: /node_modules\/maplibre-gl/ }] } } }, // own long-cached chunk
  },
})
