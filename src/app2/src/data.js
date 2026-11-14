// Data access and derived numbers. Everything shown comes from manifest.json / timeseries.json.
export const BASE = import.meta.env.BASE_URL
export const FLAGSHIP = 'tonle-sap'

const cache = new Map()
const getJson = (url) => {
  if (!cache.has(url)) cache.set(url, fetch(url).then((r) => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json() }))
  return cache.get(url)
}
export const loadIndex = () => getJson(`${BASE}data/index.json`).then((j) => j.areas.filter((a) => a !== 'demo'))
// One small request: classes, counts, thumbnails and card figures for every place. Optional: the app works without it.
export const loadSummary = () => getJson(`${BASE}data/summary.json`).catch(() => null)
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
  // season-type places whose story is not water (wildfires): no open-water series; the manifest note is the headline
  else if (m.headline === 'none') primary = { kind: 'none', label: m.headlineNote || '', unit: '', values: (m.dates || []).map(() => null) }
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
  const vals = primary.values.filter((v) => v != null)
  const change = vals.length ? Math.max(...vals) - Math.min(...vals) : 0
  return { ...m, calibrated: m.source === 'gamma0', ts, facts, water, flood, going, primary, change, spotFocus,
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
  if (isEvent(p)) return has('fringes') ? 'fringes' : has('coh_loss') ? 'coh_loss' : p.layers[0]?.id
  return p.primary.kind === 'pct' && has('coh') ? 'coh' : 'water'
}
// First pass that can be shown: one with a value for the primary series (Ararat has no change figure for the first pass),
// or, for places without a series (wildfires), the first pass the default layer has an image for.
export const startIndex = (p) => {
  if (p.primary.kind === 'none') {
    const d = p.layers.find((l) => l.id === defaultLayer(p))?.dates?.[0]
    const k = d ? p.dates.indexOf(d) : 0
    return k < 0 ? 0 : k
  }
  const i = p.primary.values.findIndex((v) => v != null)
  return i < 0 ? 0 : i
}
// ---- events: earthquakes (magnitude, fringes, line of sight) and others (volcano, landslide: coherence and lost coherence) ----
export const isQuake = (p) => isEvent(p) && (p.event.type ? p.event.type === 'earthquake' : p.event.magnitude != null)
// A view whose control pair shows nearly as much motion as the event pair is too noisy to give a number.
export const isNoisy = (v) => v.p2p_cm != null && v.control_p2p_cm != null && v.control_p2p_cm >= 0.8 * v.p2p_cm
export const goodViews = (e) => (e.views || []).filter((v) => v.p2p_cm != null && !isNoisy(v))
// Centimetres per fringe colour cycle, read from the fringes legend ("one colour cycle = 5 cm of ..."), never assumed.
export const cycleCm = (p) => {
  const L = p.layers.find((l) => l.id === 'fringes')?.legend
  if (!L) return null
  const m = /=\s*([\d.]+)\s*cm/.exec(String(L.unit || ''))
  return m ? Number(m[1]) : /cm/.test(String(L.unit || '')) && Number.isFinite(L.max) ? L.max : null
}
export const eventNoun = (p) => (isQuake(p) ? 'earthquake' : 'event')
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtDate = (d) => { const [, m, day] = d.split('-'); return `${+day} ${MON[+m - 1]}` }
export const fmtDateLong = (d) => `${fmtDate(d)} 2026`
const isoDay = (iso) => String(iso).slice(0, 10)
export const fmtUTC = (iso) => { const t = String(iso); return `${fmtDateLong(isoDay(t))}, ${t.slice(11, 16)} UTC` }
// manifest notes use a hyphen before negative numbers; show a real minus
export const tidy = (t) => String(t ?? '').replace(/(^|[\s(=:])-(?=\d)/g, '$1−')
export const fmtSigned = (v) => (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v)
export const dayNum = (d) => Date.parse(d + 'T00:00:00Z') / 864e5
// "First pass" for the left side of a compare, said truly: the change layer starts at the second pass, the coherence at the first pair.
export const firstLabel = (place, layer) => (layer?.id === 'coh' ? 'First pair' : layer?.dates?.[0] && layer.dates[0] !== place.dates[0] ? 'Earliest image' : 'First pass')

// ---- classes (gallery sections) ----
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)
// Display order and labels when summary.json is not available; with it, its `categories` list decides both.
const CLASS_FALLBACK = [['flood', 'Floods'], ['lake', 'Lakes'], ['wetland', 'Wetlands and deltas'], ['land', 'Farmland and forest'], ['fire', 'Wildfires'], ['earthquake', 'Earthquakes'], ['volcano', 'Volcanoes'], ['landslide', 'Landslides']]
export const categoryOf = (p) => String((isEvent(p) ? p.event.type : null) || p.category || 'other').toLowerCase()
// [{id, label, n}] for the places in `entries` (each {id, category}), in the data's own order; unknown ids Title-cased after the known ones.
export function classList(entries, summary) {
  const count = new Map()
  entries.forEach((e) => count.set(e.category, (count.get(e.category) || 0) + 1))
  const base = summary?.categories?.length ? summary.categories.map((c) => [c.id, c.label]) : CLASS_FALLBACK
  const out = base.filter(([id]) => count.has(id)).map(([id, label]) => ({ id, label, n: count.get(id) }))
  for (const [id, n] of count) if (!out.some((c) => c.id === id)) out.push({ id, label: cap(String(id).replace(/[-_]/g, ' ')), n })
  return out
}
// "floods, lakes and wetlands" from labels
export const joinList = (a) => (a.length > 1 ? `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}` : a[0] || '')
