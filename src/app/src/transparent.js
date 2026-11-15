// Thematic layers keep a grey background for "no significant change". Drawn as is, it hides the satellite map, so the
// grey, low-saturation pixels are made transparent in the browser. The data files are not touched.
const cache = new Map()
export function withoutGrey(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then((r) => r.blob()).then(createImageBitmap).then((img) => {
      const c = document.createElement('canvas')
      c.width = img.width; c.height = img.height
      const x = c.getContext('2d', { willReadFrequently: true })
      x.drawImage(img, 0, 0)
      const d = x.getImageData(0, 0, c.width, c.height), p = d.data
      for (let i = 0; i < p.length; i += 4) {
        if (p[i + 3] === 0) continue
        const s = Math.max(p[i], p[i + 1], p[i + 2]) - Math.min(p[i], p[i + 1], p[i + 2])
        if (s < 22) p[i + 3] = 0
        else if (s < 44) p[i + 3] = Math.min(p[i + 3], (s - 22) * 11) // soft edge
      }
      x.putImageData(d, 0, 0)
      return new Promise((res) => c.toBlob((b) => res(URL.createObjectURL(b)), 'image/png'))
    }).catch(() => url))
  }
  return cache.get(url)
}
export const GREY_BACKGROUND = ['choreo', 'sig']
