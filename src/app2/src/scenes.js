import { fmtDate, fmtKm, fmtSigned, fmtUTC, isEvent } from './data.js'

const sentences = (t, n) => (t.match(/[^.!?]+[.!?]+(\s|$)/g) || [t]).slice(0, n).join('').trim()

// Four to six scenes per place, built only from the manifest story parts, facts and spots.
export function buildScenes(p) {
  if (isEvent(p)) return buildEventScenes(p)
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
  // places with no visible open water show the radar itself instead of an empty water layer
  const wl = P.kind === 'hh' || Math.max(...vals.filter((v) => v != null), 0) < 1 ? 'hh' : 'water'
  const scenes = []
  scenes.push({
    key: 'overture', kicker: 'Overture', title: nm, layer: 'hh', at: 0, compare: false,
    text: `${n} passes of the NISAR L-band radar between ${fmtDate(p.dates[0])} and ${fmtDate(p.dates[last])}. Each pass is one beat.${gap ? ` The instrument was off from ${fmtDate(gap[0])} to ${fmtDate(gap[1])}, so the score has a rest.` : ''}`,
    stat: () => passStat,
  })
  scenes.push({
    key: 'see', kicker: 'What you see', title: 'The season plays', layer: wl, at: 0, play: true, text: sentences(p.story.see, 3), full: p.story.see,
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
      : ts0 ? { big: ts0.hh[last], unit: '', label: `${focus?.label || 'spot'}: HH brightness, from ${ts0.hh[0]} on ${fmtDate(p.dates[0])}`, sub: 'relative brightness, uncalibrated', dec: 1 } : passStat),
  })
  scenes.push({
    key: 'people', kicker: 'Why it matters', title: 'People and place', layer: wl, at: last, text: sentences(p.story.people, 3), full: p.story.people,
    stat: live,
  })
  scenes.push({
    key: 'sure', kicker: 'Honestly', title: 'How sure are we?', layer: wl, at: last, sheet: true,
    text: 'These are free preview images at about 180 m per pixel, and they are not calibrated. Timing and patterns are reliable; exact areas are not. Every number here comes with its range, and every caveat is one click away.',
    stat: () => (P.lo && P.hi ? { big: `${fmtKm(P.lo[last])}–${fmtKm(P.hi[last])}`, unit: 'km²', label: `plausible range for ${P.label.toLowerCase()} on ${fmtDate(p.dates[last])}`, sub: '', text: true } : passStat),
  })
  return scenes
}

const dayOf = (iso) => String(iso).slice(0, 10)
const cm = (v) => `≈ ${v} cm`
const viewName = (v) => `${v.label}, track ${v.track}`

