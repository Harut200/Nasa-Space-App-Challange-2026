import { cycleCm, defaultLayer, fmtDate, fmtDateLong, fmtKm, fmtSigned, fmtUTC, goodViews, isEvent, isNoisy, isQuake, isZeroChange, startIndex } from './data.js'

// split after . ! ? only where a space follows, so "about 0.3" stays whole
const sentences = (t, n) => String(t).trim().split(/(?<=[.!?])\s+/).slice(0, n).join(' ')

// Four to six scenes per place, built only from the manifest story parts, facts and spots.
export function buildScenes(p) {
  if (isEvent(p)) return buildEventScenes(p)
  const n = p.dates.length, last = n - 1
  const nm = p.name.split(',')[0]
  const P = p.primary
  const vals = P.values
  const gap = p.gaps[0]
  const sp = (id) => p.spots.find((s) => s.id === id)
  const has = (id) => p.layers.some((l) => l.id === id)
  const first = startIndex(p)
  const passStat = { big: String(n), unit: '', label: 'radar passes', sub: `${fmtDate(p.dates[0])} → ${fmtDate(p.dates[last])}` }
  const pct = P.kind === 'pct'
  const dbUnit = p.ts.unit === 'dB'
  const lower = P.label.charAt(0).toLowerCase() + P.label.slice(1)
  // "43 %" + "of the valley floor changed between passes" (the manifest label, read as a sentence)
  const pctStat = (v) => ({ big: v, unit: '%', label: `of the ${lower}` })
  const live = (idx) => {
    const v = vals[idx]
    if (v == null) return passStat
    if (pct) return { ...pctStat(v), sub: `pass ${idx + 1} of ${n}, ${fmtDate(p.dates[idx])}` }
    return { big: v, unit: P.unit === 'km²' ? 'km²' : dbUnit ? 'dB' : '', label: P.label, sub: P.lo ? `plausible range ${fmtKm(P.lo[idx])}–${fmtKm(P.hi[idx])}` : (P.unit === 'km²' ? '' : dbUnit ? 'calibrated backscatter' : 'uncalibrated display brightness') }
  }
  // places with no visible open water show the radar itself (or, where there is one, the coherence) instead of an empty water layer
  const emptyWater = P.kind === 'hh' || P.kind === 'pct' || Math.max(...vals.filter((v) => v != null), 0) < 1
  // wildfires: no open-water series; every scene runs on the layer the manifest names and the big text is its headline note
  const none = P.kind === 'none'
  const dl = none ? defaultLayer(p) : null
  const wl = none ? dl : pct && has('coh') ? 'coh' : emptyWater ? 'hh' : 'water'
  const noteStat = (idx) => ({ big: p.headlineNote || p.name, text: true, unit: '', label: '', sub: `pass ${idx + 1} of ${n}, ${fmtDate(p.dates[idx])}` })
  const sureText = [
    p.calibrated ? 'Calibrated NISAR data, provisional calibration.' : 'These are free preview images at about 180 m per pixel, and they are not calibrated.',
    p.calibrated ? 'Treat the numbers as a careful measurement of this season, not a finished product.' : 'Timing and patterns are reliable; exact areas are not.',
    P.lo && P.hi ? 'Every number here comes with its range, and every caveat is one click away.' : 'Every caveat is one click away.',
  ].join(' ')
  const scenes = []
  scenes.push({
    key: 'overture', kicker: 'Overture', title: nm, layer: pct && has('coh') ? 'coh' : 'hh', at: pct ? first : 0, compare: false,
    text: `${n} passes of the NISAR L-band radar between ${fmtDate(p.dates[0])} and ${fmtDate(p.dates[last])}. Each pass is one beat.${gap ? ` The instrument was off from ${fmtDate(gap[0])} to ${fmtDate(gap[1])}, so the score has a rest.` : ''}`,
    stat: () => passStat,
  })
  scenes.push({
    key: 'see', kicker: 'What you see', title: 'The season plays', layer: wl, at: first, play: true, text: sentences(p.story.see, 3), full: p.story.see,
    note: !none && p.headlineNote ? p.headlineNote : undefined, // a caption the data adds beside the numbers (sevan, pantanal, sudd)
    stat: none ? noteStat : live,
  })
  const gained = vals[last] != null && vals[0] != null ? vals[last] - vals[0] : null
  const settled = pct && has('coh_change')
  scenes.push({
    key: 'physics', kicker: 'What is happening', title: settled ? 'Where the fields settled' : none ? 'What the radar saw' : 'When each place changed', layer: none ? dl : settled ? 'coh_change' : 'when', at: last,
    text: settled ? sentences(p.story.lband, 3) : sentences(p.story.physics, 3), full: settled ? p.story.lband : p.story.physics,
    stat: () => {
      if (none) return noteStat(last)
      if (pct) {
        const a = vals[first], z = vals[last]
        return a != null && z != null && first !== last ? { big: `${fmtKm(a)} → ${fmtKm(z)}`, unit: '%', label: `${lower}, first pair to last pair`, sub: `${fmtDate(p.dates[first])} to ${fmtDate(p.dates[last])}`, text: true } : passStat
      }
      if (P.unit !== 'km²' || gained == null) return passStat
      const down = gained < 0
      const steady = isZeroChange(gained) || Math.abs(gained) < Math.max(1, (vals[0] || 0) * 0.005)
      if (steady) return { big: 'Steady', unit: '', label: `${P.label.toLowerCase()}, ${fmtDate(p.dates[0])} to ${fmtDate(p.dates[last])}`, sub: isZeroChange(gained) ? 'no change' : `${down ? '−' : '+'}${fmtKm(Math.abs(gained))} km² from first pass to last`, text: true }
      return { big: Math.abs(gained), sign: down ? '−' : '+', unit: 'km²', label: `${down ? 'less' : 'more'} ${P.label.toLowerCase()} on ${fmtDate(p.dates[last])} than on ${fmtDate(p.dates[0])}`, sub: '' }
    },
  })
  const fl = p.flood
  const fpeak = fl ? Math.max(...fl.values) : 0
  const fi = fl ? fl.values.indexOf(fpeak) : 0
  const focus = sp(p.spotFocus)
  const ts0 = p.ts.series.find((s) => s.spot === p.spotFocus)
  const wmax = p.water ? Math.max(...p.water.values.filter((v) => v != null), 0) : 0
  const cohCmp = pct && has('coh') && p.layers.find((l) => l.id === 'coh').dates?.length > 1
  scenes.push({
    key: 'lband', kicker: 'Why radar sees it', title: cohCmp ? 'Before and after, in coherence' : 'Before and after, in L-band', layer: none ? dl : cohCmp ? 'coh' : 'hh', at: last, compare: true, focus: cohCmp ? undefined : focus?.id,
    text: cohCmp ? sentences(p.story.physics, 3) : sentences(p.story.lband, 3), full: cohCmp ? p.story.physics : p.story.lband,
    stat: () => {
      if (none) return noteStat(last)
      if (cohCmp) return p.water ? { big: wmax, unit: 'km²', label: 'open water found by the brightness map on every pass, probably too little', sub: '' } : passStat
      return fpeak >= 1
        ? { big: fpeak, unit: 'km²', label: `possible flooded vegetation at its peak, ${fmtDate(p.dates[fi])}`, sub: '' }
        : ts0 ? { big: ts0.hh[last], unit: dbUnit ? 'dB' : '', label: `${focus?.label || 'spot'}: HH ${dbUnit ? 'backscatter' : 'brightness'}, from ${dbUnit ? fmtKm(ts0.hh[0]) : ts0.hh[0]} on ${fmtDate(p.dates[0])}`, sub: dbUnit ? 'calibrated, decibels' : 'relative brightness, uncalibrated', dec: 1 } : passStat
    },
  })
  scenes.push({
    key: 'people', kicker: 'Why it matters', title: 'People and place', layer: wl, at: last, text: sentences(p.story.people, 3), full: p.story.people,
    stat: none ? noteStat : live,
  })
  scenes.push({
    key: 'sure', kicker: 'Honestly', title: 'How sure are we?', layer: wl, at: last, sheet: true,
    text: sureText,
    stat: () => (P.lo && P.hi ? { big: `${fmtKm(P.lo[last])}–${fmtKm(P.hi[last])}`, unit: 'km²', label: `plausible range for ${P.label.toLowerCase()} on ${fmtDate(p.dates[last])}`, sub: '', text: true } : passStat),
  })
  return scenes
}

