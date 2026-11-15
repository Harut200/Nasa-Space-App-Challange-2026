import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FLAGSHIP, centreOf, classInfo, classOf, defaultIndex, defaultLayer, eventType, fmtDate, frameUrl, isDated, isEvent, loadIndex, loadPlace, loadSummary, startIndex, stubFromSummary } from './data.js'
import { useRoute } from './route.js'
import { buildScenes } from './scenes.js'
import { buildHow } from './how.js'
import { rng } from './evidence.js'
import { hasWaterClasses, onlyClass, waterClasses } from './water.js'
const MapStage = lazy(() => import('./MapStage.jsx')) // MapLibre loads after the home page is interactive
const GlobeMap = lazy(() => import('./GlobeMap.jsx'))
// Everything the landing does not need is its own chunk, fetched when the browser is idle after the landing is up (and at the latest when first used).
const loaders = {
  Story: () => import('./Story.jsx'), Explainer: () => import('./Explainer.jsx'), Explore: () => import('./Explore.jsx'),
  EventExplore: () => import('./EventParts.jsx').then((m) => ({ default: m.EventExplore })), Gallery: () => import('./Gallery.jsx'), GalleryFilter: () => import('./GalleryFilter.jsx'),
  GlobeUI: () => import('./GlobeUI.jsx'), InfoPanel: () => import('./InfoPanel.jsx'), Sheet: () => import('./Sheet.jsx'), About: () => import('./About.jsx'),
}
const Story = lazy(loaders.Story), Explainer = lazy(loaders.Explainer), Explore = lazy(loaders.Explore), EventExplore = lazy(loaders.EventExplore), Gallery = lazy(loaders.Gallery), GalleryFilter = lazy(loaders.GalleryFilter)
const GlobeUI = lazy(loaders.GlobeUI), InfoPanel = lazy(loaders.InfoPanel), Sheet = lazy(loaders.Sheet), About = lazy(loaders.About)
const S = ({ children }) => <Suspense fallback={null}>{children}</Suspense>
import Home from './Home.jsx'
import TopBar from './TopBar.jsx'
import Legend from './Legend.jsx'
import { SearchPanel, filterPlaces } from './Search.jsx'
import { reducedMotion } from './ui.jsx'

const useSize = () => {
  const [s, setS] = useState({ w: window.innerWidth, h: window.innerHeight })
  useEffect(() => { const f = () => setS({ w: window.innerWidth, h: window.innerHeight }); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f) }, [])
  return s
}

// The water picture with only one legend colour kept (see water.js); the original while "both" is chosen.
function useOnly(url, color) {
  const [r, setR] = useState({ src: null, color: null, out: null })
  useEffect(() => {
    if (!url || !color) return
    let dead = false
    onlyClass(url, color).then((out) => { if (!dead) setR({ src: url, color, out }) })
    return () => { dead = true }
  }, [url, color])
  if (!url || !color) return url
  return r.src === url && r.color === color && r.out ? r.out : url // until the recoloured picture is ready, the whole picture stays on show
}