// An earthquake is one beat: five to six scenes, built only from the manifest (event, story, layers, legends).
function buildEventScenes(p) {
  const e = p.event
  const nm = p.name.split(',')[0]
  const views = e.views || []
  const v0 = views[0]
  const has = (id) => p.layers.some((l) => l.id === id)
  const lposLayer = ['los_cm', 'los_asc_cm', 'los_desc_cm'].find(has) || (has('fringes') ? 'fringes' : null)
  const bothLooks = has('los_asc_cm') && has('los_desc_cm')
  const ev = fmtDate(dayOf(e.time))
  const between = v0 && dayOf(v0.before) < dayOf(e.time) && dayOf(e.time) < dayOf(v0.after)
  const mag = `M ${Number(e.magnitude).toFixed(1)}`
  const main = v0 && v0.p2p_cm != null ? { big: `≈ ${v0.p2p_cm}`, unit: 'cm', label: 'ground motion along the radar’s line of sight',
    sub: views.length > 1 ? views.map((v) => `${v.label}, track ${v.track}: ${v.p2p_cm != null ? cm(v.p2p_cm) : 'not measured'}`).join(' · ') : `peak to peak, ${viewName(v0)}` }
    : { big: String(views.length), unit: '', label: views.length === 1 ? 'radar pair across the earthquake' : 'radar pairs across the earthquake', sub: '' }
  const scenes = []
  scenes.push({
    key: 'beat', kicker: 'One beat', title: 'One beat', layer: null, compare: false, focusLL: e.lngLat,
    text: `A season has many beats. An earthquake is one. It struck ${e.place}, at a depth of about ${Math.round(e.depth_km)} km (USGS).`,
    hint: 'The star marks the USGS epicentre. The map shows bare terrain: no radar layer yet.',
    stat: () => ({ big: mag, unit: '', label: `${nm}, ${fmtUTC(e.time)}`, sub: `depth ${e.depth_km} km` }),
  })
  scenes.push({
    key: 'before-after', kicker: 'Before and after', title: 'Before and after', layer: has('fringes') ? 'fringes' : lposLayer, compare: false, strip: true,
    text: `${views.length > 1 ? 'Each pair is' : 'The pair is'} two NISAR passes of the same track${between ? `: one before the earthquake (${fmtDate(dayOf(v0.before))}), one after (${fmtDate(dayOf(v0.after))}). The earthquake struck on ${ev}, between them` : ''}. ${has('fringes') ? 'The coloured bands are fringes: each full colour cycle is 10 cm of ground motion toward or away from the satellite.' : ''}`.replace(/\s+/g, ' ').trim(),
    stat: () => main,
  })
  const lo = p.layers.find((l) => l.id === lposLayer)
  const L = lo?.legend
  scenes.push({
    key: 'fringes', kicker: 'Reading the fringes', title: 'Reading the fringes', layer: lposLayer, legend: lposLayer !== 'fringes', compare: false,
    text: sentences(p.story.physics, 3), full: [p.story.physics, p.story.lband].filter(Boolean).join(' '),
    stat: () => (L && L.type === 'diverging' ? { big: `${fmtSigned(L.min)} to ${fmtSigned(L.max)}`, unit: L.unit || 'cm', label: 'colour scale of the displacement map', sub: 'along the radar’s line of sight' } : main),
  })
  if (bothLooks) {
    const a = views.find((v) => v.label === 'ascending'), d = views.find((v) => v.label === 'descending')
    scenes.push({
      key: 'looks', kicker: 'Two directions', title: 'Ascending and descending', layer: 'los_desc_cm', compare: true, cmpLayer: 'los_asc_cm', legend: true,
      cmpTags: ['Ascending', 'Descending'],
      text: 'The satellite sees the same ground from two sides, on its ascending and descending tracks. The same movement can then look different in size, or even have the opposite sign.',
      hint: `Drag the handle on the map: ascending on the left, descending on the right.${has('east_cm') ? ' Explore also has approximate east-west and vertical layers, labelled as approximate.' : ''}`,
      stat: () => (a && d && a.p2p_cm != null && d.p2p_cm != null ? { big: `${a.p2p_cm} / ${d.p2p_cm}`, unit: 'cm', label: 'peak to peak, ascending / descending', sub: `tracks ${a.track} / ${d.track}` } : main),
    })
  }
  const ctl = views.filter((v) => v.control_p2p_cm != null)
  if (ctl.length) {
    const c0 = ctl[0]
    scenes.push({
      key: 'control', kicker: 'The control', title: 'The control', layer: has('fringes') ? 'fringes' : lposLayer, compare: false,
      text: ctl.length === 1
        ? `As a check, we measured the same way on a pair of the same track from before the earthquake. It shows about ${c0.control_p2p_cm} cm, against about ${c0.p2p_cm ?? 'the'} cm across the earthquake. No control image is shown here, only the number.`
        : `As a check, we measured the same way on pairs of the same tracks from before the earthquake. ${ctl.map((v) => `${v.label}, track ${v.track}: about ${v.control_p2p_cm} cm${v.p2p_cm != null ? ` (across the earthquake: about ${v.p2p_cm} cm)` : ''}`).join('; ')}. No control images are shown here, only the numbers.`,
      stat: () => ({ big: `≈ ${c0.control_p2p_cm}`, unit: 'cm', label: 'the same track before the earthquake', sub: ctl.length > 1 ? ctl.map((v) => `${v.label}, track ${v.track}: ${cm(v.control_p2p_cm)}`).join(' · ') : `peak to peak, ${viewName(c0)}` }),
    })
  }
  const looks = new Set(views.map((v) => v.label)).size
  scenes.push({
    key: 'people', kicker: 'Why it matters', title: 'People and place', layer: lposLayer, compare: false, sheet: true,
    text: sentences(p.story.people, 3), full: p.story.people,
    note: `Honestly: provisional data · ${looks > 1 ? 'two viewing directions' : 'one viewing direction'} · line of sight only · sign convention unconfirmed · not validated against GNSS.`,
    stat: () => main,
  })
  return scenes
}