const dayOf = (iso) => String(iso).slice(0, 10)
const cm = (v) => `≈ ${v} cm`
const viewName = (v) => `${v.label}, track ${v.track}`
const NOISY = 'too noisy to use'
// The clock time only when it is a recorded one (an inferred date carries a time_note and a placeholder 00:00).
const whenText = (e) => {
  const t = String(e.time), clock = t.slice(11, 16)
  return !e.time_note && clock && clock !== '00:00' ? fmtUTC(t) : fmtDateLong(dayOf(t))
}

// Events are one beat. Earthquakes: five to seven scenes. Volcanoes, landslides: four. Built only from the manifest.
function buildEventScenes(p) {
  return isQuake(p) ? buildQuakeScenes(p) : buildChangeScenes(p)
}

// A volcano or a landslide: where the surface changed completely between two passes. No magnitude, no motion in centimetres.
function buildChangeScenes(p) {
  const e = p.event
  const nm = p.name.split(',')[0]
  const views = e.views || []
  const has = (id) => p.layers.some((l) => l.id === id)
  const lossLayer = has('coh_loss') ? 'coh_loss' : null
  const cohLayer = has('coherence') ? 'coherence' : null
  const title = e.title || p.name
  const summary = e.summary ? { big: e.summary, unit: '', label: `${nm}: the area of the two-pass comparison where the radar picture no longer matches`, sub: '', text: true } : { big: String(views.length), unit: '', label: views.length === 1 ? 'radar pair across the event' : 'radar pairs across the event', sub: '' }
  const L = p.layers.find((l) => l.id === cohLayer)?.legend
  const scenes = []
  scenes.push({
    key: 'beat', kicker: 'One beat', title: 'One beat', layer: null, compare: false, focusLL: e.lngLat,
    text: `A season has many beats. This event is one.${e.place ? ` It happened at ${e.place}.` : ''}${e.time_note ? ` Date note: ${e.time_note}.` : ''}`.replace(/\.\./g, '.'),
    hint: 'The star marks the event site. The map shows bare terrain: no radar layer yet.',
    stat: () => ({ big: title, unit: '', label: `${e.place || nm}, ${whenText(e)}`, sub: e.time_note ? 'date inferred, see the note' : '', text: true }),
  })
  scenes.push({
    key: 'before-after', kicker: 'Before and after', title: 'Before and after', layer: lossLayer || cohLayer, compare: false, strip: true,
    text: sentences(p.story.see, 3), full: p.story.see,
    stat: () => summary,
  })
  scenes.push({
    key: 'fringes', kicker: 'Reading it', title: 'Reading the coherence', layer: cohLayer || lossLayer, legend: true, compare: false,
    text: sentences(p.story.physics, 3), full: [p.story.physics, p.story.lband].filter(Boolean).join(' '),
    stat: () => (L && L.type === 'ramp' ? { big: `${fmtKm(L.min)} to ${fmtKm(L.max)}`, unit: '', label: L.unit || 'coherence', sub: 'the layer shown on the map', text: true } : summary),
  })
  scenes.push({
    key: 'people', kicker: 'Why it matters', title: 'People and place', layer: lossLayer || cohLayer, compare: false, sheet: true,
    text: sentences(p.story.people, 3), full: p.story.people,
    note: 'Honestly: provisional data. This shows where the surface changed, not what it is made of or how thick it is.',
    stat: () => summary,
  })
  return scenes
}

