// Data access and derived numbers. Everything shown comes from manifest.json / timeseries.json.
export const BASE = import.meta.env.BASE_URL
export const FLAGSHIP = 'tonle-sap'

const cache = new Map()
const getJson = (url) => {
  if (!cache.has(url)) cache.set(url, fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json() }))
  return cache.get(url)
}
export const loadIndex = () => getJson(`${BASE}data/index.json`).then((j) => j.areas.filter((a) => a !== 'demo'))
export const loadPlace = (id) =>
  Promise.all([getJson(`${BASE}data/${id}/manifest.json`), getJson(`${BASE}data/${id}/timeseries.json`)])
    .then(([m, ts]) => buildPlace(m, ts))

// Image address for a layer on a date (contract C).
export const frameUrl = (place, layer, date) => {
  const l = place.layers.find((x) => x.id === layer)
  if (!l) return null
  if (l.dates) {
    const d = l.dates.includes(date) ? date : null
    return d ? `${BASE}data/${place.id}/${layer}/${d}.webp` : null
  }
  return `${BASE}data/${place.id}/${layer}.webp`
}
export const isDated = (place, layer) => !!place.layers.find((x) => x.id === layer)?.dates
// Earthquake places: one before/after pair, no dated passes, no timeseries.
export const isEvent = (place) => !!place && place.kind === 'event' && !!place.event

const sum = (a) => a.reduce((s, v) => s + (v || 0), 0)

function buildPlace(m, ts0) {
  let ts = ts0
  const facts = ts.facts || []
  ts = { ...ts, series: ts.series || [] }
  const find = (label) => facts.find((f) => f.label === label)
  const water = find('Open water')
  const flood = find('Flooded vegetation')
  const going = facts.find((f) => f.label.startsWith('of which'))
  // spot with the largest HH change first→last, for the radar scene
  let spotFocus = null, best = -1
  for (const s of ts.series || []) {
    const h = s.hh.filter((v) => v != null)
    if (h.length > 1) { const d = Math.abs(h[h.length - 1] - h[0]); if (d > best) { best = d; spotFocus = s.spot } }
  }
  // The series that drives the chart: open water if there is any, else flooded vegetation,
  // else the brightness at the first spot (always honest about its unit).
  const pct = facts.find((f) => f.unit === '%' && f.values.some((v) => v != null))
  const waterMax = water ? Math.max(...water.values.filter((v) => v != null), 0) : 0
  let primary
  if (m.kind === 'event') primary = { kind: 'event', label: '', unit: '', values: [] }
  else if (m.headline === 'none') primary = null
  else if (pct && waterMax < 1) primary = { kind: 'pct', label: pct.label, unit: '%', values: pct.values }
  else if (water && Math.max(...water.values) > 0) primary = { kind: 'water', label: 'Open water', unit: 'km²', values: water.values, lo: water.lo, hi: water.hi }
  else if (flood && Math.max(...flood.values) > 0) primary = { kind: 'flood', label: 'Flooded vegetation', unit: 'km²', values: flood.values }
  else {
    const s = ts.series.find((x) => x.spot === spotFocus) || ts.series[0]
    const hv = (s ? s.hh : []).filter((v) => v != null)
    const db = ts.unit === 'dB'
    const spotName = m.spots.find((p) => p.id === s?.spot)?.label || 'first spot'
    primary = db
      ? { kind: 'hh', label: `HH backscatter, ${spotName}`, unit: 'dB', values: s ? s.hh : [], fixedMin: Math.floor(Math.min(0, ...hv) / 5) * 5, fixedMax: Math.ceil(Math.max(-1e9, ...hv, -5) / 5) * 5 }
      : { kind: 'hh', label: `HH brightness, ${spotName}`, unit: 'relative brightness', values: s ? s.hh : [], fixedMin: 0, fixedMax: Math.max(100, Math.ceil(Math.max(0, ...hv) / 20) * 20) }
  }
  if (!primary) {
    const s = ts.series.find((x) => x.spot === spotFocus) || ts.series[0]
    const hv = (s ? s.hh : []).filter((v) => v != null)
    const db = ts.unit === 'dB'
    const spotName = m.spots.find((p) => p.id === s?.spot)?.label || 'first spot'
    primary = db
      ? { kind: 'hh', label: `HH backscatter, ${spotName}`, unit: 'dB', values: s ? s.hh : [], fixedMin: Math.floor(Math.min(0, ...hv) / 5) * 5, fixedMax: Math.ceil(Math.max(-1e9, ...hv, -5) / 5) * 5 }
      : { kind: 'hh', label: `HH brightness, ${spotName}`, unit: 'relative brightness', values: s ? s.hh : [], fixedMin: 0, fixedMax: Math.max(100, Math.ceil(Math.max(0, ...hv) / 20) * 20) }
  }
  const vals = primary.values.filter((v) => v != null)
  const change = vals.length ? Math.max(...vals) - Math.min(...vals) : 0
  // Evidence (manifest.evidence): the plain steps, the two series by their labels, the numbers behind them.
  let evi = null
  const E = m.evidence
  if (E && typeof E === 'object') {
    const [la, lb] = E.series || []
    const fa = find(la), fb = find(lb)
    evi = { kind: E.kind, before: E.before, after: E.after, diff: E.diff, mask: E.mask || null, stats: E.stats || {},
      steps: (E.steps || []).map((t) => String(t).replace(/^\s*\d+\.\s*/, '')), labels: [la, lb],
      inside: fa ? fa.values : null, outside: fb ? fb.values : null, unit: fa?.unit || '' }
  }
  // A burn scar is not water: no water or flooded-vegetation layers for wildfires (the class names would misread the scar).
  const layers = String(m.category).toLowerCase() === 'fire' ? (m.layers || []).filter((l) => !['water', 'classes', 'when'].includes(l.id)) : m.layers
  return { ...m, layers, evi, calibrated: m.source === 'gamma0', ts, facts, water, flood, going, primary, change, spotFocus,
    quality: m.quality || {}, gaps: m.gaps || [], spots: m.spots || [],
    peak: vals.length ? Math.max(...vals) : 0, totalFlood: sum(flood?.values || []) }
}

