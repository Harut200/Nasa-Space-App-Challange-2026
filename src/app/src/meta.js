import { useEffect, useRef, useState } from 'react'
import { nearest } from './data.js'
// Presentation helpers. Every number comes from manifests/timeseries; this file only orders and renames.
export const DATA = import.meta.env.BASE_URL + 'data/'
export const BRAND = import.meta.env.BASE_URL + 'brand/'
export const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'

export const getJson = (path) => fetch(DATA + path).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${path}: ${r.status}`))))
export const words = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
export const dayMonth = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
export const km = (v) => Math.round(v).toLocaleString('en')

// The clearest layers come first; the rest sit under "More layers". The full label is still shown above the legend.
export const PRIMARY = ['water', 'when', 'change', 'hh']
const PRIMARY_COH = ['coh', 'coh_change']
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
  coh: 'Coherence',
  coh_loss: 'Where it changed completely',
  ev_before: 'Before',
  ev_after: 'After',
  ev_diff: 'Echo change',
  ev_mask: 'Changed patch',
  burn_before: 'Before the fire',
  burn_after: 'After the fire',
  burn_diff: 'Echo change after the fire',
  burn_scar: 'Changed patch',
}
// A layer id without a short name falls back to the manifest label, cut at a word boundary when it is long.
export function shortLabel(l) {
  if (SHORT[l.id]) return SHORT[l.id]
  const t = String(l.label ?? l.id), max = 30
  if (t.length <= max) return t
  const cut = t.slice(0, max), sp = cut.lastIndexOf(' ')
  return `${(sp > 14 ? cut.slice(0, sp) : cut).replace(/[\s,;:.(–-]+$/, '')}…`
}
// A number for display: whole numbers for areas, one decimal for a percentage or a dB value; a real minus sign.
export function fmtVal(v, unit) {
  if (!Number.isFinite(v)) return ''
  const t = unit === '%' || unit === 'dB' || /^relative brightness/.test(unit || '') ? v.toFixed(1) : km(v)
  return t.replace(/^-(?=\d)/, '\u2212')
}
// "63.4 %" is written "63.4%": a percent sign sits against its number.
export const withUnit = (v, unit) => (unit === '%' ? `${fmtVal(v, unit)}%` : `${fmtVal(v, unit)}${unit ? ` ${unit}` : ''}`)

// Layers that are the story of a place, not just extras: the coherence pair (Ararat) and the opening layer a manifest names (fire places).
// `lead`: the promoted layers come first (the evidence layers of a place that has a "How we found it" story).
export function splitLayers(layers, event, promote = [], lead = false) {
  if (event) return { main: layers, more: [] }
  const ids = lead ? [...promote, ...PRIMARY, ...PRIMARY_COH] : [...PRIMARY, ...PRIMARY_COH, ...promote]
  const first = [...new Set(ids)].map((id) => layers.find((l) => l.id === id)).filter(Boolean)
  return { main: first, more: layers.filter((l) => !first.includes(l)) }
}
export const isEvent = (m) => m?.kind === 'event' && !!m.event
export const eventType = (m) => (isEvent(m) ? m.event.type || 'earthquake' : null)
// The opening layer: the manifest's own choice when it names a real layer; else fringes (or coh_loss, else the first layer) for an event, water (else the first layer) for a season.
export function defaultLayer(m) {
  const has = (id) => m.layers.find((l) => l.id === id)
  const pick = (m.defaultLayer && has(m.defaultLayer)) || (isEvent(m) ? has('fringes') || has('coh_loss') : has('water')) || m.layers[0]
  return pick.id
}
// The evidence block of a place found by before/after (fire season places and the ev_* places): layer ids, series labels and the five sentences, leading "1. " removed.
export const evidenceOf = (m) => (m?.evidence && Array.isArray(m.evidence.steps) ? m.evidence : null)
export const evidenceLayerIds = (m) => { const e = evidenceOf(m); return e ? [e.before, e.after, e.diff, e.mask].filter((id) => id && m.layers.some((l) => l.id === id)) : [] }
export const evidenceSteps = (m) => (evidenceOf(m)?.steps || []).map((t) => String(t).replace(/^\s*\d+\.\s*/, ''))
// A fact that belongs to the evidence chart (the inside/outside echo series and their difference), not to the water headline.
export const isEvidenceFact = (m, f) => { const e = evidenceOf(m); return !!e && (e.series.includes(f.label) || f.label === 'Difference') }
// Compare for a season place that has no dated layer on screen: the evidence layer against its twin (after/diff/patch against before, before against after).
export function compareTwin(m, layer) {
  const e = evidenceOf(m)
  if (!e || !layer || isEvent(m) || !evidenceLayerIds(m).includes(layer.id)) return null
  return m.layers.find((l) => l.id === (layer.id === e.before ? e.after : e.before)) || null
}
export const compareName = (m, l) => { const e = evidenceOf(m); return l.id === e?.before ? 'Before' : l.id === e?.after ? 'After' : shortLabel(l) }
// The caution line of a place: shown wherever its headline number is, never hidden.
export const cautionOf = (m) => (typeof m?.caution === 'string' && m.caution.trim() ? m.caution.trim() : null)
// A view of a quake place (ascending / descending) whose control is as big as its signal; its layer is marked too.
export function noisyLayer(m, l) {
  if (!isEvent(m)) return false
  const dir = /(^|_)asc(_|$)/.test(l.id) ? 'ascending' : /(^|_)desc(_|$)/.test(l.id) ? 'descending' : null
  return !!dir && (m.event.views || []).some((v) => v.label === dir && noisyView(v))
}
// A place whose story is not open water: the manifest says "none" and gives its own note.
// The date a dated opening layer shows: the manifest's defaultDate snapped to a pass the place really has.
export const defaultDateIndex = (m) => (m.defaultDate && m.dates.length ? m.dates.indexOf(nearest(m.dates, m.defaultDate)) : 0)
export const noHeadline = (m) => m?.headline === 'none'
// A view is too noisy to use when its control pair moves nearly as much as the event pair.
export const noisyView = (v) => Number.isFinite(v?.p2p_cm) && Number.isFinite(v?.control_p2p_cm) && v.control_p2p_cm >= 0.8 * v.p2p_cm
// Ground-motion cycle of the fringes, read from the manifest's own legend text ("one colour cycle = 5 cm ...").
export function fringeCycle(layers) {
  const u = layers?.find((l) => l.id === 'fringes')?.legend?.unit
  const x = /=\s*([\d.]+)\s*cm/.exec(u || '')
  return x ? x[1] : null
}

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

// A place as the globe and the lists see it: a season of passes or a one-off event (earthquake, volcano, landslide).
export const centre = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
export const categoryOf = (m) => m.category || (isEvent(m) ? eventType(m) : 'other')
const dayOf = (t) => String(t).slice(0, 10)
function span(m) {
  const ds = isEvent(m) ? [m.event.time, ...eventViews(m.event).flatMap((v) => [v.before, v.after])].filter(Boolean).map(dayOf) : m.dates || []
  return ds.length ? [ds.reduce((a, b) => (b < a ? b : a)), ds.reduce((a, b) => (b > a ? b : a))] : null
}
export const makePlace = (m, ts) => ({
  id: m.id, name: m.name, lngLat: centre(m.bounds), kind: isEvent(m) ? 'event' : 'season', event: isEvent(m) ? m.event : null,
  category: categoryOf(m), noWater: noHeadline(m), note: noHeadline(m) ? m.headlineNote || null : null, caption: noHeadline(m) ? null : m.headlineNote || null, caution: cautionOf(m), span: span(m),
  peak: noHeadline(m) ? null : peakWater(ts),
})
export const magnitude = (e) => (Number.isFinite(e?.magnitude) ? e.magnitude.toFixed(1) : null)
const cmOf = (x) => (Number.isFinite(x) ? km(x) : null)
// Tooltip / summary text for an event. An earthquake: magnitude and how far the ground moved (first usable view). Other events: the manifest's own summary.
export function eventTip(p) {
  const e = p.event, mag = magnitude(e), quake = (e.type || 'earthquake') === 'earthquake'
  const head = `${p.name.split(',')[0]}${mag ? `, M${mag}` : ''}`
  const usable = (e.views || []).find((v) => Number.isFinite(v.p2p_cm) && !noisyView(v))
  const cm = quake ? cmOf(usable?.p2p_cm) : null
  const rest = [e.time ? shortDate(e.time) : null, cm ? `ground moved ≈ ${cm} cm along the radar's line of sight` : null, !quake ? e.summary : null].filter(Boolean).join(' · ')
  return { head, rest }
}
export const eventSummary = (p) => [magnitude(p.event) && `M${magnitude(p.event)}`, p.event.time && shortDate(p.event.time), !magnitude(p.event) && p.event.summary].filter(Boolean).join(' · ')
export const placeSummary = (p) => (p.kind === 'event' ? eventSummary(p) : p.note ? p.note : p.peak?.v >= 1 ? `${km(p.peak.v)} km² peak water` : 'no open water mapped')

