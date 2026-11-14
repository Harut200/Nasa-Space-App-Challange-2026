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
  los_cm: 'Ground motion',
  los_asc_cm: 'Ground motion, ascending',
  los_desc_cm: 'Ground motion, descending',
  fringes: 'Fringes',
  east_cm: 'East-west, approximate',
  up_cm: 'Vertical, approximate',
  coherence: 'Coherence',
}
export const shortLabel = (l) => SHORT[l.id] || l.label

export function splitLayers(layers, event) {
  if (event) return { main: layers, more: [] }
  const first = PRIMARY.map((id) => layers.find((l) => l.id === id)).filter(Boolean)
  return { main: first, more: layers.filter((l) => !PRIMARY.includes(l.id)) }
}
export const isEvent = (m) => m?.kind === 'event' && !!m.event
export const defaultLayer = (m) => (isEvent(m) ? (m.layers.find((l) => l.id === 'fringes') || m.layers[0]) : (m.layers.find((l) => l.id === 'water') || m.layers[0])).id

// Peak open-water area over the season, from the timeseries facts.
export function peakWater(ts) {
  const f = ts?.facts?.find((x) => x.label === 'Open water')
  if (!f) return null
  let best = null
  f.values.forEach((v, i) => { if (v != null && (best == null || v > best.v)) best = { v, date: ts.dates[i], unit: f.unit } })
  return best
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const two = (n) => String(n).padStart(2, '0')
export const shortDate = (d) => { const x = new Date(d); return `${x.getUTCDate()} ${MON[x.getUTCMonth()]} ${x.getUTCFullYear()}` }
export const dayMon = (d) => { const x = new Date(d); return `${x.getUTCDate()} ${MON[x.getUTCMonth()]}` }
export const utcStamp = (d) => { const x = new Date(d); return `${shortDate(d)}, ${two(x.getUTCHours())}:${two(x.getUTCMinutes())} UTC` }

// A place as the globe and the lists see it: a season of passes or an earthquake.
export const centre = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
export const makePlace = (m, ts) => ({ id: m.id, name: m.name, lngLat: centre(m.bounds), peak: peakWater(ts), kind: isEvent(m) ? 'event' : 'season', event: isEvent(m) ? m.event : null })
export const magnitude = (e) => (Number.isFinite(e?.magnitude) ? e.magnitude.toFixed(1) : null)
const cmOf = (x) => (Number.isFinite(x) ? km(x) : null)
// Tooltip / summary text for an earthquake. The motion clause is left out when the first view has no number.
export function eventTip(p) {
  const e = p.event, mag = magnitude(e)
  const head = `${p.name.split(',')[0]}${mag ? `, M${mag}` : ''}`
  const cm = cmOf(e.views?.[0]?.p2p_cm)
  const rest = [e.time ? shortDate(e.time) : null, cm ? `ground moved ≈ ${cm} cm along the radar's line of sight` : null].filter(Boolean).join(' · ')
  return { head, rest }
}
export const eventSummary = (p) => [magnitude(p.event) && `M${magnitude(p.event)}`, p.event.time && shortDate(p.event.time)].filter(Boolean).join(' · ')
export const placeSummary = (p) => (p.kind === 'event' ? eventSummary(p) : p.peak?.v >= 1 ? `${km(p.peak.v)} km² peak water` : 'no open water mapped')
export const groupPlaces = (places) => [
  { key: 'season', title: 'A season of water', items: places.filter((p) => p.kind !== 'event') },
  { key: 'event', title: 'Earthquakes', items: places.filter((p) => p.kind === 'event') },
].filter((g) => g.items.length)
export const countPlaces = (places) => ({ seasons: places.filter((p) => p.kind !== 'event').length, quakes: places.filter((p) => p.kind === 'event').length })
export function landingText(places) {
  const { seasons, quakes } = countPlaces(places)
  const s = `${seasons} place${seasons === 1 ? '' : 's'} through a season`
  return `NISAR watched ${quakes ? `${s}, and ${quakes} earthquake${quakes === 1 ? '' : 's'}` : s} between June and October 2026.`
}
// What the two passes and the earthquake look like on the bar: "19 Oct → ⚡ 22 Oct → 31 Oct".
export const eventViews = (e) => (e?.views || []).filter((v) => v.before && v.after)
export const viewMotion = (v) => (Number.isFinite(v.p2p_cm) ? `≈ ${km(v.p2p_cm)} cm` : null)
export const viewControl = (v) => (Number.isFinite(v.control_p2p_cm) ? `≈ ${km(v.control_p2p_cm)} cm` : null)
export function viewingNote(e) {
  const vs = e?.views || [], dirs = new Set(vs.map((v) => v.label))
  const how = vs.length < 2 ? 'one viewing direction' : dirs.size > 1 ? 'two viewing directions' : `${vs.length} tracks, all ${[...dirs][0]}`
  return `Provisional data, ${how}: this is motion along the radar's line of sight, not up/down or sideways.`
}

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
export const DOT_COLOR = { big: '#4aa8ff', some: '#35c7b8', land: '#f2b84b', quake: '#ff4f8b' }
export const dotRadius = (peak) => 4 + 2.4 * Math.log10(1 + (peak?.v || 0))
// Earthquake dots grow with magnitude (M6.8 is about 9 px, M7.7 about 11 px).
export const quakeRadius = (mag) => 5 + 2.2 * Math.max(0, (Number.isFinite(mag) ? mag : 6) - 5)

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