const fmtAbs = (v) => (v === 0 ? '0' : v >= 100 ? Math.round(v).toLocaleString('en-US') : v >= 10 ? v.toFixed(0) : v.toFixed(1))
export const fmtKm = (v) => (v == null || !Number.isFinite(v) ? '—' : v < 0 ? `−${fmtAbs(-v)}` : fmtAbs(v))
// A value of the primary series with its unit, e.g. "5,046 km²", "43 %", "−11.0 dB".
export const fmtVal = (P, v) => (v == null || !Number.isFinite(v) ? '—' : P.unit === 'dB' ? `${v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)} dB` : P.unit === 'km²' ? `${fmtKm(v)} km²` : P.unit === '%' ? `${fmtKm(v)} %` : fmtKm(v))
export const unitLabel = (P) => (P.unit === 'km²' || P.unit === '%' || P.unit === 'dB' ? P.unit : '')
const ZERO = /^[−]?0(\.0)?$/
export const isZeroChange = (v) => v == null || ZERO.test(fmtKm(Math.abs(v)))
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty', 'twenty-one', 'twenty-two', 'twenty-three', 'twenty-four', 'twenty-five']
export const numWord = (n) => WORDS[n] || String(n)
export const capWord = (n) => { const w = numWord(n); return w[0].toUpperCase() + w.slice(1) }
// Which data the place rests on, from manifest.source only.
export const isCalibrated = (p) => !!p && p.source === 'gamma0'
export const dataNotice = (p) => (!p || isEvent(p) ? null : isCalibrated(p) ? 'Calibrated NISAR data, provisional calibration.' : 'Preview images, about 180 m per pixel, not calibrated: patterns and timing are reliable, exact areas are not.')
// The layer a place opens on in Explore.
export const defaultLayer = (p) => {
  const has = (id) => p.layers.some((l) => l.id === id)
  if (p.defaultLayer && has(p.defaultLayer)) return p.defaultLayer
  if (isEvent(p)) return has('fringes') ? 'fringes' : p.layers[0]?.id
  if (p.primary.kind === 'pct' && has('coh')) return 'coh'
  return has('water') ? 'water' : p.layers[0]?.id
}
// The pass a place opens on when the manifest names one (defaultDate), else null.
export const defaultIndex = (p) => { const i = p.defaultDate ? p.dates.indexOf(p.defaultDate) : -1; return i >= 0 ? i : null }
// First pass that has a value for the primary series (Ararat has no change figure for the first pass).
export const startIndex = (p) => { const i = p.primary.values.findIndex((v) => v != null); return i < 0 ? 0 : i }
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtDate = (d) => { const [, m, day] = d.split('-'); return `${+day} ${MON[+m - 1]}` }
export const fmtDateLong = (d) => `${fmtDate(d)} 2026`
const isoDay = (iso) => String(iso).slice(0, 10)
export const fmtUTC = (iso) => { const t = String(iso); return `${fmtDateLong(isoDay(t))}, ${t.slice(11, 16)} UTC` }
// manifest notes use a hyphen before negative numbers; show a real minus
export const tidy = (t) => String(t ?? '').replace(/(^|[\s(=:])-(?=\d)/g, '$1−')
export const fmtSigned = (v) => (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v)
export const dayNum = (d) => Date.parse(d + 'T00:00:00Z') / 864e5

// ---------- places as lists and globes see them (any number of places, any event type) ----------
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)
// Event types we know get their own colour and shape; an unknown type gets a generic label and a diamond.
const TYPES = {
  earthquake: { label: 'Earthquake', plural: 'Earthquakes', color: '#ff4f8b', shape: 'ring' },
  volcano: { label: 'Volcano', plural: 'Volcanoes', color: '#ff8a3d', shape: 'triangle' },
  landslide: { label: 'Landslide', plural: 'Landslides', color: '#b58cff', shape: 'square' },
  flood: { label: 'Flood', plural: 'Floods', color: '#5fd9ff', shape: 'drop' },
  wildfire: { label: 'Wildfire', plural: 'Wildfires', color: '#ffb02e', shape: 'triangle' },
}
export const SEASON = { label: 'Season of water', plural: 'Seasons of water', color: '#4aa8ff', shape: 'dot' }
export const eventType = (p) => (isEvent(p) ? String(p.event.type || 'event').trim().toLowerCase() || 'event' : null)
export const typeInfo = (t) => TYPES[t] || { label: cap(t || 'event'), plural: `${cap(t || 'event')}s`, color: '#e6e0cf', shape: 'diamond' }
export const kindKey = (p) => (isEvent(p) ? `e:${eventType(p)}` : 's')
// Season dots by peak open water: big blue, some teal, none amber (as on the donor globe).
export const SEASON_COLOR = { big: '#4aa8ff', some: '#35c7b8', land: '#f2b84b' }
export const centreOf = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
export const magOf = (e) => (finite(Number(e?.magnitude)) && e?.magnitude != null ? Number(e.magnitude) : null)
const MONTHS_LONG = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const dmy = (iso) => { const d = new Date(iso); return Number.isNaN(+d) ? '' : `${d.getUTCDate()} ${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}` }
export const placeColor = (p) => (isEvent(p) ? typeInfo(eventType(p)).color : classOf(p) !== 'season' ? classInfo(classOf(p)).color : p.peak >= 1000 && p.primary.kind === 'water' ? SEASON_COLOR.big : p.primary.kind === 'water' && p.peak >= 1 ? SEASON_COLOR.some : SEASON_COLOR.land)
export const dotSize = (p) => (isEvent(p) ? 5 + 2.2 * Math.max(0, (magOf(p.event) ?? 6) - 5) : p.primary.kind === 'water' ? 4 + 2.4 * Math.log10(1 + (p.peak || 0)) : 4)
// The real headline number of a place: short (for lists and cards) and long (for the globe tooltip).
export function headline(p) {
  if (p._h) return p._h
  if (p.headline === 'none') return { short: 'radar echo', tag: 'changed', long: p.headlineNote || 'Radar echo changed' }
  if (isEvent(p)) {
    const e = p.event, m = magOf(e), v0 = e.views?.[0], cmv = finite(v0?.p2p_cm) ? v0.p2p_cm : null
    const t = typeInfo(eventType(p))
    const short = m != null ? `M ${m.toFixed(1)}` : t.label
    const long = [m != null ? `${t.label}, M ${m.toFixed(1)}` : t.label, e.time ? dmy(e.time) : '', cmv != null ? `ground moved ≈ ${fmtKm(cmv)} cm along the radar’s line of sight` : ''].filter(Boolean).join(' · ')
    return { short: cmv != null ? `≈ ${fmtKm(cmv)} cm` : short, tag: short, long }
  }
  const P = p.primary
  const vals = P.values || []
  const i = vals.reduce((b, v, k) => (v != null && (b < 0 || v > vals[b]) ? k : b), -1)
  const when = i >= 0 && p.dates[i] ? fmtDate(p.dates[i]) : ''
  if (P.kind === 'water' && p.peak >= 1) return { short: `${fmtKm(p.peak)} km²`, tag: 'open water', long: `${fmtKm(p.peak)} km² open water at its peak${when ? ` (${when})` : ''}` }
  if (P.kind === 'flood' && p.peak >= 1) return { short: `${fmtKm(p.peak)} km²`, tag: 'flooded vegetation', long: `${fmtKm(p.peak)} km² flooded vegetation at its peak${when ? ` (${when})` : ''}` }
  if (P.kind === 'pct' && finite(p.peak)) return { short: `${fmtKm(p.peak)} %`, tag: 'changed', long: `Up to ${fmtKm(p.peak)} % ${P.label.charAt(0).toLowerCase() + P.label.slice(1)}` }
  return { short: 'no open water', tag: 'land surface', long: 'No open water mapped here: a land-surface story' }
}
export const kindLabel = (p) => (isEvent(p) ? typeInfo(eventType(p)).label : p.calibrated ? 'Season of water, calibrated' : 'Season of water')
// Everything a person might type to find a place: its name, the USGS place line, its kind, event type and a few plain words.
export function searchText(p) {
  const ci = classInfo(classOf(p))
  const w = [p.name, p.id.replace(/-/g, ' '), ci.label, ci.id]
  if (isEvent(p)) {
    const t = typeInfo(eventType(p))
    w.push(t.label, t.plural, 'event', p.event.place || '', 'before after')
  } else {
    w.push('season', 'seasons of water', 'water')
    if (p.flood && Math.max(...p.flood.values.filter(finite), 0) > 0) w.push('flood', 'flooded vegetation')
    if (/flood|delta|haor|indus|mekong|nile/i.test(p.name + ' ' + p.id)) w.push('flood')
    if (p.calibrated) w.push('calibrated', 'gamma0', 'provisional')
    if (p.primary.kind === 'pct') w.push('coherence', 'farming', 'fields')
  }
  return w.join(' ').toLowerCase()
}
export function matches(p, q, kind) {
  if (kind && kind !== 'all' && classOf(p) !== kind) return false
  const toks = String(q || '').toLowerCase().split(/[\s,]+/).filter(Boolean)
  if (!toks.length) return true
  const hay = p._hay || (p._hay = searchText(p))
  return toks.every((t) => hay.includes(t))
}
export const coordText = (b) => { const [x, y] = centreOf(b); return `${Math.abs(y).toFixed(2)}° ${y < 0 ? 'S' : 'N'}, ${Math.abs(x).toFixed(2)}° ${x < 0 ? 'W' : 'E'}` }
export const dmyDate = dmy