// Classes: id order and labels come from summary.json when it loads, else from here. An unknown id is Title-cased and goes last.
export const CLASS_ORDER = ['flood', 'lake', 'wetland', 'land', 'fire', 'earthquake', 'volcano', 'landslide']
export const CLASS_LABEL = { flood: 'Floods', lake: 'Lakes', wetland: 'Wetlands and deltas', land: 'Farmland and forest', fire: 'Wildfires', earthquake: 'Earthquakes', volcano: 'Volcanoes', landslide: 'Landslides' }
const titleCase = (s) => String(s).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
export function classList(places, summary) {
  const ids = [...new Set(places.map((p) => p.category))]
  const label = (id) => summary?.find((c) => c.id === id)?.label || CLASS_LABEL[id] || titleCase(id)
  const order = [...(summary?.map((c) => c.id) || []), ...CLASS_ORDER]
  const rank = (id) => { const i = order.indexOf(id); return i < 0 ? order.length : i }
  return ids.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).map((id) => ({ id, label: label(id), n: places.filter((p) => p.category === id).length }))
}
export const groupPlaces = (places, summary) => classList(places, summary).map((c) => ({ key: c.id, title: c.label, items: places.filter((p) => p.category === c.id) }))
const WATER_CLASSES = ['flood', 'lake', 'wetland', 'land']
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
export function landingText(places, summary) {
  const cs = classList(places, summary)
  const n = places.length
  const days = places.flatMap((p) => p.span || []).sort()
  const [a, b] = days.length ? [new Date(days[0]).getUTCMonth(), new Date(days[days.length - 1]).getUTCMonth()] : [null, null]
  const when = a == null ? '' : a === b ? ` in ${MONTHS[a]} 2026` : ` between ${MONTHS[a]} and ${MONTHS[b]} 2026`
  return `NISAR watched ${n} place${n === 1 ? '' : 's'}${when}: ${cs.map((c) => `${c.label.toLowerCase()} (${c.n})`).join(', ')}.`
}
// What the two passes and the earthquake look like on the bar: "19 Oct → ⚡ 22 Oct → 31 Oct".
export const eventViews = (e) => (e?.views || []).filter((v) => v.before && v.after)
export const viewMotion = (v) => (Number.isFinite(v.p2p_cm) ? `≈ ${km(v.p2p_cm)} cm` : null)
export const viewControl = (v) => (Number.isFinite(v.control_p2p_cm) ? `≈ ${km(v.control_p2p_cm)} cm` : null)
export function viewingNote(e) {
  const vs = e?.views || [], dirs = new Set(vs.map((v) => v.label))
  const NUM = ['zero', 'one', 'two', 'three', 'four', 'five', 'six']
  const how = vs.length < 2 ? 'one viewing direction' : dirs.size > 1 ? 'two viewing directions' : `${NUM[vs.length] || vs.length} passes of the same viewing direction`
  return `Provisional data, ${how}: this is motion along the radar's line of sight, not up/down or sideways.`
}