// An earthquake is one beat: five to seven scenes, built only from the manifest (event, story, layers, legends).
function buildQuakeScenes(p) {
  const e = p.event
  const nm = p.name.split(',')[0]
  const views = e.views || []
  const good = goodViews(e) // a view whose control pair is as big as its own signal gives no number
  const noisy = views.filter(isNoisy)
  const v0 = good[0] || views[0]
  const has = (id) => p.layers.some((l) => l.id === id)
  const lposLayer = ['los_cm', 'los_asc_cm', 'los_desc_cm'].find(has) || (has('fringes') ? 'fringes' : null)
  const bothLooks = has('los_asc_cm') && has('los_desc_cm')
  const nLooks = new Set(views.map((v) => v.label)).size
  const ev = fmtDate(dayOf(e.time))
  const between = v0 && dayOf(v0.before) < dayOf(e.time) && dayOf(e.time) < dayOf(v0.after)
  const mag = e.magnitude != null && Number.isFinite(Number(e.magnitude)) ? `M ${Number(e.magnitude).toFixed(1)}` : 'Earthquake'
  const cyc = cycleCm(p)
  const depth = e.depth_km != null && Number.isFinite(Number(e.depth_km)) ? Number(e.depth_km) : null
  const perView = (v) => (isNoisy(v) ? NOISY : v.p2p_cm != null ? cm(v.p2p_cm) : 'not measured')
  const main = good.length ? { big: `≈ ${good[0].p2p_cm}`, unit: 'cm', label: 'ground motion along the radar’s line of sight',
    sub: views.length > 1 ? views.map((v) => `${v.label}, track ${v.track}: ${perView(v)}`).join(' · ') : `peak to peak, ${viewName(good[0])}` }
    : { big: String(views.length), unit: '', label: views.length === 1 ? 'radar pair across the earthquake' : 'radar pairs across the earthquake', sub: noisy.length ? `${noisy.map(viewName).join(', ')}: ${NOISY}` : '' }
  const scenes = []
  scenes.push({
    key: 'beat', kicker: 'One beat', title: 'One beat', layer: null, compare: false, focusLL: e.lngLat,
    text: `A season has many beats. An earthquake is one. It struck ${e.place}${depth != null ? `, at a depth of about ${Math.round(depth)} km (USGS)` : ''}.`,
    hint: 'The star marks the USGS epicentre. The map shows bare terrain: no radar layer yet.',
    stat: () => ({ big: mag, unit: '', label: `${nm}, ${fmtUTC(e.time)}`, sub: depth != null ? `depth ${depth} km` : '' }),
  })
  scenes.push({
    key: 'before-after', kicker: 'Before and after', title: 'Before and after', layer: has('fringes') ? 'fringes' : lposLayer, compare: false, strip: true,
    text: `${views.length > 1 ? 'Each pair is' : 'The pair is'} two passes of the same viewing direction${between ? `: one before the earthquake (${fmtDate(dayOf(v0.before))}), one after (${fmtDate(dayOf(v0.after))}). The earthquake struck on ${ev}, between them` : ''}.${nLooks > 1 ? ' The pairs look from two viewing directions, ascending and descending.' : ''} ${has('fringes') && cyc ? `The coloured bands are fringes: each full colour cycle is ${cyc} cm of ground motion toward or away from the satellite.` : ''}`.replace(/\s+/g, ' ').trim(),
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
    const both = a && d && a.p2p_cm != null && d.p2p_cm != null && !isNoisy(a) && !isNoisy(d)
    scenes.push({
      key: 'looks', kicker: 'Two directions', title: 'Ascending and descending', layer: 'los_desc_cm', compare: true, cmpLayer: 'los_asc_cm', legend: true,
      cmpTags: ['Ascending', 'Descending'],
      text: `The satellite sees the same ground from two sides, on its ascending and descending tracks. The same movement can then look different in size, or even have the opposite sign.${noisy.length ? ` One of the two pairs is ${NOISY} (${noisy.map(viewName).join(', ')}), so only the other gives a number.` : ''}`,
      hint: `Drag the handle on the map: ascending on the left, descending on the right.${has('east_cm') ? ' Explore also has approximate east-west and vertical layers, labelled as approximate.' : ''}`,
      stat: () => (both ? { big: `${a.p2p_cm} / ${d.p2p_cm}`, unit: 'cm', label: 'peak to peak, ascending / descending', sub: `tracks ${a.track} / ${d.track}` } : main),
    })
  }
  const ctl = views.filter((v) => v.control_p2p_cm != null && !isNoisy(v))
  const ctlNoisy = noisy.filter((v) => v.control_p2p_cm != null)
  if (ctl.length || ctlNoisy.length) {
    const c0 = ctl[0]
    const noisyTxt = ctlNoisy.length ? ` ${ctlNoisy.map(viewName).join('; ')}: ${NOISY}, because the control pair shows nearly as much motion as the earthquake pair.` : ''
    scenes.push({
      key: 'control', kicker: 'The control', title: 'The control', layer: has('fringes') ? 'fringes' : lposLayer, compare: false,
      text: (ctl.length === 0 ? 'As a check, we measured the same way on a pair from before the earthquake.'
        : ctl.length === 1
          ? `As a check, we measured the same way on a pair of the same track from before the earthquake. It shows about ${c0.control_p2p_cm} cm, against about ${c0.p2p_cm ?? 'the'} cm across the earthquake. No control image is shown here, only the number.`
          : `As a check, we measured the same way on pairs of the same tracks from before the earthquake. ${ctl.map((v) => `${v.label}, track ${v.track}: about ${v.control_p2p_cm} cm${v.p2p_cm != null ? ` (across the earthquake: about ${v.p2p_cm} cm)` : ''}`).join('; ')}. No control images are shown here, only the numbers.`) + noisyTxt,
      stat: () => (c0 ? { big: `≈ ${c0.control_p2p_cm}`, unit: 'cm', label: 'the same track before the earthquake', sub: ctl.length > 1 ? ctl.map((v) => `${v.label}, track ${v.track}: ${cm(v.control_p2p_cm)}`).join(' · ') : `peak to peak, ${viewName(c0)}` }
        : { big: 'Too noisy', unit: '', label: 'the control pair shows nearly as much motion as the earthquake pair', sub: ctlNoisy.map(viewName).join(' · '), text: true }),
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