// ---------- classes: the one way to choose a place (manifest.category; events fall back to event.type, seasons to "season") ----------
const CLASS_LIST = [
  { id: 'flood', label: 'Floods', color: '#4aa8ff', shape: 'dot', desc: 'Water spreading over land through the season' },
  { id: 'lake', label: 'Lakes', color: '#35c7b8', shape: 'dot', desc: 'Water bodies that hold steady or shrink' },
  { id: 'wetland', label: 'Wetlands and deltas', color: '#5fd6a4', shape: 'dot', desc: 'Marsh, channels and floodplains' },
  { id: 'land', label: 'Farmland and forest', color: '#e3b54f', shape: 'dot', desc: 'Fields and forest edges changing' },
  { id: 'season', label: 'Seasons of water', color: '#4aa8ff', shape: 'dot', desc: 'A season of radar passes over water and land' },
  { id: 'fire', label: 'Wildfires', color: '#ff5a36', shape: 'flame', desc: 'Forest burned and the radar echo changed' },
  { id: 'earthquake', label: 'Earthquakes', color: '#ff4f8b', shape: 'ring', desc: 'The ground itself moved between two passes' },
  { id: 'volcano', label: 'Volcanoes', color: '#ff8a3d', shape: 'triangle', desc: 'Fresh lava or ash changed the surface' },
  { id: 'landslide', label: 'Landslides', color: '#b58cff', shape: 'square', desc: 'A slope failed and the surface changed' },
]
export const classOf = (p) => (isEvent(p) ? eventType(p) : String(p.category || 'season').toLowerCase())
export const classInfo = (id) => CLASS_LIST.find((c) => c.id === id) || { id, label: cap(String(id).replace(/[-_]/g, ' ')) + (id.endsWith('s') ? '' : 's'), color: '#e6e0cf', shape: 'diamond', desc: '' }
// the classes present in the data, in display order, with counts
export function classesIn(places) {
  const n = new Map()
  for (const p of places) n.set(classOf(p), (n.get(classOf(p)) || 0) + 1)
  const order = CLASS_LIST.map((c) => c.id)
  return [...n].sort((a, b) => (order.indexOf(a[0]) < 0 ? 99 : order.indexOf(a[0])) - (order.indexOf(b[0]) < 0 ? 99 : order.indexOf(b[0])) || a[0].localeCompare(b[0])).map(([id, count]) => ({ id, count, ...classInfo(id) }))
}
export const classColor = (p) => classInfo(classOf(p)).color

