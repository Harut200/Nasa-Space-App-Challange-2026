// "How we found it": three to five steps per place, each one drives the map. Only numbers that are in the manifest, timeseries or summary.
import { defaultIndex, eventType, fmtDate, fmtKm, isEvent, startIndex, tidy } from './data.js'
import { evidenceSteps, rng } from './evidence.js'

const day = (s) => String(s).slice(0, 10)
const range = (a) => (!a || !a.length ? '' : a.length === 1 ? fmtDate(day(a[0])) : `${fmtDate(day(a[0]))} to ${fmtDate(day(a[a.length - 1]))}`)

export function buildHow(p) {
  if (!p) return []
  if (isEvent(p)) return eventHow(p)
  if (p.evi) { const s = evidenceSteps(p); if (s.length >= 3) return evidenceHow(p, s) }
  return seasonHow(p)
}

function evidenceHow(p, steps) {
  const out = steps.map((s) => ({ key: s.key, title: s.title, text: s.text, layer: s.layer, stat: s.stat, chart: s.chart }))
  if (p.caution) out.push({ key: 'caution', title: 'Read with care', text: p.caution, layer: out[out.length - 1].layer, tone: 'caution' })
  return out
}

function eventHow(p) {
  const e = p.event, views = e.views || [], ba = e.before_after || []
  const has = (id) => p.layers.some((l) => l.id === id)
  const type = eventType(p)
  const when = e.time ? fmtDate(day(e.time)) : null
  const main = views.find((v) => v.id === ba[0]?.view) || views[0]
  const out = []
  if (type === 'earthquake') {
    const fr = ba.find((b) => b.before === 'fringes_before' && has(b.before)) || null
    const mo = ba.find((b) => b.before === 'los_before_cm' && has(b.after)) || null
    const cm = main?.p2p_cm, cc = main?.control_p2p_cm
    const view = main ? `${main.label}, track ${main.track}` : ''
    if (fr) out.push({
      key: 'still', title: 'First, a pair from before', layer: fr.before,
      text: `Two passes of the same track from before the earthquake (${range(fr.before_dates)}). With no earthquake between them, the ground should not have moved, so this is the level of apparent motion the method shows on quiet ground.`,
      stat: cc != null ? { big: `≈ ${cc}`, unit: 'cm', label: 'apparent motion along the line of sight, before the earthquake', sub: view } : null,
    })
    if (has('fringes')) out.push({
      key: 'across', title: 'Then a pair across the quake', layer: 'fringes', cmpLayer: fr ? fr.before : null, cmpTags: fr ? ['Before the quake', 'Across the quake'] : null,
      text: `Two passes of the same track, one before and one after the earthquake${when ? ` of ${when}` : ''}${main ? ` (${fmtDate(day(main.before))} and ${fmtDate(day(main.after))})` : ''}. Each full colour cycle of the fringes is ${p.layers.find((l) => l.id === 'fringes')?.label.match(/= ([\d.]+ cm)/)?.[1] || '10 cm'} of change along the radar's line of sight. Drag the handle to compare.`,
      stat: cm != null ? { big: `≈ ${cm}`, unit: 'cm', label: 'along the radar’s line of sight', sub: `peak to peak, ${view}` } : null,
    })
    const mapLayer = mo ? mo.after : ['los_cm', 'los_asc_cm', 'los_desc_cm'].find((id) => has(id) && (!main || id !== (main.label === 'ascending' ? 'los_desc_cm' : 'los_asc_cm')))
    if (mapLayer) out.push({
      key: 'motion', title: 'The motion map', layer: mapLayer, cmpLayer: mo ? mo.before : null, cmpTags: mo ? ['Before the quake', 'Across the quake'] : null,
      text: 'The fringes turned into centimetres of ground motion along the line of sight. The legend says which colour is toward the satellite and which away; the sign convention is not confirmed by the product guide.',
    })
    if (cm != null && cc != null) out.push({
      key: 'compare', title: 'Earthquake against control', layer: has('fringes') ? 'fringes' : mapLayer,
      text: `The pair across the earthquake shows about ${cm} cm, the pair from before it about ${cc} cm. The first is the earthquake's signal; the second is what the method shows without one.${cm <= 3 * cc ? ' The two are not far apart here, so read the result with care.' : ''}`,
      stat: { big: `${cm} vs ${cc}`, unit: 'cm', label: 'across the earthquake, against the control', sub: view, text: true },
    })
    out.push({
      key: 'claims', title: 'What we do not claim', layer: mapLayer || 'fringes',
      text: `This is ground motion along one line of sight${views.length > 1 ? ` (${view}; other tracks exist but are not used here)` : ''}, from provisional data. It is not a damage map, and it says nothing about casualties.`,
    })
    return out.filter((s) => s.layer)
  }
  // volcanoes, landslides: coherence before, coherence across, where it was lost
  const km = String(e.summary || '').match(/[\d.,]+\s*km²/)?.[0]
  const b = ba.find((x) => has(x.before)) || null
  const label = type === 'volcano' ? 'eruption' : type === 'landslide' ? 'landslide' : type === 'fire' ? 'fire' : 'event'
  if (b) out.push({
    key: 'intact', title: 'First, a pair from before', layer: b.before,
    text: `Coherence says how comparable two passes are: bright means the surface looked the same both times. Here is a pair from before the ${label} (${range(b.before_dates)}): the surface is mostly intact.`,
  })
  if (has('coherence')) out.push({
    key: 'across', title: `Then a pair across the ${label}`, layer: 'coherence', cmpLayer: b ? b.before : null, cmpTags: b ? ['Before', 'Across the event'] : null,
    text: `Two passes, one before and one after the ${label}${when ? ` of ${when}` : ''}${main ? ` (${fmtDate(day(main.before))} and ${fmtDate(day(main.after))})` : ''}. Dark means the radar picture changed. Drag the handle to compare.`,
  })
  if (has('coh_loss')) out.push({
    key: 'lost', title: 'Where it changed completely', layer: 'coh_loss',
    text: `The places that were comparable in the pair from before, but not in the pair across the ${label}.${km ? ` Together ${km}.` : ''}`,
    stat: km ? { big: km.replace(/\s*km²/, ''), unit: 'km²', label: 'of surface changed completely', sub: '' } : null,
  })
  out.push({
    key: 'claims', title: 'What this does not say', layer: has('coh_loss') ? 'coh_loss' : 'coherence',
    text: `Lost coherence shows that the surface changed, not what it changed into: ${type === 'volcano' ? 'fresh lava or ash' : type === 'fire' ? 'burnt ground' : 'fresh debris'} and also vegetation, snow or wetness can do it. It is not a damage map and says nothing about casualties.`,
  })
  return out.filter((s) => s.layer)
}

