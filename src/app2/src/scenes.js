import { fmtDate, fmtKm } from './data.js'

const sentences = (t, n) => (t.match(/[^.!?]+[.!?]+(\s|$)/g) || [t]).slice(0, n).join('').trim()

// Four to six scenes per place, built only from the manifest story parts, facts and spots.
export function buildScenes(p) {
  const n = p.dates.length, last = n - 1
  const nm = p.name.split(',')[0]
  const P = p.primary
  const vals = P.values
  const gap = p.gaps[0]
  const sp = (id) => p.spots.find((s) => s.id === id)
  const passStat = { big: String(n), unit: '', label: 'radar passes', sub: `${fmtDate(p.dates[0])} → ${fmtDate(p.dates[last])}` }
  const live = (idx) => {
    const v = vals[idx]
    if (v == null) return passStat
    return { big: v, unit: P.unit === 'km²' ? 'km²' : '', label: P.label, sub: P.lo ? `plausible range ${fmtKm(P.lo[idx])}–${fmtKm(P.hi[idx])}` : (P.unit === 'km²' ? '' : 'uncalibrated display brightness') }
  }
  const scenes = []
  scenes.push({
    key: 'overture', kicker: 'Overture', title: nm, layer: 'hh', at: 0, compare: false,
    text: `${n} passes of the NISAR L-band radar between ${fmtDate(p.dates[0])} and ${fmtDate(p.dates[last])}. Each pass is one beat.${gap ? ` The instrument was off from ${fmtDate(gap[0])} to ${fmtDate(gap[1])}, so the score has a rest.` : ''}`,
    stat: () => passStat,
  })
  scenes.push({
    key: 'see', kicker: 'What you see', title: 'The season plays', layer: 'water', at: 0, play: true, text: sentences(p.story.see, 3), full: p.story.see,
    stat: live,
  })
  const gained = vals[last] != null && vals[0] != null ? vals[last] - vals[0] : null
  scenes.push({
    key: 'physics', kicker: 'What is happening', title: 'When each place changed', layer: 'when', at: last, text: sentences(p.story.physics, 3), full: p.story.physics,
    stat: () => (P.unit === 'km²' && gained != null
      ? { big: Math.abs(gained), sign: gained >= 0 ? '+' : '−', unit: 'km²', label: `${P.label.toLowerCase()}, ${fmtDate(p.dates[0])} to ${fmtDate(p.dates[last])}`, sub: '' }
      : passStat),
  })
  const fl = p.flood
  const fpeak = fl ? Math.max(...fl.values) : 0
  const fi = fl ? fl.values.indexOf(fpeak) : 0
  const focus = sp(p.spotFocus)
  const ts0 = p.ts.series.find((s) => s.spot === p.spotFocus)
  scenes.push({
    key: 'lband', kicker: 'Why radar sees it', title: 'Before and after, in L-band', layer: 'hh', at: last, compare: true, focus: focus?.id, text: sentences(p.story.lband, 3), full: p.story.lband,
    stat: () => (fpeak >= 1
      ? { big: fpeak, unit: 'km²', label: `possible flooded vegetation at its peak, ${fmtDate(p.dates[fi])}`, sub: '' }
      : ts0 ? { big: ts0.hh[last], unit: '', label: `${focus?.label || 'spot'}: HH brightness, from ${ts0.hh[0]} on ${fmtDate(p.dates[0])}`, sub: 'uncalibrated display brightness, 0 to 100', dec: 1 } : passStat),
  })
  scenes.push({
    key: 'people', kicker: 'Why it matters', title: 'People and place', layer: 'water', at: last, text: sentences(p.story.people, 3), full: p.story.people,
    stat: live,
  })
  scenes.push({
    key: 'sure', kicker: 'Honestly', title: 'How sure are we?', layer: 'water', at: last, sheet: true,
    text: 'These are free preview images at about 180 m per pixel, and they are not calibrated. Timing and patterns are reliable; exact areas are not. Every number here comes with its range, and every caveat is one click away.',
    stat: () => (P.lo && P.hi ? { big: `${fmtKm(P.lo[last])}–${fmtKm(P.hi[last])}`, unit: 'km²', label: `plausible range for ${P.label.toLowerCase()} on ${fmtDate(p.dates[last])}`, sub: '', text: true } : passStat),
  })
  return scenes
}