// ---------- summary.json: one small request that is enough for the landing globe, the class chips, the counts and the search ----------
export const loadSummary = () => getJson(`${BASE}data/summary.json`).then((j) => j.places || []).catch(() => [])
export function stubFromSummary(s) {
  const h = s.headline || {}, ev = s.kind === 'event', none = !s.headline && s.note
  const v = Number.isFinite(h.value) ? h.value : null
  const hl = none ? { short: 'radar echo', tag: 'changed', long: s.note } : ev
    ? { short: Number.isFinite(h.p2p_cm) ? `≈ ${fmtKm(h.p2p_cm)} cm` : v != null ? `M ${v.toFixed(1)}` : 'event', tag: v != null ? `M ${v.toFixed(1)}` : 'event', long: [`${cap(s.eventType || 'event')}${v != null ? `, M ${v.toFixed(1)}` : ''}`, h.date ? dmy(h.date) : '', Number.isFinite(h.p2p_cm) ? `ground moved ≈ ${fmtKm(h.p2p_cm)} cm along the radar’s line of sight` : ''].filter(Boolean).join(' · ') }
    : h.unit === 'km²' && v != null && v >= 1
      ? { short: `${fmtKm(v)} km²`, tag: String(h.label || 'open water').toLowerCase(), long: `${fmtKm(v)} km² ${String(h.label || 'open water').toLowerCase()} at its peak${h.date ? ` (${fmtDate(h.date)})` : ''}` }
      : h.unit === '%' && v != null ? { short: `${fmtKm(v)} %`, tag: 'changed', long: `Up to ${fmtKm(v)} % ${String(h.label || '').toLowerCase()}` }
        : { short: 'no open water', tag: 'land surface', long: 'No open water mapped here: a land-surface story' }
  const p = { id: s.id, name: s.name, kind: s.kind, category: s.category, bounds: s.bounds, source: s.source, headline: none ? 'none' : undefined, defaultLayer: s.defaultLayer, defaultDate: s.defaultDate, caution: s.caution, calibrated: s.source === 'gamma0', _stub: true, _h: hl,
    peak: v, dates: [], flood: null, primary: { kind: h.unit === 'km²' ? 'water' : h.unit === '%' ? 'pct' : 'hh', values: [], label: h.label || '' },
    event: ev ? { type: s.eventType || 'event', magnitude: v, time: h.date, place: h.label, views: [{ p2p_cm: h.p2p_cm }] } : null }
  p._hay = `${s.searchText || ''} ${s.name} ${classInfo(classOf(p)).label} ${classOf(p)}${p.calibrated ? ' calibrated' : ''}`.toLowerCase()
  return p
}
