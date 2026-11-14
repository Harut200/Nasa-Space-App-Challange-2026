import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FLAGSHIP, centreOf, defaultLayer, fmtDate, frameUrl, isDated, isEvent, loadIndex, loadPlace, startIndex } from './data.js'
import { useRoute } from './route.js'
import { buildScenes } from './scenes.js'
import { hasWaterClasses, onlyClass, waterClasses } from './water.js'
const MapStage = lazy(() => import('./MapStage.jsx')) // MapLibre loads after the home page is interactive
const GlobeMap = lazy(() => import('./GlobeMap.jsx'))
import GlobeUI from './GlobeUI.jsx'
import Home from './Home.jsx'
import TopBar from './TopBar.jsx'
import InfoPanel from './InfoPanel.jsx'
import About from './About.jsx'
import Explore from './Explore.jsx'
import { EventExplore } from './EventParts.jsx'
import Story from './Story.jsx'
import Gallery from './Gallery.jsx'
import GalleryFilter from './GalleryFilter.jsx'
import Sheet from './Sheet.jsx'
import Legend from './Legend.jsx'
import { SearchPanel, filterPlaces } from './Search.jsx'
import { KeyHint, reducedMotion } from './ui.jsx'

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
  return r.color === color ? r.out : null
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
  const [kind, setKind] = useState('all')
  const [selected, setSelected] = useState(null)
  const [near, setNear] = useState(null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [waterShow, setWaterShow] = useState('both')
  const [gMounted, setGMounted] = useState(false)
  const { w: width, h: height } = useSize()
  const phone = width < 760
  const barInput = useRef(null), panelInput = useRef(null)

  // index and places: the one on show first, then the rest three at a time
  useEffect(() => { loadIndex().then(setIds).catch(() => setFailed(true)) }, [])
  const ensure = useCallback((id) => loadPlace(id).then((p) => setPlaces((c) => (c[id] ? c : { ...c, [id]: p }))).catch(() => {}), [])
  useEffect(() => { ensure(route.place || FLAGSHIP) }, [route.place, ensure])
  useEffect(() => {
    let dead = false
    ;(async () => {
      for (let i = 0; i < ids.length; i += 3) { if (dead) return; await Promise.all(ids.slice(i, i + 3).map(ensure)) }
    })()
    return () => { dead = true }
  }, [ids, ensure])
  const list = useMemo(() => ids.map((id) => places[id]).filter(Boolean), [ids, places])

  const placeId = route.place && ids.includes(route.place) ? route.place : route.place ? null : FLAGSHIP
  const place = placeId ? places[placeId] : null
  const unknown = route.place && ids.length && !ids.includes(route.place)
  useEffect(() => { if (unknown) go({ place: null, mode: 'open' }, false) }, [unknown, go])

  const mode = route.mode // open | globe | places | story | explore
  const inPlace = mode === 'story' || mode === 'explore'
  const globeFull = mode === 'globe' || (inPlace && !!route.globe)
  const scenes = useMemo(() => (place ? buildScenes(place) : []), [place])
  const sceneN = Math.min(Math.max(1, route.scene || 1), scenes.length || 1)
  const scene = mode === 'story' ? scenes[sceneN - 1] : null

  const ev = isEvent(place)
  const nDates = place?.dates.length || 1
  const index = Math.min(indexState, nDates - 1) // a new place can have fewer passes than the one just left
  const setIndex = useCallback((i) => setIndexRaw(Math.max(0, Math.min(nDates - 1, i))), [nDates])

  // Layer on show
  const layerId = mode === 'story' ? scene?.layer : mode === 'explore' ? (route.layer && place?.layers.some((l) => l.id === route.layer) ? route.layer : place ? defaultLayer(place) : 'water') : 'water'
  const layer = place?.layers.find((l) => l.id === layerId)

  // Reset the playhead when place / scene / mode changes
  const sceneKey = `${placeId}|${mode}|${mode === 'story' ? sceneN : ''}`
  const firstApply = useRef(true)
  const applying = useRef(null) // explore playhead index just applied from the URL / defaults; the URL sync waits for it
  useEffect(() => {
    if (!place) return
    setActiveSpot(null); setWaterShow('both')
    const keepCmp = firstApply.current && !!route.cmp
    if (mode === 'story' && scene) {
      const dated = route.date && place.dates.includes(route.date) && firstApply.current ? place.dates.indexOf(route.date) : null
      setIndexRaw(dated ?? (scene.at ?? 0))
      setPlaying(!!scene.play && !reducedMotion() && dated == null && !isEvent(place))
      if (scene.compare) setCmp((c) => ({ ...c, on: true })); else setCmp((c) => ({ ...c, on: false }))
    } else if (mode === 'explore') {
      const di = route.date ? place.dates.indexOf(route.date) : -1
      applying.current = di >= 0 ? di : Math.max(0, place.dates.length - 1)
      setIndexRaw(applying.current)
      setPlaying(false)
      setCmp((c) => ({ ...c, on: keepCmp }))
    } else if (mode === 'open') {
      setIndexRaw(0); setPlaying(!reducedMotion()); setCmp((c) => ({ ...c, on: false }))
    } else { setPlaying(false); setCmp((c) => ({ ...c, on: false })) }
    firstApply.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneKey, place])
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

  const date = place?.dates[index]
  const rawUrl = place && layer ? frameUrl(place, layerId, date) : null
  const dated = place ? isDated(place, layerId) : false
  const firstOfLayer = layer?.dates?.[0]
  // seasons: first pass vs now. Earthquakes: the layer vs bare terrain (explore) or ascending vs descending (story)
  const cmpAvailable = ev ? !!layer : !!(layer?.dates && layer.dates.length > 1)
  const cmpOn = cmp.on && cmpAvailable && mode !== 'open'
  const cmpLayerId = ev ? (mode === 'story' ? scene?.cmpLayer : null) : layerId
  const rawCmp = cmpOn && cmpLayerId ? frameUrl(place, cmpLayerId, firstOfLayer) : null

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

  const cmpTags = !cmpOn ? null : ev
    ? (scene?.cmpTags || ['Bare terrain', layer?.label.split(/ \(|:/)[0] || 'Layer'])
    : [`${layer?.id === 'coh' ? 'First pair' : 'First pass'} · ${firstOfLayer ? fmtDate(firstOfLayer) : ''}`, date ? fmtDate(date) : '']

  // Camera: the free area is what the panels, the timeline and the legend leave. Fit the whole radar footprint into it.
  const focusSpot = scene?.focus ? place?.spots.find((s) => s.id === scene.focus) : scene?.focusLL ? { id: 'epicentre', lngLat: scene.focusLL } : null
  const dockH = Math.min(290, Math.round(height * (height <= 800 ? 0.36 : 0.38)))
  const storyLegend = mode === 'story' && !!layer?.legend && (!ev || !!scene?.legend)
  const padding = mode === 'places' || mode === 'globe' ? { top: 60, bottom: 60, left: 40, right: 40 }
    : mode === 'open' ? (phone ? { top: 150, bottom: 400, left: 20, right: 20 } : { top: 72, bottom: height <= 760 ? 350 : 385, left: Math.round(width * 0.46), right: 40 })
    : phone ? { top: mode === 'story' ? 116 : 250, left: 20, right: 20, bottom: mode === 'story' ? 380 : 300 }
    : mode === 'story' ? { top: 74, bottom: storyLegend ? 198 : 70, left: 524, right: 44 }
    : { top: 74, bottom: dockH + 44, left: 330, right: 360 }
  const camera = { key: focusSpot ? focusSpot.id : 'fit', focus: focusSpot?.lngLat, zoomBoost: 1.25 }

  // ---- navigation ----
  const origin = inPlace ? (route.globe ? 'globe' : route.from) : mode === 'globe' ? 'globe' : mode === 'places' ? 'places' : 'home'
  const openPlace = useCallback((id, m = 'story', from = origin) => { setInfo(false); setSelected(null); go({ place: id, mode: m, scene: 1, layer: null, date: null, cmp: false, from, globe: false, q: '' }) }, [go, origin])
  const goHome = () => { setInfo(false); setSelected(null); go({ place: null, mode: 'open', q: '', globe: false }) }
  const goPlaces = () => { setInfo(false); go({ place: null, mode: 'places', globe: false, q: '' }) }
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
  const hop = (d) => {
    if (!ids.length) return
    const i = ids.indexOf(placeId)
    openPlace(ids[(i + d + ids.length) % ids.length], inPlace ? mode : 'story')
  }
  const random = () => {
    const pool = ids.filter((id) => id !== (inPlace ? placeId : null))
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
  const setMode = (m) => go(m === 'story' ? { mode: 'story', scene: 1, layer: null, date: null } : { mode: 'explore', layer: null, date: null })
  const setQ = useCallback((v) => { setQRaw(v); if (mode === 'globe' || mode === 'places') go({ q: v }, false) }, [mode, go])
  useEffect(() => { if (mode === 'globe' || mode === 'places') setQRaw(route.q || '') }, [route.q, mode])
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

  const shownPlace = place || places[FLAGSHIP]
  const bounds = shownPlace?.bounds || [-180, -60, 180, 75]
  const pass = place ? place.dates[index] : null
  const missing = place && layer && dated && !rawUrl
  const heroPlace = !inPlace
  const quakeId = ids.find((id) => places[id] && isEvent(places[id]) && places[id].event.type === 'earthquake') || ids.find((id) => places[id] && isEvent(places[id])) || null
  const quakePick = ids.includes('tibet-m70-2026') ? 'tibet-m70-2026' : quakeId

  const globeMode = globeFull ? 'full' : inPlace && !phone ? 'mini' : 'hidden'
  useEffect(() => { if (globeMode !== 'hidden') setGMounted(true) }, [globeMode])
  const matchIds = useMemo(() => (globeFull && (q || kind !== 'all') ? new Set(filterPlaces(list, q, kind).map((p) => p.id)) : null), [globeFull, list, q, kind])
  const galShown = useMemo(() => (mode === 'places' ? filterPlaces(list, q, kind) : []), [mode, list, q, kind])
  const galIds = useMemo(() => (q || kind !== 'all' ? galShown.map((p) => p.id) : ids), [galShown, q, kind, ids])
  const globeOpenPlace = (id) => { if (id === placeId && inPlace) closeGlobe(); else openPlace(id) }

  if (failed) return <div className="fatal"><h1>Data not available</h1><p>The place list could not be loaded.</p></div>

  return (
    <div className={`app mode-${mode} ${phone ? 'phone' : ''} ${ev && inPlace ? 'ev' : ''} ${globeFull ? 'globe-on' : ''}`}>
      <Suspense fallback={null}><MapStage
        bounds={bounds} url={mode === 'places' || mode === 'globe' ? null : url} opacity={opacity} padding={padding} camera={camera}
        spots={(ev ? place.spots.filter((s) => s.id !== 'epicentre') : place?.spots) || []} showSpots={!!place && inPlace && spotsOn} activeSpot={activeSpot}
        onSpot={(id) => { if (inPlace) setActiveSpot((a) => (a === id ? null : id)) }}
        interactive={inPlace} star={ev && inPlace && Array.isArray(place.event.lngLat) ? place.event.lngLat : null}
        compare={{ on: cmpOn, split: cmp.split, url: cmpUrl, tags: cmpTags }} onSplit={(s) => setCmp((c) => ({ ...c, split: s }))}
      /></Suspense>
      <div className={`veil ${heroPlace ? 'hero' : ''}`} aria-hidden="true" />

      <TopBar view={globeFull ? 'globe' : inPlace ? 'place' : mode === 'places' ? 'places' : mode === 'open' ? 'open' : 'globe'} phone={phone} place={place} placeIndex={ids.indexOf(placeId)} count={ids.length}
        mode={mode} from={route.from} places={list} q={q} setQ={setQ} kind={kind} setKind={setKind} inputRef={barInput} globeOpen={globeFull} backText={globeFull && inPlace ? 'Place' : null}
        onHome={goHome} onBack={goBack} onPick={pickFromSearch} onPrev={() => hop(-1)} onNext={() => hop(1)} onRandom={random} onName={focusSearch} onMode={setMode}
        onPlaces={goPlaces} onGlobe={openGlobe} onInfo={() => setInfo((x) => !x)} onSheet={() => setSheet(true)} onAbout={() => setAbout(true)} onSearchSheet={() => setSearchSheet(true)} infoOpen={info} />

      {mode === 'open' && <Home place={shownPlace} index={index} places={places} ids={ids} onWatch={() => openPlace(FLAGSHIP, 'story', 'home')} onChoose={goPlaces} onGlobe={openGlobe} onFeel={quakePick ? () => openPlace(quakePick, 'story', 'home') : null} onRandom={random} onPick={(id) => openPlace(id, 'story', 'home')} />}
      {mode === 'places' && <Gallery places={places} ids={galIds} total={ids.length} current={placeId} onPick={(id) => openPlace(id, 'story', 'places')} onClose={goBack}
        filter={<GalleryFilter places={list} shown={galShown} q={q} setQ={setQ} kind={kind} setKind={setKind} onPick={(p) => openPlace(p.id, 'story', 'places')} />} />}
      {mode === 'story' && place && scene && (
        <Story place={place} scenes={scenes} n={sceneN} index={index} playing={playing} setPlaying={setPlaying} phone={phone}
          onScene={setScene} onIndex={(i) => { setPlaying(false); setIndex(i) }} onSheet={() => setSheet(true)}
          onExplore={() => setMode('explore')} missing={missing} />
      )}
      {mode === 'explore' && place && ev && (
        <EventExplore place={place} layerId={layerId} layer={layer} setLayer={setLayer} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} setActiveSpot={setActiveSpot} phone={phone} />
      )}
      {mode === 'explore' && place && !ev && (
        <Explore place={place} layerId={layerId} layer={layer} setLayer={setLayer} index={index} setIndex={(i) => { setPlaying(false); setIndex(i) }}
          playing={playing} setPlaying={setPlaying} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp} cmpAvailable={cmpAvailable}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} activeSpot={activeSpot} setActiveSpot={setActiveSpot} missing={missing} dated={dated} phone={phone}
          water={wc ? { show: waterShow, setShow: setWaterShow, only } : null} />
      )}
      {storyLegend && layer && <Legend key={layer.id} layer={layer} phone={phone} strip only={only} water={wc ? { show: waterShow, setShow: setWaterShow } : null} />}
      {!place && inPlace && <div className="loading" role="status">Loading…</div>}
      {info && inPlace && place && !globeFull && <InfoPanel place={place} onClose={() => setInfo(false)} onSheet={() => { setSheet(true) }} />}
      <Sheet open={sheet} onClose={() => setSheet(false)} place={place || shownPlace} pass={pass} />
      <About open={about} onClose={() => setAbout(false)} />
      {inPlace && !phone && !globeFull && <KeyHint text={`${ev ? (mode === 'story' ? '← → scenes · M explore · P places · ? caveats' : 'C compare · M story · P places · ? caveats') : (mode === 'story' ? '← → scenes · Space play · M explore · P places · ? caveats' : '← → passes · Space play · C compare · M story · P places · ? caveats')} · [ ] other place · R random · G globe · / search`} />}

      {(gMounted || globeMode !== 'hidden') && (
        <Suspense fallback={null}><GlobeMap mode={globeMode} places={list} currentId={inPlace ? placeId : null} matchIds={matchIds} paused={!!matchIds}
          onOpen={globeOpenPlace} onExpand={openGlobe} onSelect={setSelected} selectedId={selected} /></Suspense>
      )}
      {globeFull && (
        <GlobeUI list={list} q={q} setQ={setQ} kind={kind} setKind={setKind} selected={selected} onSelect={setSelected} onOpen={globeOpenPlace} onRandom={random} onNear={nearMe} near={near}
          inputRef={panelInput} onEscape={closeGlobe} phone={phone} panelOpen={panelOpen} setPanelOpen={setPanelOpen} />
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
