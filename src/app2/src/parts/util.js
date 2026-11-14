// Pure helpers shared by TimelineChart and PlaceGallery. No React, no DOM.
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function dayNum(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number)
  return Date.UTC(y, (m || 1) - 1, d || 1) / 86400000
}
export function fmtDate(iso) {
  const [, m, d] = String(iso).slice(0, 10).split('-').map(Number)
  return `${d} ${MON[m - 1]}`
}
export function fmtNum(v, digits) {
  if (v == null || !Number.isFinite(v)) return '–'
  const dg = digits ?? (Math.abs(v) < 10 && v !== 0 ? 1 : 0)
  return v.toLocaleString('en-US', { minimumFractionDigits: dg, maximumFractionDigits: dg })
}
export function fmtSigned(v, digits) {
  if (v == null || !Number.isFinite(v)) return '–'
  const s = fmtNum(Math.abs(v), digits)
  return (v > 0 && s !== '0' && s !== '0.0' ? '+' : v < 0 && s !== '0' && s !== '0.0' ? '−' : '') + s
}
export function niceMax(v) {
  if (!(v > 0)) return 1
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p
  return 10 * p
}
// Linear map of dates onto [x0, x1] by real days. Degenerate span -> middle.
export function makeXScale(dates, x0, x1) {
  const d = (dates || []).map(dayNum)
  const a = d.length ? d[0] : 0
  const b = d.length ? d[d.length - 1] : 1
  const span = b - a
  const f = (day) => (span > 0 ? x0 + ((day - a) / span) * (x1 - x0) : (x0 + x1) / 2)
  return { f, a, b, days: d }
}
export function nearestIndex(xs, x) {
  let best = 0, bd = Infinity
  for (let i = 0; i < xs.length; i++) {
    const dd = Math.abs(xs[i] - x)
    if (dd < bd) { bd = dd; best = i }
  }
  return best
}
// Month starts strictly inside [a, b] (day numbers).
export function monthTicks(a, b) {
  const out = []
  if (!(b > a)) return out
  const s = new Date(a * 86400000)
  let y = s.getUTCFullYear(), m = s.getUTCMonth() + 1
  for (let k = 0; k < 60; k++) {
    const day = Date.UTC(y, m, 1) / 86400000
    if (day > b) break
    if (day >= a) out.push({ day, label: MON[m] })
    m++; if (m > 11) { m = 0; y++ }
  }
  return out
}
export function openWater(facts) {
  return (facts || []).find((f) => /open water/i.test(f.label)) || (facts || [])[0] || null
}
export function floodedVeg(facts) {
  return (facts || []).find((f) => /flooded veg/i.test(f.label)) || null
}
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

// { peak, peakDate, first, last, delta, pct } for open water. pct is null when first is 0.
export function placeStats(timeseries) {
  const f = openWater(timeseries?.facts)
  const dates = timeseries?.dates || []
  const vals = f?.values || []
  let peak = null, peakDate = null, first = null, last = null
  vals.forEach((raw, i) => {
    const v = num(raw)
    if (v == null) return
    if (first == null) first = v
    last = v
    if (peak == null || v > peak) { peak = v; peakDate = dates[i] ?? null }
  })
  const delta = first != null && last != null ? last - first : null
  const pct = delta != null && first > 0 ? (delta / first) * 100 : null
  return { peak, peakDate, first, last, delta, pct }
}
// quality is keyed by ISO date: { "2026-08-31": { quality: "warn"|"fail", quality_note, channels } }
export function qualityFor(quality, date) {
  const q = quality && quality[date]
  if (!q || (q.quality !== 'warn' && q.quality !== 'fail')) return null
  return { level: q.quality, note: q.quality_note || '', channels: q.channels || [] }
}
// Thumbnail: static composite first, then classes, then last frame of the water/hh layer.
export function thumbUrl(manifest, base = import.meta.env.BASE_URL + 'data/') {
  if (!manifest?.id) return null
  const layers = manifest.layers || []
  const stat = ['rgb', 'classes'].map((id) => layers.find((l) => l.id === id && !l.dates)).find(Boolean)
  if (stat) return `${base}${manifest.id}/${stat.id}.webp`
  const fr = layers.find((l) => l.id === 'water' && l.dates?.length) || layers.find((l) => l.dates?.length)
  if (fr) return `${base}${manifest.id}/${fr.id}/${fr.dates[fr.dates.length - 1]}.webp`
  return null
}