export function distanceKm([lng1, lat1], [lng2, lat2]) {
  const r = Math.PI / 180, a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lng2 - lng1) * r) / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(a))
}
export const nearestPlace = (places, pos) => places.reduce((a, b) => (distanceKm(pos, b.lngLat) < distanceKm(pos, a.lngLat) ? b : a))

// Dot style on the globe: water places by their peak open water (as before), earthquakes a ring, every other class its own colour and shape.
export function dotKind(peak) {
  if (peak == null || peak.v < 1) return 'land'
  return peak.v >= 1000 ? 'big' : 'some'
}
export const DOT_COLOR = { big: '#4aa8ff', some: '#35c7b8', land: '#f2b84b', quake: '#ff4f8b', fire: '#ff7a1a', volcano: '#c77dff', landslide: '#b98b5e', other: '#a9b4c0' }
export const SHAPE_CLASSES = ['fire', 'volcano', 'landslide', 'other'] // drawn as a shape; the rest are circles / the earthquake ring
export const SHAPE = { fire: 'diamond', volcano: 'triangle', landslide: 'square', other: 'hexagon' }
// The style key of a place: 'big' | 'some' | 'land' (water classes), 'quake', or its class (fire, volcano, landslide, other).
export function placeStyle(p) {
  if (p.kind === 'event' && (p.event?.type || 'earthquake') === 'earthquake') return 'quake'
  if (WATER_CLASSES.includes(p.category) && p.kind !== 'event') return dotKind(p.peak)
  return DOT_COLOR[p.category] && p.category !== 'earthquake' ? p.category : 'other'
}
export const dotRadius = (peak) => 4 + 2.4 * Math.log10(1 + (peak?.v || 0))
// Earthquake dots grow with magnitude (M6.8 is about 9 px, M7.7 about 11 px).
export const quakeRadius = (mag) => 5 + 2.2 * Math.max(0, (Number.isFinite(mag) ? mag : 6) - 5)
export const placeRadius = (p) => (p.kind === 'event' ? (placeStyle(p) === 'quake' ? quakeRadius(p.event?.magnitude) : 8) : SHAPE_CLASSES.includes(placeStyle(p)) ? 8 : dotRadius(p.peak))

// Link state. Format: #/<place>?layer=<id>&date=<YYYY-MM-DD>[&compare&split=<2-98>&other=<YYYY-MM-DD>]; theme is a normal query parameter (?theme=a|b|c).
export function parseHash(h = location.hash) {
  const [path, query = ''] = h.replace(/^#\/?/, '').split('?')
  const q = new URLSearchParams(query)
  return { id: path || null, layer: q.get('layer'), date: q.get('date'), about: q.has('about'), compare: q.has('compare'), split: q.has('split') ? Math.min(98, Math.max(2, +q.get('split') || 50)) : 50, ...(q.get('other') ? { other: q.get('other') } : {}) }
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
