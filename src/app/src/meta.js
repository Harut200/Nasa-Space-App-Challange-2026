import { useEffect, useRef, useState } from 'react'
// Presentation helpers. Every number comes from manifests/timeseries; this file only orders and renames.
export const DATA = import.meta.env.BASE_URL + 'data/'
export const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'

export const getJson = (path) => fetch(DATA + path).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${path}: ${r.status}`))))
export const words = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
export const dayMonth = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
export const km = (v) => Math.round(v).toLocaleString('en')

// The clearest layers come first; the rest sit under "More layers". The full label is still shown above the legend.
export const PRIMARY = ['water', 'when', 'change', 'hh']
export const SHORT = {
  water: 'Water and flooded vegetation',
  when: 'When it changed',
  change: 'Change since first pass',
  hh: 'Radar image (HH)',
  hv: 'Radar image (HV)',
  classes: 'Season classes',
  sig: 'Significant change',
  arrival: 'Day the water arrived',
  choreo: 'Choreography',
  rgb: 'Three-date composite',
}
export const shortLabel = (l) => SHORT[l.id] || l.label

export function splitLayers(layers) {
  const first = PRIMARY.map((id) => layers.find((l) => l.id === id)).filter(Boolean)
  return { main: first, more: layers.filter((l) => !PRIMARY.includes(l.id)) }
}
export const defaultLayer = (m) => (m.layers.find((l) => l.id === 'water') || m.layers[0]).id

// Peak open-water area over the season, from the timeseries facts.
export function peakWater(ts) {
  const f = ts?.facts?.find((x) => x.label === 'Open water')
  if (!f) return null
  let best = null
  f.values.forEach((v, i) => { if (v != null && (best == null || v > best.v)) best = { v, date: ts.dates[i], unit: f.unit } })
  return best
}

export const centre = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]

export function distanceKm([lng1, lat1], [lng2, lat2]) {
  const r = Math.PI / 180, a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lng2 - lng1) * r) / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(a))
}
export const nearestPlace = (places, pos) => places.reduce((a, b) => (distanceKm(pos, b.lngLat) < distanceKm(pos, a.lngLat) ? b : a))

// Dot class on the globe, from the peak open-water area.
export function dotKind(peak) {
  if (peak == null || peak.v < 1) return 'land'
  return peak.v >= 1000 ? 'big' : 'some'
}
export const DOT_COLOR = { big: '#4aa8ff', some: '#35c7b8', land: '#f2b84b' }
export const dotRadius = (peak) => 4 + 2.4 * Math.log10(1 + (peak?.v || 0))

// Link state. Format: #/<place>?layer=<id>&date=<YYYY-MM-DD>; theme is a normal query parameter (?theme=a|b|c).
export function parseHash(h = location.hash) {
  const [path, query = ''] = h.replace(/^#\/?/, '').split('?')
  const q = new URLSearchParams(query)
  return { id: path || null, layer: q.get('layer'), date: q.get('date'), about: q.has('about'), compare: q.has('compare'), split: q.has('split') ? Math.min(98, Math.max(2, +q.get('split') || 50)) : 50 }
}
export const THEMES = [['earth', 'Earth'], ['arameh', 'Dark serif'], ['mission', 'Mission control']]
export const basemapMode = (t) => (t === 'earth' ? 'natural' : 'dark')
export function currentTheme() {
  const t = new URLSearchParams(location.search).get('theme')
  return THEMES.some(([k]) => k === t) ? t : 'earth'
}
export function applyTheme(t) {
  document.documentElement.dataset.theme = t
  const u = new URL(location.href)
  if (t === 'earth') u.searchParams.delete('theme'); else u.searchParams.set('theme', t)
  history.replaceState(null, '', u)
}

// Smoothly animate a number towards a new value (the headline follows the date).
export function useTween(target, ms = 700) {
  const [v, setV] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target == null || matchMedia('(prefers-reduced-motion: reduce)').matches) { from.current = target; return }
    const a = from.current ?? target, t0 = performance.now()
    let raf
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - (1 - k) ** 3
      const x = a + (target - a) * e
      from.current = x
      setV(x)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return target == null ? target : v
}

export const coords = ([w, s, e, n]) => [[w, n], [e, n], [e, s], [w, s]]
