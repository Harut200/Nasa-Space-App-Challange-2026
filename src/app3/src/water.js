// The water layer draws open water in blue, water under vegetation in green, and two more classes (teal, orange).
// To show only blue or only green, the loaded image is redrawn in the browser keeping the pixels near one legend colour.
// The data files are never touched. Colours are read from the manifest legend, not guessed.
const cache = new Map()
const hex = (h) => { const m = /^#?([0-9a-f]{6})/i.exec(h || ''); if (!m) return null; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255] }
const FALLBACK = { blue: '#2f6fde', green: '#35b04a' }

// Which legend entries are the blue and the green ones (by their words first, then by their colour).
export function waterClasses(layer) {
  const items = layer?.legend?.type === 'classes' ? layer.legend.items : []
  const find = (re, fb) => items.find((it) => re.test(it.label))?.color || fb
  return { blue: find(/^open water/i, FALLBACK.blue), green: find(/under vegetation|flooded veg/i, FALLBACK.green), items }
}
// A thin outline of a mask image (the edge of its opaque area), redrawn in the browser; the data files are never touched.
const oc = new Map()
export function outlineOf(url) {
  if (!oc.has(url)) {
    oc.set(url, fetch(url).then((r) => r.blob()).then(createImageBitmap).then((img) => {
      const w = img.width, h = img.height, c = document.createElement('canvas')
      c.width = w; c.height = h
      const x = c.getContext('2d', { willReadFrequently: true })
      x.drawImage(img, 0, 0)
      const src = x.getImageData(0, 0, w, h).data, out = x.createImageData(w, h), o = out.data
      const on = (i, j) => i >= 0 && j >= 0 && i < w && j < h && src[(j * w + i) * 4 + 3] > 40
      const R = Math.max(1, Math.round(Math.max(w, h) / 600))
      for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
        if (!on(i, j)) continue
        if (!on(i - R, j) || !on(i + R, j) || !on(i, j - R) || !on(i, j + R)) { const k = (j * w + i) * 4; o[k] = 255; o[k + 1] = 255; o[k + 2] = 255; o[k + 3] = 235 }
      }
      x.putImageData(out, 0, 0)
      return new Promise((res) => c.toBlob((b) => res(URL.createObjectURL(b)), 'image/png'))
    }).catch(() => null))
  }
  return oc.get(url)
}
export const hasWaterClasses = (layer) => layer?.id === 'water' && layer?.legend?.type === 'classes'

export function onlyClass(url, color, tol = 70) {
  const key = `${url}|${color}`
  if (!cache.has(key)) {
    const want = hex(color)
    cache.set(key, fetch(url).then((r) => r.blob()).then(createImageBitmap).then((img) => {
      const c = document.createElement('canvas')
      c.width = img.width; c.height = img.height
      const x = c.getContext('2d', { willReadFrequently: true })
      x.drawImage(img, 0, 0)
      const d = x.getImageData(0, 0, c.width, c.height), p = d.data
      for (let i = 0; i < p.length; i += 4) {
        if (p[i + 3] === 0) continue
        const dist = Math.hypot(p[i] - want[0], p[i + 1] - want[1], p[i + 2] - want[2])
        if (dist > tol) p[i + 3] = 0
        else if (dist > tol * 0.65) p[i + 3] = Math.round(p[i + 3] * (tol - dist) / (tol * 0.35)) // soft edge
      }
      x.putImageData(d, 0, 0)
      return new Promise((res) => c.toBlob((b) => res(URL.createObjectURL(b)), 'image/png'))
    }).catch(() => url))
  }
  return cache.get(key)
}