const km = (a, b) => { const r = Math.PI / 180, x = Math.sin(((b[1] - a[1]) * r) / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(((b[0] - a[0]) * r) / 2) ** 2; return 12742 * Math.asin(Math.sqrt(x)) }

export default function App() {
  const [route, go] = useRoute()
  const [ids, setIds] = useState([])
  const [places, setPlaces] = useState({}) // id -> built place
  const [failed, setFailed] = useState(false)
  const [indexState, setIndexRaw] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [opacity, setOpacity] = useState(0.92)
  const [cmp, setCmp] = useState(() => ({ on: /[?&]cmp=1/.test(location.hash) && /mode=explore/.test(location.hash), split: 50 }))
  const [spotsOn, setSpotsOn] = useState(true)
  const [activeSpot, setActiveSpot] = useState(null)
  const [sheet, setSheet] = useState(() => /[?&]sheet=1/.test(location.hash))
  const [about, setAbout] = useState(false)
  const [info, setInfo] = useState(false)
  const [searchSheet, setSearchSheet] = useState(false)
  const [q, setQRaw] = useState(() => route.q || '')
  const [kind, setKindRaw] = useState(() => route.cls || 'all')
  const [diving, setDiving] = useState(false)
  const [phase, setPhase] = useState('')
  const [stubs, setStubs] = useState({})
  const [diveKeep, setDiveKeep] = useState('full')
  const diveRef = useRef(false)
  const [diveTo, setDiveTo] = useState(null) // the place the camera is flying to: the map underneath is made ready for it (still hidden)
  const [overlayHold, setOverlayHold] = useState(false)
  const [stageIdle, setStageIdle] = useState(false) // the place map is built once the landing is up and idle, or at once when a place is opened
  const [selected, setSelected] = useState(null)
  const [near, setNear] = useState(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [waterShow, setWaterShow] = useState('both')
  const [gMounted, setGMounted] = useState(false)
  const { w: width, h: height } = useSize()
  const phone = width < 760
  const barInput = useRef(null), panelInput = useRef(null)

  useEffect(() => {
    const go = () => Object.values(loaders).forEach((l) => l().catch(() => {}))
    const t0 = setTimeout(() => setStageIdle(true), 2200)
    const t = setTimeout(() => (window.requestIdleCallback ? requestIdleCallback(go, { timeout: 4000 }) : go()), 1200)
    return () => { clearTimeout(t); clearTimeout(t0) }
  }, [])
  // index and places: the one on show first, then the rest three at a time
  useEffect(() => { loadIndex().then(setIds).catch(() => setFailed(true)) }, [])
  // summary.json: one small request that is enough for the landing globe, the class counts and the search; manifests load as places open
  useEffect(() => { loadSummary().then((l) => setStubs(Object.fromEntries(l.map((s) => [s.id, stubFromSummary(s)])))) }, [])
  const ensure = useCallback((id) => loadPlace(id).then((p) => setPlaces((c) => (c[id] ? c : { ...c, [id]: p }))).catch(() => {}), [])
  useEffect(() => { ensure(route.place || FLAGSHIP) }, [route.place, ensure])
  useEffect(() => {
    let dead = false
    ;(async () => {
      for (let i = 0; i < ids.length; i += 3) { if (dead) return; await Promise.all(ids.slice(i, i + 3).map(ensure)) }
    })()
    return () => { dead = true }
  }, [ids, ensure])
  const list = useMemo(() => ids.map((id) => places[id] || stubs[id]).filter(Boolean), [ids, places, stubs])

  const placeId = route.place && ids.includes(route.place) ? route.place : route.place ? null : FLAGSHIP
  const place = placeId ? places[placeId] : null
  const unknown = route.place && ids.length && !ids.includes(route.place)
  useEffect(() => { if (unknown) go({ place: null, mode: 'open' }, false) }, [unknown, go])

  const mode = route.mode // open | globe | places | story | explore
  const inPlace = mode === 'story' || mode === 'explore'
  const globeFull = mode === 'globe' || (inPlace && !!route.globe)
  const scenes = useMemo(() => (place ? buildScenes(place) : []), [place])
  const sceneN = Math.min(Math.max(1, route.scene || 1), scenes.length || 1)
  const howSteps = useMemo(() => (place ? buildHow(place) : []), [place])
  const how = !!route.how && inPlace && !globeFull && howSteps.length > 0
  const howN = Math.min(Math.max(1, route.step || 1), howSteps.length || 1)
  const hstep = how ? howSteps[howN - 1] : null
  const scene = mode === 'story' ? scenes[sceneN - 1] : null
  const driver = hstep || scene // what drives the map: an explainer step, else the story scene

  const ev = isEvent(place)
  const nDates = place?.dates.length || 1
  const index = Math.min(indexState, nDates - 1) // a new place can have fewer passes than the one just left
  const setIndex = useCallback((i) => setIndexRaw(Math.max(0, Math.min(nDates - 1, i))), [nDates])

  // Layer on show
  const layerId = hstep ? hstep.layer : mode === 'story' ? scene?.layer : mode === 'explore' ? (route.layer && place?.layers.some((l) => l.id === route.layer) ? route.layer : place?.evi && cmp.on && place.layers.some((l) => l.id === place.evi.after) ? place.evi.after : place ? defaultLayer(place) : 'water') : 'water'
  const layer = place?.layers.find((l) => l.id === layerId)

  // Reset the playhead when place / scene / mode changes
  const sceneKey = `${placeId}|${mode}|${how ? `h${howN}` : mode === 'story' ? sceneN : ''}`
  const firstApply = useRef(true)
  const scrub = useRef(0), playTimer = useRef(0), lastPlace = useRef(null), indexRef = useRef(0)
  const applying = useRef(null) // explore playhead index just applied from the URL / defaults; the URL sync waits for it
  useEffect(() => {
    if (!place) return
    setActiveSpot(null); setWaterShow('both')
    const keepCmp = firstApply.current && !!route.cmp
    if (driver && (mode === 'story' || how)) {
      const scene = driver
      const dated = route.date && place.dates.includes(route.date) && firstApply.current ? place.dates.indexOf(route.date) : null
      const target = Math.min(dated ?? (scene.at ?? (how ? indexRef.current : 0)), Math.max(0, place.dates.length - 1))
      // a step that changes the date runs through the passes in between (a 400-600 ms scrub), then playback starts after a short beat
      clearInterval(scrub.current)
      const fromI = Math.min(indexRef.current, place.dates.length - 1)
      if (!firstApply.current && lastPlace.current === placeId && !reducedMotion() && Math.abs(target - fromI) > 1 && Math.abs(target - fromI) < 12) {
        const dir = target > fromI ? 1 : -1, steps = Math.abs(target - fromI), every = Math.max(45, Math.round(520 / steps))
        let k = fromI
        scrub.current = setInterval(() => { k += dir; setIndexRaw(k); if (k === target) clearInterval(scrub.current) }, every)
      } else setIndexRaw(target)
      lastPlace.current = placeId
      const beat = !reducedMotion() && scene.play ? 320 + (Math.abs(target - fromI) > 1 ? 560 : 0) : 0
      clearTimeout(playTimer.current)
      setPlaying(false)
      if (scene.play && !reducedMotion() && dated == null && !isEvent(place)) playTimer.current = setTimeout(() => setPlaying(true), beat)
      if (scene.compare || scene.cmpLayer) setCmp((c) => ({ ...c, on: true })); else setCmp((c) => ({ ...c, on: false }))
    } else if (mode === 'explore') {
      clearInterval(scrub.current); clearTimeout(playTimer.current); lastPlace.current = placeId
      const di = route.date ? place.dates.indexOf(route.date) : defaultIndex(place) ?? -1
      applying.current = di >= 0 ? di : Math.max(0, place.dates.length - 1)
      setIndexRaw(applying.current)
      setPlaying(false)
      setCmp((c) => ({ ...c, on: keepCmp }))
    } else if (mode === 'open') {
      setIndexRaw(0); setPlaying(false); setCmp((c) => ({ ...c, on: false }))
    } else { setPlaying(false); setCmp((c) => ({ ...c, on: false })) }
    firstApply.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneKey, place])
  useEffect(() => { setWaterShow('both') }, [layerId, placeId]) // legend isolation never carries over to another layer or place
  // the expanded globe holds the place still underneath: no playback while it is open
  useEffect(() => { if (globeFull) setPlaying(false) }, [globeFull])

  // Playback: one beat at a time; the opening loops, others stop at the end.
  useEffect(() => {
    if (!playing || !place) return
    const atEnd = index >= nDates - 1
    const t = setTimeout(() => {
      if (atEnd) { if (mode === 'open') setIndexRaw(0); else setPlaying(false) }
      else setIndexRaw(index + 1)
    }, atEnd ? 2200 : mode === 'open' ? 1250 : 1100)
    return () => clearTimeout(t)
  }, [playing, index, nDates, mode, place])

  // keep the URL in step with the explore date
  useEffect(() => {
    if (mode === 'explore' && place && !isEvent(place)) {
      if (applying.current != null) { if (index !== applying.current) return; applying.current = null }
      const d = place.dates[index]
      if (route.date !== d) go({ date: d }, false)
    }
  }, [index, mode, place]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode === 'explore' && place && !!route.cmp !== cmp.on) go({ cmp: cmp.on }, false)
  }, [cmp.on, mode, place]) // eslint-disable-next-line react-hooks/exhaustive-deps

  indexRef.current = index
  const date = place?.dates[index]
  const rawUrl = place && layer ? frameUrl(place, layerId, date) : null
  const dated = place ? isDated(place, layerId) : false
  const firstOfLayer = layer?.dates?.[0]
  // seasons: first pass vs now. Earthquakes: the layer vs bare terrain (explore) or ascending vs descending (story)
  // evidence places: Compare is "before | this layer"; events: the true pre-event partner of the layer on show, else bare terrain
  const evCmp = !!place?.evi && !!layer && !layer.dates
  const pair = ev && layer ? (place.event.before_after || []).find((b) => b.after === layerId && place.layers.some((l) => l.id === b.before)) : null
  const cmpAvailable = ev ? !!layer : evCmp || !!(layer?.dates && layer.dates.length > 1)
  const cmpOn = cmp.on && cmpAvailable && mode !== 'open'
  const cmpLayerId = driver?.cmpLayer ?? (ev ? pair?.before ?? null : evCmp ? place.evi.before : layerId)
  const cmpFirst = place?.layers.find((l) => l.id === cmpLayerId)?.dates?.[0]
  const rawCmp = cmpOn && cmpLayerId ? frameUrl(place, cmpLayerId, cmpFirst) : null

  const outlineUrl = place?.evi?.mask && layerId === place.evi.diff && inPlace && !cmpOn && place.layers.some((l) => l.id === place.evi.mask) ? frameUrl(place, place.evi.mask) : null

  // Blue only / green only on the water layer
  const wc = hasWaterClasses(layer) && inPlace ? waterClasses(layer) : null
  const only = wc && waterShow !== 'both' ? (waterShow === 'blue' ? wc.blue : wc.green) : null
  const url = useOnly(rawUrl, only)
  const cmpUrl = useOnly(rawCmp, only)

  // Preload the other frames of the layer on show, four at a time, so Play never waits; stops when the place or layer changes.
  const layerKey = place && layer && layer.dates ? `${place.id}/${layer.id}` : ''
  useEffect(() => {
    if (!layerKey || !inPlace) return
    let dead = false
    const order = layer.dates.map((d, i) => ({ d, i })).sort((x, y) => Math.abs(x.i - index) - Math.abs(y.i - index)).map((x) => frameUrl(place, layer.id, x.d))
    let next = 0
    const worker = async () => { while (!dead && next < order.length) { const u = order[next++]; try { const im = new Image(); im.decoding = 'async'; im.src = u; await im.decode() } catch { /* a missing frame is handled where it is shown */ } } }
    const t = setTimeout(() => { for (let k = 0; k < 4; k++) worker() }, 300)
    return () => { dead = true; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerKey, inPlace])

  // the next step's picture is fetched and decoded while this one is read, so the cross-fade never waits for it
  useEffect(() => {
    if (!place || !inPlace || globeFull) return
    const nxt = how ? howSteps[howN] : mode === 'story' ? scenes[sceneN] : null
    if (!nxt?.layer) return
    const t = setTimeout(() => {
      const l = place.layers.find((x) => x.id === nxt.layer)
      if (!l) return
      const d = l.dates ? place.dates[Math.min(nxt.at ?? indexRef.current, place.dates.length - 1)] : undefined
      for (const id of [nxt.layer, nxt.cmpLayer]) { const u = id && frameUrl(place, id, id === nxt.layer ? d : place.layers.find((x) => x.id === id)?.dates?.[0]); if (u) { const im = new Image(); im.decoding = 'async'; im.src = u; im.decode?.().catch(() => {}) } }
    }, 500)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placeId, sceneN, howN, how, mode, globeFull])

  const stt = place?.evi?.stats || {}
  const cmpTags = !cmpOn ? null : driver?.cmpTags ? driver.cmpTags : ev
    ? (scene?.cmpTags || (pair ? [`Before · ${rng(pair.before_dates)}`, `Across the event · ${rng(pair.after_dates)}`] : ['Bare terrain', layer?.label.split(/ \(|:/)[0] || 'Layer']))
    : evCmp ? [`Before · ${rng(stt.before_dates)}`, `After · ${rng(stt.after_dates)}`] : [`${layer?.id === 'coh' ? 'First pair' : 'First pass'} · ${firstOfLayer ? fmtDate(firstOfLayer) : ''}`, date ? fmtDate(date) : '']

  // A compare step: the divider slides in from the edge and rests at the middle of the part of the picture that is actually visible (right of the panel), so both sides are on the image.
  const cmpStep = (mode === 'story' || how) && cmpOn
  const midRef = useRef(50)
  useEffect(() => {
    if (!cmpStep) return
    if (reducedMotion()) { setCmp((c) => ({ ...c, split: midRef.current })); return }
    let raf, stopped = false
    const t0 = performance.now(), D = 620
    const tick = (t) => {
      if (stopped) return
      const k = Math.min(1, (t - t0) / D), e = 1 - (1 - k) ** 3
      setCmp((c) => ({ ...c, split: 100 + (midRef.current - 100) * e }))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    setCmp((c) => ({ ...c, split: 100 }))
    raf = requestAnimationFrame(tick)
    const stop = () => { stopped = true; cancelAnimationFrame(raf) }
    window.addEventListener('pointerdown', stop, { once: true })
    return () => { stop(); window.removeEventListener('pointerdown', stop) }
  }, [cmpStep, sceneN, howN, how, placeId])

  // Camera: the free area is what the panels, the timeline and the legend leave. Fit the whole radar footprint into it.
  const fscene = how ? null : scene
  const focusSpot = fscene?.focus ? place?.spots.find((s) => s.id === fscene.focus) : fscene?.focusLL ? { id: 'epicentre', lngLat: fscene.focusLL } : null
  const dockH = Math.min(290, Math.round(height * (height <= 800 ? 0.36 : 0.38)))
  const storyLegend = (mode === 'story' || how) && !!layer?.legend && (!ev || how || !!scene?.legend)
  const padding = mode === 'places' || mode === 'globe' ? { top: 60, bottom: 60, left: 40, right: 40 }
    : mode === 'open' ? (phone ? { top: 150, bottom: 400, left: 20, right: 20 } : { top: 72, bottom: height <= 760 ? 240 : 272, left: Math.round(width * 0.46), right: 40 })
    : phone ? { top: mode === 'story' ? 116 : 250, left: 20, right: 20, bottom: mode === 'story' ? 380 : 300 }
    : mode === 'story' ? { top: 74, bottom: storyLegend ? 126 : 70, left: Math.round(24 + Math.min(640, Math.max(440, width * 0.27)) + 28), right: 44 }
    : { top: 74, bottom: dockH + 84, left: 240, right: 40 }
  // The free area is measured from the real panel rectangles (and re-measured when they resize), so the footprint stays clear at any window size.
  const [measured, setMeasured] = useState(null)
  useEffect(() => {
    if (!inPlace || diving) { setMeasured(null); return }
    let timer = 0
    const run = () => {
      const vw = innerWidth, vh = innerHeight
      const R = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1 ? r : null }
      let left = 24, right = 24, bottom = 24
      let top = (phone ? 112 : 70) + 8
      for (const s of ['.story', '.layers', '.spot-card']) {
        const r = R(s); if (!r) continue
        if (r.width > vw * 0.7 && r.top < vh / 3) top = Math.max(top, r.bottom + 10)
        else if (r.width > vw * 0.7) bottom = Math.max(bottom, vh - r.top + 14)
        else if (r.left + r.width / 2 < vw / 2) left = Math.max(left, r.right + 24)
        else right = Math.max(right, vw - r.left + 24)
      }
      for (const s of ['.dock', '.legend']) { const r = R(s); if (r) bottom = Math.max(bottom, vh - r.top + 14) }
      const next = { top, left: Math.round(left), right: Math.round(right), bottom: Math.round(bottom) }
      setMeasured((m) => (m && Math.abs(m.left - next.left) < 6 && Math.abs(m.right - next.right) < 6 && Math.abs(m.bottom - next.bottom) < 6 && m.top === next.top ? m : next))
    }
    const later = () => { clearTimeout(timer); timer = setTimeout(run, 140) }
    later()
    const ro = new ResizeObserver(later)
    const watch = () => document.querySelectorAll('.story, .layers, .dock, .legend, .spot-card').forEach((e) => ro.observe(e))
    const t2 = setTimeout(watch, 200)
    window.addEventListener('resize', later)
    return () => { clearTimeout(timer); clearTimeout(t2); ro.disconnect(); window.removeEventListener('resize', later) }
  }, [inPlace, diving, phone, mode, sceneN, placeId, layerId, width, height])
  const storyPad = phone ? { top: 116, left: 20, right: 20, bottom: 380 } : { top: 74, bottom: 70, left: Math.round(24 + Math.min(640, Math.max(440, width * 0.27)) + 28), right: 44 }
  const wasHolder = useRef(true) // the map showed a stand-in place until this one's data arrived: go straight there, no flight
  useEffect(() => { if (place) wasHolder.current = false })
  const camera = { key: focusSpot ? focusSpot.id : 'fit', focus: focusSpot?.lngLat, zoomBoost: 1.25, duration: mode === 'story' ? 820 : 1500, jump: !!diveTo || (!!place && wasHolder.current) }
  midRef.current = (() => { const p = measured || padding, W = width || 1; return Math.max(30, Math.min(70, ((p.left + (W - p.left - p.right) / 2) / W) * 100)) })()
  // Compare in Explore: the handle starts in the middle of the visible footprint, so both sides have data
  const exploreCmp = mode === 'explore' && cmpOn
  useEffect(() => { if (exploreCmp) setCmp((c) => ({ ...c, split: midRef.current })) }, [exploreCmp, placeId, layerId, Math.round(midRef.current)]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- navigation ----
  const origin = inPlace ? (route.globe ? 'globe' : route.from) : mode === 'globe' ? 'globe' : mode === 'places' ? 'places' : 'home'
  const fromList = (mode === 'globe' || mode === 'places' || (inPlace && route.globe)) && kind !== 'all'
  const activeClass = inPlace ? route.cls : ''
  const openPlace = useCallback((id, m = 'story', from = origin) => {
    setInfo(false); setSelected(null)
    const want = inPlace && !route.globe ? route.cls : fromList ? kind : ''
    const cls = want && (places[id] || stubs[id]) && classOf(places[id] || stubs[id]) === want ? want : ''
    go({ place: id, mode: m, scene: 1, layer: null, date: null, cmp: false, how: false, step: 1, from, globe: false, q: '', cls })
  }, [go, origin, inPlace, route.globe, route.cls, fromList, kind, places, stubs])
  const goHome = () => { setInfo(false); setSelected(null); go({ place: null, mode: 'open', q: '', globe: false }) }
  const goPlaces = () => { setInfo(false); go({ place: null, mode: 'places', globe: false, q: '', cls: '' }) }
  const closeGlobe = useCallback(() => {
    setSelected(null)
    if (inPlace && route.globe) { if (history.state?.globe) history.back(); else go({ globe: false }, false) }
    else if (history.state?.app > 0) history.back()
    else go({ place: null, mode: 'open', q: '' })
  }, [inPlace, route.globe, go])
  const openGlobe = () => {
    setInfo(false)
    if (globeFull) return
    if (inPlace) go({ globe: true }, true, { globe: 1 })
    else go({ place: null, mode: 'globe', q: '' })
  }
  const goBack = () => {
    setInfo(false)
    if (globeFull) return closeGlobe()
    if (inPlace) return route.from === 'globe' ? go({ place: null, mode: 'globe', q: '' }) : route.from === 'places' ? go({ place: null, mode: 'places' }) : go({ place: null, mode: 'open' })
    if (history.state?.app > 0) history.back(); else goHome()
  }
  const pool0 = useMemo(() => (activeClass ? ids.filter((id) => (places[id] || stubs[id]) && classOf(places[id] || stubs[id]) === activeClass) : ids), [ids, places, stubs, activeClass])
  const poolIdx = pool0.indexOf(placeId)
  const hop = (d) => {
    if (!pool0.length) return
    const i = poolIdx < 0 ? 0 : poolIdx
    openPlace(pool0[(i + d + pool0.length) % pool0.length], inPlace ? mode : 'story')
  }
  const random = () => {
    const pool = pool0.filter((id) => id !== (inPlace ? placeId : null))
    if (pool.length) openPlace(pool[Math.floor(Math.random() * pool.length)], inPlace ? mode : 'story')
  }
  const nearMe = () => {
    if (!navigator.geolocation) return setNear('Your browser cannot share a location.')
    setNear('Finding the nearest place we cover…')
    navigator.geolocation.getCurrentPosition((pos) => {
      const here = [pos.coords.longitude, pos.coords.latitude]
      const p = list.reduce((a, b) => (km(here, centreOf(b.bounds)) < km(here, centreOf(a.bounds)) ? b : a))
      setNear(`Nearest place we cover: ${p.name}, ${Math.round(km(here, centreOf(p.bounds))).toLocaleString('en-US')} km away.`)
      openPlace(p.id)
    }, () => setNear('Location was not shared, so we cannot find the nearest place. Pick a dot instead.'), { timeout: 8000 })
  }
  const setScene = (n) => go({ scene: Math.max(1, Math.min(scenes.length, n)), date: null }, false)
  const setLayer = (l) => go({ layer: l }, false)
  const setMode = (m) => go(m === 'story' ? { mode: 'story', scene: 1, layer: null, date: null, how: false, step: 1 } : { mode: 'explore', layer: null, date: null, how: false, step: 1 })
  const setHow = (on) => go(on ? { how: true, step: 1 } : { how: false, step: 1 }, false)
  const setStep = (n) => go({ how: true, step: Math.max(1, Math.min(howSteps.length, n)) }, false)
  const setQ = useCallback((v) => { setQRaw(v); if (mode === 'globe' || mode === 'places') go({ q: v }, false) }, [mode, go])
  useEffect(() => { if (mode === 'globe' || mode === 'places') { setQRaw(route.q || ''); setKindRaw(route.cls || 'all') } }, [route.q, route.cls, mode])
  const setKind = useCallback((v) => { setKindRaw(v); if (mode === 'globe' || mode === 'places') go({ cls: v === 'all' ? '' : v }, false) }, [mode, go])
  useEffect(() => { if (!globeFull) { setSelected(null); setNear(null) } }, [globeFull])
  const focusSearch = () => {
    if (globeFull) { if (panelInput.current) panelInput.current.focus(); else setPanelOpen(true) }
    else if (phone) setSearchSheet(true)
    else barInput.current?.focus()
  }
  const pickFromSearch = (p) => { setSearchSheet(false); setQRaw(''); openPlace(p.id, inPlace ? mode : 'story') }

  // Keyboard
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag = e.target.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      const k = e.key
      if (k === 'Escape') {
        if (searchSheet) setSearchSheet(false)
        else if (sheet) setSheet(false)
        else if (how) setHow(false)
        else if (about) setAbout(false)
        else if (info) setInfo(false)
        else if (selected) setSelected(null)
        else if (globeFull) closeGlobe()
        else if (mode === 'places') goBack()
        return
      }
      if (sheet || about || searchSheet) return
      if (k === '?' || k === 'h') { setSheet(true); return }
      if (k === '/') { e.preventDefault(); focusSearch(); return }
      if (k === 'g' || k === 'G') { if (globeFull) closeGlobe(); else openGlobe(); return }
      if (k === 'r' || k === 'R') { random(); return }
      if (globeFull) return
      if (mode === 'open') { if (k === 'Enter' && tag !== 'BUTTON') { openPlace(FLAGSHIP) } else if (k === 'p') goPlaces(); return }
      if (mode === 'places') {
        const cards = [...document.querySelectorAll('.gallery .pt-card')]
        if (!cards.length || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(k)) return
        e.preventDefault()
        let i = cards.indexOf(document.activeElement)
        if (i < 0) { cards[0].focus(); return }
        const r = cards[i].getBoundingClientRect()
        if (k === 'ArrowRight') i = Math.min(cards.length - 1, i + 1)
        else if (k === 'ArrowLeft') i = Math.max(0, i - 1)
        else { // up / down: the card above or below, nearest in x
          const dir = k === 'ArrowDown' ? 1 : -1
          const cand = cards.filter((c) => { const b = c.getBoundingClientRect(); return dir * (b.top - r.top) > r.height / 2 })
          if (cand.length) { const row = dir > 0 ? Math.min(...cand.map((c) => c.getBoundingClientRect().top)) : Math.max(...cand.map((c) => c.getBoundingClientRect().top)); i = cards.indexOf(cand.filter((c) => Math.abs(c.getBoundingClientRect().top - row) < 4).sort((x, y) => Math.abs(x.getBoundingClientRect().left - r.left) - Math.abs(y.getBoundingClientRect().left - r.left))[0]) }
        }
        cards[i].focus(); cards[i].scrollIntoView({ block: 'nearest' })
        return
      }
      if (!place) return
      if (k === '[' || k === ']' || (e.shiftKey && (k === 'ArrowLeft' || k === 'ArrowRight'))) { e.preventDefault(); hop(k === '[' || k === 'ArrowLeft' ? -1 : 1); return }
      if (k === 'i' || k === 'I') { setInfo((x) => !x); return }
      if ((k === 'e' || k === 'E') && howSteps.length) { setHow(!how); return }
      if (how) {
        const r = k === 'ArrowRight' || k === 'PageDown', l = k === 'ArrowLeft' || k === 'PageUp'
        if (r || l) { e.preventDefault(); setStep(howN + (r ? 1 : -1)) }
        return
      }
      if (ev) { // earthquakes have one pass: no playhead, so these keys do nothing
        if (k === ' ' || k === '0' || /^[1-9]$/.test(k)) { if (k === ' ') e.preventDefault(); return }
        if ((k === 'ArrowRight' || k === 'PageDown' || k === 'ArrowLeft' || k === 'PageUp') && mode !== 'story') { e.preventDefault(); return }
      }
      if (k === ' ') { e.preventDefault(); if (tag === 'BUTTON') e.target.blur(); setPlaying((p) => (!p && index >= nDates - 1 ? (setIndexRaw(startIndex(place)), true) : !p)); return }
      if (/^[1-9]$/.test(k)) { setPlaying(false); setIndex(+k - 1); return }
      if (k === '0') { setPlaying(false); setIndex(nDates - 1); return }
      if (k === 'p') { goPlaces(); return }
      if (k === 'm') { setMode(mode === 'story' ? 'explore' : 'story'); return }
      if (k === 'c' && mode === 'explore') { setCmp((c) => ({ ...c, on: !c.on })); return }
      const right = k === 'ArrowRight' || k === 'PageDown', left = k === 'ArrowLeft' || k === 'PageUp'
      if (!right && !left) return
      e.preventDefault()
      if (mode === 'story') setScene(sceneN + (right ? 1 : -1))
      else { setPlaying(false); setIndex(index + (right ? 1 : -1)) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const lastShown = useRef(null)
  if (place) lastShown.current = place
  const shownPlace = place || places[FLAGSHIP]
  const bounds = (place || lastShown.current || shownPlace)?.bounds || [-180, -60, 180, 75] // while the next place loads, the map stays where it is
  const pass = place ? place.dates[index] : null
  const missing = place && layer && dated && !rawUrl
  const emptyPass = !!place && !ev && layerId === 'water' && place.primary?.kind === 'water' && place.primary.values[index] != null && place.primary.values[index] < 0.5
  const heroPlace = !inPlace

  const globeMode = diving ? diveKeep : globeFull ? 'full' : inPlace && !phone ? 'mini' : mode === 'open' ? 'landing' : 'hidden'
  useEffect(() => { if (globeMode !== 'hidden') setGMounted(true) }, [globeMode])
  const matchIds = useMemo(() => (globeFull && (q || kind !== 'all') ? new Set(filterPlaces(list, q, kind).map((p) => p.id)) : null), [globeFull, list, q, kind])
  const galShown = useMemo(() => (mode === 'places' ? filterPlaces(list, q, kind) : []), [mode, list, q, kind])
  const galIds = useMemo(() => (q || kind !== 'all' ? galShown.map((p) => p.id) : ids), [galShown, q, kind, ids])
  const faceLL = useMemo(() => {
    if (!globeFull || kind === 'all') return null
    const ms = list.filter((p) => classOf(p) === kind).map((p) => centreOf(p.bounds))
    if (!ms.length) return null
    const r = Math.PI / 180, ang = (a, b) => Math.acos(Math.min(1, Math.max(-1, Math.sin(a[1] * r) * Math.sin(b[1] * r) + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.cos((a[0] - b[0]) * r)))) / r
    return ms.map((c) => [c, ms.filter((o) => ang(c, o) < 25).length]).sort((x, y) => y[1] - x[1])[0][0]
  }, [globeFull, kind, list])
  const diveT = useRef([])
  const later = (fn, ms) => diveT.current.push(setTimeout(fn, ms))
  const onDiveStart = (id) => { diveT.current.forEach(clearTimeout); diveT.current = []; setOverlayHold(true); setDiveTo(id) }
  const onDive = (id) => { if (id === placeId && inPlace) { closeGlobe(); return } diveRef.current = true; setDiveKeep(mode === 'open' ? 'landing' : 'full'); setDiving(true); openPlace(id) }
  // After the flight: the globe dissolves while the map underneath comes in (it zooms the last bit), then the picture fades in, then the panel and the corner globe arrive.
  const onDiveEnd = () => {
    if (!diveRef.current) { setDiveTo(null); setOverlayHold(false); return }
    diveRef.current = false
    setPhase('dissolve')
    later(() => setOverlayHold(false), 100)
    later(() => { setDiving(false); setDiveTo(null); setPhase('snap'); later(() => { setPhase('pop'); later(() => setPhase(''), 800) }, 90) }, 560)
  }
  const globeOpenPlace = (id) => { if (id === placeId && inPlace) closeGlobe(); else openPlace(id) }

  if (failed) return <div className="fatal"><h1>Data not available</h1><p>The place list could not be loaded.</p></div>

  return (
    <div className={`app mode-${mode} ${diving || diveTo ? 'diving' : ''} ${phase === 'dissolve' ? 'dv-dissolve' : ''} ${phase ? 'dv-reveal' : ''} ${phone ? 'phone' : ''} ${ev && inPlace ? 'ev' : ''} ${globeFull ? 'globe-on' : ''}`}>
      {(stageIdle || mode !== 'open' || !!diveTo) && <Suspense fallback={null}><MapStage
        bounds={(diveTo && (places[diveTo]?.bounds || stubs[diveTo]?.bounds)) || bounds} hold={overlayHold} url={mode === 'places' || mode === 'globe' || mode === 'open' ? null : url} opacity={opacity} padding={diveTo || diving ? storyPad : inPlace && measured ? measured : padding} camera={camera}
        spots={(ev ? place.spots.filter((s) => s.id !== 'epicentre') : place?.spots) || []} showSpots={!!place && inPlace && spotsOn} activeSpot={activeSpot}
        onSpot={(id) => { if (inPlace) setActiveSpot((a) => (a === id ? null : id)) }}
        interactive={inPlace} star={ev && inPlace && Array.isArray(place.event.lngLat) ? place.event.lngLat : null}
        outline={outlineUrl} starLabel={ev && place.event.type !== 'earthquake' ? 'Event site' : 'Epicentre'}
        ring={ev && inPlace && Array.isArray(place.event.lngLat) && Number(place.event.radius_km) > 0 ? { lngLat: place.event.lngLat, km: Number(place.event.radius_km) } : null}
        compare={{ on: cmpOn, split: cmp.split, url: cmpUrl, tags: cmpTags }} onSplit={(s) => setCmp((c) => ({ ...c, split: s }))}
      /></Suspense>}
      <div className={`veil ${heroPlace ? 'hero' : ''}`} aria-hidden="true" />

      <TopBar view={globeFull ? 'globe' : inPlace ? 'place' : mode === 'places' ? 'places' : mode === 'open' ? 'open' : 'globe'} phone={phone} place={place} placeIndex={Math.max(0, poolIdx)} count={pool0.length} prevName={(places[pool0[(poolIdx - 1 + pool0.length) % (pool0.length || 1)]] || stubs[pool0[(poolIdx - 1 + pool0.length) % (pool0.length || 1)]])?.name} nextName={(places[pool0[(poolIdx + 1) % (pool0.length || 1)]] || stubs[pool0[(poolIdx + 1) % (pool0.length || 1)]])?.name} classLabel={activeClass ? classInfo(activeClass).label : ''} onClearClass={() => go({ cls: '' }, false)}
        mode={mode} from={route.from} places={list} q={q} setQ={setQ} kind={kind} setKind={setKind} inputRef={barInput} globeOpen={globeFull} backText={globeFull && inPlace ? 'Place' : null}
        onHome={goHome} onBack={goBack} onPick={pickFromSearch} onPrev={() => hop(-1)} onNext={() => hop(1)} onRandom={random} onName={focusSearch} onMode={setMode}
        onPlaces={goPlaces} onGlobe={openGlobe} onInfo={() => setInfo((x) => !x)} onSheet={() => setSheet(true)} onSearchSheet={() => setSearchSheet(true)} infoOpen={info} />

      {mode === 'open' && <Home onGlobe={openGlobe} />}
      {mode === 'places' && <S><Gallery places={places} ids={galIds} total={ids.length} current={placeId} onPick={(id) => openPlace(id, 'story', 'places')} onClose={goBack}
        filter={<S><GalleryFilter places={list} shown={galShown} q={q} setQ={setQ} kind={kind} setKind={setKind} onPick={(p) => openPlace(p.id, 'story', 'places')} /></S>} /></S>}
      {!diving && how && place && hstep && <S><Explainer place={place} steps={howSteps} n={howN} index={index} onStep={setStep} onIndex={(i) => { setPlaying(false); setIndex(i) }} onClose={() => setHow(false)} emptyPass={emptyPass} /></S>}
      {!diving && !how && mode === 'story' && place && scene && (
        <S><Story place={place} scenes={scenes} n={sceneN} index={index} playing={playing} setPlaying={setPlaying} phone={phone}
          onScene={setScene} onIndex={(i) => { setPlaying(false); setIndex(i) }} onSheet={() => setSheet(true)}
          onExplore={() => setMode('explore')} onHow={howSteps.length ? () => setHow(true) : null} missing={missing} emptyPass={emptyPass} /></S>
      )}
      {!diving && !how && mode === 'explore' && place && ev && (
        <S><EventExplore place={place} layerId={layerId} layer={layer} setLayer={setLayer} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} setActiveSpot={setActiveSpot} phone={phone} onHow={howSteps.length ? () => setHow(true) : null} /></S>
      )}
      {!diving && !how && mode === 'explore' && place && !ev && (
        <S><Explore key={place.id} place={place} layerId={layerId} layer={layer} setLayer={setLayer} index={index} setIndex={(i) => { setPlaying(false); setIndex(i) }}
          playing={playing} setPlaying={setPlaying} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp} cmpAvailable={cmpAvailable} onHow={howSteps.length ? () => setHow(true) : null}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} activeSpot={activeSpot} setActiveSpot={setActiveSpot} missing={missing} dated={dated} phone={phone}
          water={wc ? { show: waterShow, setShow: setWaterShow, only, blue: wc.blue, green: wc.green } : null} /></S>
      )}
      {!diving && storyLegend && layer && <Legend key={layer.id} layer={layer} phone={phone} strip open0={!!place?.evi && !phone} only={only} water={wc ? { show: waterShow, setShow: setWaterShow, blue: wc.blue, green: wc.green } : null} />}
      {!place && inPlace && <div className="loading" role="status"><img src={`${import.meta.env.BASE_URL}brand/mark.svg`} width="32" height="32" alt="" /><span>Loading…</span></div>}
      {info && inPlace && place && !globeFull && <S><InfoPanel place={place} onClose={() => setInfo(false)} onSheet={() => { setSheet(true) }} /></S>}
      <S><Sheet open={sheet} onClose={() => setSheet(false)} place={place || shownPlace} pass={pass} onAbout={() => { setSheet(false); setAbout(true) }} /></S>
      <S><About open={about} onClose={() => setAbout(false)} /></S>

      {(gMounted || globeMode !== 'hidden') && (
        <Suspense fallback={null}><GlobeMap mode={globeMode} places={list} currentId={inPlace ? placeId : null} matchIds={matchIds} paused={!!matchIds} phase={phase} faceLL={faceLL} faceKey={kind}
          onDiveStart={onDiveStart} onDive={onDive} onDiveEnd={onDiveEnd} divePad={storyPad} onOpen={globeOpenPlace} onExpand={openGlobe} onSelect={setSelected} selectedId={selected} /></Suspense>
      )}
      {globeFull && (
        <S><GlobeUI list={list} q={q} setQ={setQ} kind={kind} setKind={setKind} selected={selected} onSelect={setSelected} onOpen={globeOpenPlace} onRandom={random} onNear={nearMe} near={near}
          inputRef={panelInput} onEscape={closeGlobe} phone={phone} panelOpen={panelOpen} setPanelOpen={setPanelOpen} /></S>
      )}
      {searchSheet && (
        <div className="sheet-back" onClick={() => setSearchSheet(false)}>
          <div className="search-sheet" role="dialog" aria-modal="true" aria-label="Search places" onClick={(e) => e.stopPropagation()}>
            <div className="row"><h2>Find a place</h2><button className="icon-btn" onClick={() => setSearchSheet(false)} aria-label="Close search">×</button></div>
            <SearchPanel places={list} q={q} setQ={setQRaw} kind={kind} setKind={setKind} onPick={pickFromSearch} inputRef={panelInput} autoFocus onEscape={() => setSearchSheet(false)} compactList />
          </div>
        </div>
      )}
    </div>
  )
}
