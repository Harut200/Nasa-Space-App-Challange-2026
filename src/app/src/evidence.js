// "How we found it" for places with manifest.evidence: five plain steps, each tied to one layer and one number from the manifest.
import { fmtDate, fmtKm } from './data.js'

const rng = (a) => (!a || !a.length ? '' : a.length === 1 ? fmtDate(a[0]) : `${fmtDate(a[0])} to ${fmtDate(a[a.length - 1])}`)
const sgn = (v, d = 2) => (v == null || !Number.isFinite(v) ? '–' : `${v < 0 ? '−' : '+'}${Math.abs(v).toFixed(d)}`)
const isBurn = (p) => p.evi?.kind === 'burn'

export function evidenceSteps(p) {
  const e = p.evi, st = e.stats || {}, T = e.steps || []
  const has = (id) => id && p.layers.some((l) => l.id === id)
  const diffLayer = has(e.diff) ? e.diff : null
  const burn = isBurn(p)
  const per = st.persistence ? Object.values(st.persistence) : []
  const above = per.filter((x) => x.above_threshold).length
  const steps = []
  if (has(e.before)) steps.push({
    key: 'before', title: burn ? 'Before the fire' : 'Before', text: T[0], layer: e.before,
    stat: { big: String((st.before_dates || []).length || ''), unit: '', label: (st.before_dates || []).length === 1 ? 'pass, used as the image' : 'passes, combined into one image', sub: rng(st.before_dates) },
  })
  if (has(e.after)) steps.push({
    key: 'after', title: burn ? 'After the fire' : 'After', text: T[1], layer: e.after,
    stat: { big: String((st.after_dates || []).length || ''), unit: '', label: (st.after_dates || []).length === 1 ? 'pass, used as the image' : 'passes, combined into one image', sub: [rng(st.after_dates), (st.excluded_dates || []).length ? `left out: ${st.excluded_dates.map(fmtDate).join(', ')}` : ''].filter(Boolean).join(' · ') },
  })
  if (diffLayer) steps.push({
    key: 'diff', title: 'What changed', text: T[2], layer: diffLayer,
    stat: Number.isFinite(st.mean_change) ? { big: sgn(st.mean_change), unit: '', label: 'mean change of the echo inside the patch, on the 0–1 preview scale', sub: Number.isFinite(st.area_km2) ? `${fmtKm(st.area_km2)} km² · ${st.area_label || 'area of changed radar echo'}` : '', text: true } : null,
  })
  if (T[3] && (has(e.mask) || diffLayer)) steps.push({
    key: 'control', title: 'A check', text: T[3], layer: has(e.mask) ? e.mask : diffLayer,
    stat: Number.isFinite(st.control_mean) ? { big: sgn(st.control_mean), unit: '', label: 'mean change in the same pixels before the event', sub: Number.isFinite(st.control_frac_above) ? `${st.control_frac_above > 0 && st.control_frac_above < 0.005 ? 'under 1' : Math.round(st.control_frac_above * 100)} % of them cross the threshold` : '', text: true } : null,
  })
  if (T[4]) steps.push({
    key: 'curve', title: 'The same place, pass by pass', text: T[4], layer: has(e.after) ? e.after : diffLayer, chart: !!(e.inside && e.outside),
    stat: per.length ? { big: `${above} of ${per.length}`, unit: '', label: 'later passes with the patch above the threshold', text: true } : null,
  })
  return steps
}
export { rng }