function seasonHow(p) {
  const P = p.primary, n = p.dates.length, last = n - 1
  const has = (id) => p.layers.some((l) => l.id === id)
  const water = has('water') && P.kind !== 'pct'
  const wl = water ? 'water' : has('coh') ? 'coh' : 'hh'
  const vals = P.values
  // a first pass with no open water at all is an empty picture: open on the first pass that has some
  const bare = water && P.unit === 'km²' && vals[0] != null && vals[0] < 0.5
  const firstWet = bare ? vals.findIndex((v) => v != null && v >= 1) : -1
  const first = P.kind === 'pct' ? startIndex(p) : firstWet > 0 ? firstWet : 0
  let peak = -1
  vals.forEach((v, i) => { if (v != null && (peak < 0 || v > vals[peak])) peak = i })
  const di = defaultIndex(p)
  const at2 = di ?? (peak >= 0 ? peak : last)
  const out = []
  const unit = P.unit === 'km²' ? ' km²' : P.unit === '%' ? ' %' : ''
  out.push({ key: 'first', title: P.kind === 'pct' ? 'The first pair' : 'The first pass', layer: wl, at: first,
    text: P.kind === 'pct' ? `Coherence on ${fmtDate(p.dates[first])}: dark where the ground changed between passes.` : firstWet > 0 ? `No open water on the first pass (${fmtDate(p.dates[0])}). ${fmtDate(p.dates[first])} is the first pass that shows some.` : `${fmtDate(p.dates[first])}, the first pass: ${water ? 'the water as the radar sees it at the start' : 'the radar image at the start'}.`,
    stat: vals[first] != null && P.kind !== 'hh' ? { big: vals[first], unit: P.unit === 'km²' ? 'km²' : P.unit === '%' ? '%' : '', label: P.label, sub: '' } : null })
  if (at2 !== first) out.push({ key: 'peak', title: di != null ? `The pass on ${fmtDate(p.dates[at2])}` : 'The peak', layer: wl, at: at2,
    text: di != null ? `${fmtDate(p.dates[at2])}: the pass the place opens on.` : `${fmtDate(p.dates[at2])}: the pass with the most ${P.label.toLowerCase()}.`,
    stat: vals[at2] != null && P.kind !== 'hh' ? { big: vals[at2], unit: P.unit === 'km²' ? 'km²' : P.unit === '%' ? '%' : '', label: P.label, sub: '' } : null })
  const wh = has('when') ? 'when' : has('change') ? 'change' : has('coh_change') ? 'coh_change' : null
  if (wh) out.push({ key: 'when', title: wh === 'when' ? 'When each place changed' : 'What changed', layer: wh, at: last,
    text: wh === 'when' ? 'Each place coloured by the pass on which it changed for good.' : 'The difference between the first pass and the last.' })
  if (P.kind !== 'hh' && vals[at2] != null) {
    const rangeTxt = P.lo && P.hi ? `, plausible range ${fmtKm(P.lo[at2])}–${fmtKm(P.hi[at2])}${unit}` : ''
    out.push({ key: 'number', title: 'The number', layer: wl, at: at2,
      text: `${P.label} on ${fmtDate(p.dates[at2])}: ${fmtKm(vals[at2])}${unit}${rangeTxt}. ${p.calibrated ? 'From calibrated NISAR data with a provisional calibration.' : 'From NISAR preview images at about 180 m per pixel, not calibrated: patterns and timing are reliable, exact areas are not.'}`,
      stat: { big: fmtKm(vals[at2]), unit: P.unit === 'km²' ? 'km²' : P.unit === '%' ? '%' : '', label: P.label, sub: P.lo && P.hi ? `range ${fmtKm(P.lo[at2])}–${fmtKm(P.hi[at2])}` : '', text: true } })
  }
  if (p.headline === 'none' && p.headlineNote) out.push({ key: 'note', title: 'What we found', layer: wl, at: at2, text: tidy(p.headlineNote) })
  if (p.caution) out.push({ key: 'caution', title: 'Read with care', layer: wl, at: at2, text: p.caution, tone: 'caution' })
  return out.filter((s) => p.layers.some((l) => l.id === s.layer))
}
export { rng }
