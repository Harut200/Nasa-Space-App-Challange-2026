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
  let primary
  if (m.kind === 'event') primary = { kind: 'event', label: '', unit: '', values: [] }
  else if (water && Math.max(...water.values) > 0) primary = { kind: 'water', label: 'Open water', unit: 'km²', values: water.values, lo: water.lo, hi: water.hi }
  else if (flood && Math.max(...flood.values) > 0) primary = { kind: 'flood', label: 'Flooded vegetation', unit: 'km²', values: flood.values }
  else {
    const s = ts.series.find((x) => x.spot === spotFocus) || ts.series[0]
    primary = { kind: 'hh', label: `HH brightness, ${m.spots.find((p) => p.id === s?.spot)?.label || 'first spot'}`, unit: 'relative brightness', values: s ? s.hh : [], fixedMax: Math.max(100, Math.ceil(Math.max(0, ...(s ? s.hh : []).filter((v) => v != null)) / 20) * 20) }
  }
  const vals = primary.values.filter((v) => v != null)
  const change = vals.length ? Math.max(...vals) - Math.min(...vals) : 0
  return { ...m, ts, facts, water, flood, going, primary, change, spotFocus,
    quality: m.quality || {}, gaps: m.gaps || [], spots: m.spots || [],
    peak: vals.length ? Math.max(...vals) : 0, totalFlood: sum(flood?.values || []) }
}

export const fmtKm = (v) => (v == null ? '—' : v === 0 ? '0' : v >= 100 ? Math.round(v).toLocaleString('en-US') : v >= 10 ? v.toFixed(0) : v.toFixed(1))
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtDate = (d) => { const [, m, day] = d.split('-'); return `${+day} ${MON[+m - 1]}` }
export const fmtDateLong = (d) => `${fmtDate(d)} 2026`
const isoDay = (iso) => String(iso).slice(0, 10)
export const fmtUTC = (iso) => { const t = String(iso); return `${fmtDateLong(isoDay(t))}, ${t.slice(11, 16)} UTC` }
export const fmtSigned = (v) => (v < 0 ? '−' : v > 0 ? '+' : '') + Math.abs(v)
export const dayNum = (d) => Date.parse(d + 'T00:00:00Z') / 864e5
