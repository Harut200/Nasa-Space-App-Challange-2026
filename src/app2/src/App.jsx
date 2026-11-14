import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FLAGSHIP, fmtDate, frameUrl, isDated, loadIndex, loadPlace } from './data.js'
import { useRoute } from './route.js'
import { buildScenes } from './scenes.js'
import MapStage from './MapStage.jsx'
import Opening from './Opening.jsx'
import Explore from './Explore.jsx'
import Story from './Story.jsx'
import Gallery from './Gallery.jsx'
import Sheet from './Sheet.jsx'
import { Icon, reducedMotion } from './ui.jsx'

const useWidth = () => {
  const [w, setW] = useState(window.innerWidth)
  useEffect(() => { const f = () => setW(window.innerWidth); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f) }, [])
  return w
}

export default function App() {
  const [route, go] = useRoute()
  const [ids, setIds] = useState([])
  const [places, setPlaces] = useState({}) // id -> built place
  const [failed, setFailed] = useState(false)
  const [index, setIndexRaw] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [opacity, setOpacity] = useState(0.92)
  const [cmp, setCmp] = useState(() => ({ on: /[?&]cmp=1/.test(location.hash) && /mode=explore/.test(location.hash), split: 50 }))
  const [spotsOn, setSpotsOn] = useState(true)
  const [activeSpot, setActiveSpot] = useState(null)
  const [sheet, setSheet] = useState(() => /[?&]sheet=1/.test(location.hash))
  const width = useWidth()
  const phone = width < 760

  // index and places
  useEffect(() => { loadIndex().then(setIds).catch(() => setFailed(true)) }, [])
  const ensure = useCallback((id) => loadPlace(id).then((p) => setPlaces((c) => (c[id] ? c : { ...c, [id]: p }))).catch(() => {}), [])
  useEffect(() => { ensure(route.place || FLAGSHIP) }, [route.place, ensure])
  useEffect(() => { // everything else in the background, one at a time
    let dead = false
    ;(async () => { for (const id of ids) { if (dead) return; await ensure(id) } })()
    return () => { dead = true }
  }, [ids, ensure])

  const placeId = route.place && ids.includes(route.place) ? route.place : route.place ? null : FLAGSHIP
  const place = placeId ? places[placeId] : null
  const unknown = route.place && ids.length && !ids.includes(route.place)
  useEffect(() => { if (unknown) go({ place: null, mode: 'open' }, false) }, [unknown, go])

  const mode = route.mode
  const scenes = useMemo(() => (place ? buildScenes(place) : []), [place])
  const sceneN = Math.min(Math.max(1, route.scene || 1), scenes.length || 1)
  const scene = mode === 'story' ? scenes[sceneN - 1] : null

  const nDates = place?.dates.length || 1
  const setIndex = useCallback((i) => setIndexRaw(Math.max(0, Math.min(nDates - 1, i))), [nDates])

  // Layer on show
  const layerId = mode === 'story' ? scene?.layer : mode === 'explore' ? (route.layer && place?.layers.some((l) => l.id === route.layer) ? route.layer : 'water') : 'water'
  const layer = place?.layers.find((l) => l.id === layerId)

  // Reset the playhead when place / scene / mode changes
  const sceneKey = `${placeId}|${mode}|${mode === 'story' ? sceneN : ''}`
  const firstApply = useRef(true)
  useEffect(() => {
    if (!place) return
    setActiveSpot(null)
    const keepCmp = firstApply.current && !!route.cmp
    if (mode === 'story' && scene) {
      const dated = route.date && place.dates.includes(route.date) && firstApply.current ? place.dates.indexOf(route.date) : null
      setIndexRaw(dated ?? (scene.at ?? 0))
      setPlaying(!!scene.play && !reducedMotion() && dated == null)
      if (scene.compare) setCmp((c) => ({ ...c, on: true })); else setCmp((c) => ({ ...c, on: false }))
    } else if (mode === 'explore') {
      const di = route.date ? place.dates.indexOf(route.date) : -1
      setIndexRaw(di >= 0 ? di : place.dates.length - 1)
      setPlaying(false)
      setCmp((c) => ({ ...c, on: keepCmp }))
    } else if (mode === 'open') {
      setIndexRaw(0); setPlaying(!reducedMotion()); setCmp((c) => ({ ...c, on: false }))
    }
    firstApply.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneKey, place])

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
    if (mode === 'explore' && place) {
      const d = place.dates[index]
      if (route.date !== d) go({ date: d }, false)
    }
  }, [index, mode, place]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode === 'explore' && place && !!route.cmp !== cmp.on) go({ cmp: cmp.on }, false)
  }, [cmp.on, mode, place]) // eslint-disable-next-line react-hooks/exhaustive-deps

  const date = place?.dates[index]
  const url = place && layer ? frameUrl(place, layerId, date) : null
  const dated = place ? isDated(place, layerId) : false
  const firstOfLayer = layer?.dates?.[0]
  const cmpAvailable = !!(layer?.dates && layer.dates.length > 1)
  const cmpOn = cmp.on && cmpAvailable && mode !== 'open'
  const cmpUrl = cmpOn ? frameUrl(place, layerId, firstOfLayer) : null

  // Camera
  const focusSpot = scene?.focus ? place?.spots.find((s) => s.id === scene.focus) : null
  const padding = mode === 'open' || mode === 'places' ? { top: 60, bottom: 60, left: 40, right: 40 }
    : phone ? { top: 70, left: 20, right: 20, bottom: mode === 'story' ? 360 : 300 }
    : mode === 'story' ? { top: 80, bottom: 70, left: 520, right: 60 }
    : { top: 80, bottom: 340, left: 330, right: activeSpot ? 340 : 40 }
  const camera = { key: focusSpot ? focusSpot.id : mode === 'places' ? 'wide' : 'fit', focus: focusSpot?.lngLat, zoomBoost: 1.25 }

  // Actions
  const openPlace = (id, m = 'story') => go({ place: id, mode: m, scene: 1, layer: null, date: null })
  const setScene = (n) => go({ scene: Math.max(1, Math.min(scenes.length, n)), date: null }, false)
  const setLayer = (l) => go({ layer: l }, false)

  // Keyboard
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag = e.target.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape') { if (sheet) setSheet(false); return }
      if (sheet) return
      const k = e.key
      if (k === '?' || k === 'h') { setSheet(true); return }
      if (mode === 'open') { if (k === 'Enter') { openPlace(FLAGSHIP) } else if (k === 'p') go({ place: null, mode: 'places' }); return }
      if (mode === 'places') { if (k === 'Escape') go({ place: null, mode: 'open' }); return }
      if (!place) return
      if (k === ' ' ) { e.preventDefault(); if (tag === 'BUTTON') e.target.blur(); setPlaying((p) => (!p && index >= nDates - 1 ? (setIndexRaw(0), true) : !p)); return }
      if (/^[1-9]$/.test(k)) { setPlaying(false); setIndex(+k - 1); return }
      if (k === '0') { setPlaying(false); setIndex(nDates - 1); return }
      if (k === 'p') { go({ place: null, mode: 'places' }); return }
      if (k === 'm') { go({ mode: mode === 'story' ? 'explore' : 'story', scene: 1, layer: null, date: null }); return }
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

  const bounds = (place || places[FLAGSHIP])?.bounds || [-180, -60, 180, 75]
  const shownPlace = place || places[FLAGSHIP]
  const pass = place ? place.dates[index] : null
  const missing = place && layer && dated && !url
  const heroPlace = mode === 'open' || mode === 'places'

  if (failed) return <div className="fatal"><h1>Data not available</h1><p>The place list could not be loaded.</p></div>

  return (
    <div className={`app mode-${mode} ${phone ? 'phone' : ''}`}>
      <MapStage
        bounds={bounds} url={mode === 'places' ? null : url} opacity={opacity} padding={padding} camera={camera}
        spots={place?.spots || []} showSpots={!!place && !heroPlace && spotsOn} activeSpot={activeSpot}
        onSpot={(id) => { if (mode === 'explore' || mode === 'story') setActiveSpot((a) => (a === id ? null : id)) }}
        interactive={!heroPlace}
        compare={{ on: cmpOn, split: cmp.split, url: cmpUrl }} onSplit={(s) => setCmp((c) => ({ ...c, split: s }))}
      />
      <div className={`veil ${heroPlace ? 'hero' : ''}`} aria-hidden="true" />

      <header className="topbar">
        <button className="brand" onClick={() => go({ place: null, mode: 'open' })} aria-label="Dancing with the SARs, back to the opening">
          <span className="brand-mark" aria-hidden="true" /> <span>Dancing <em>with the SARs</em></span>
        </button>
        {!heroPlace && place && (
          <nav className="crumbs" aria-label="Place and view">
            <button className="chip place-chip" onClick={() => go({ place: null, mode: 'places' })} title="All places (P)">{Icon.grid}<span>{place.name}</span></button>
            <div className="seg" role="tablist" aria-label="View">
              <button role="tab" aria-selected={mode === 'story'} className={mode === 'story' ? 'on' : ''} onClick={() => go({ mode: 'story', scene: 1, layer: null, date: null })}>Story</button>
              <button role="tab" aria-selected={mode === 'explore'} className={mode === 'explore' ? 'on' : ''} onClick={() => go({ mode: 'explore', layer: null, date: null })}>Explore</button>
            </div>
          </nav>
        )}
        <div className="spacer" />
        <button className="chip" onClick={() => setSheet(true)} title="How sure are we? (?)">{Icon.info}<span>How sure are we?</span></button>
      </header>

      {mode === 'open' && <Opening place={shownPlace} index={index} onWatch={() => openPlace(FLAGSHIP)} onChoose={() => go({ place: null, mode: 'places' })} />}
      {mode === 'places' && <Gallery places={places} ids={ids} current={placeId} onPick={(id) => openPlace(id)} onClose={() => go({ place: null, mode: 'open' })} />}
      {mode === 'story' && place && scene && (
        <Story place={place} scenes={scenes} n={sceneN} index={index} playing={playing} setPlaying={setPlaying} phone={phone}
          onScene={setScene} onIndex={(i) => { setPlaying(false); setIndex(i) }} onSheet={() => setSheet(true)}
          onExplore={() => go({ mode: 'explore', layer: null, date: null })} missing={missing} />
      )}
      {mode === 'explore' && place && (
        <Explore place={place} layerId={layerId} layer={layer} setLayer={setLayer} index={index} setIndex={(i) => { setPlaying(false); setIndex(i) }}
          playing={playing} setPlaying={setPlaying} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp} cmpAvailable={cmpAvailable}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} activeSpot={activeSpot} setActiveSpot={setActiveSpot} missing={missing} dated={dated} phone={phone} />
      )}
      {cmpOn && <div className="cmp-tags" aria-hidden="true"><span>First pass · {firstOfLayer && fmtDate(firstOfLayer)}</span><span className="r">{date && fmtDate(date)}</span></div>}
      {mode !== 'places' && !place && mode !== 'open' && <div className="loading" role="status">Loading…</div>}
      <Sheet open={sheet} onClose={() => setSheet(false)} place={place || shownPlace} pass={pass} />
      {mode !== 'open' && mode !== 'places' && <div className="keys-hint" aria-hidden="true">{mode === 'story' ? '← → scenes' : '← → passes'} · space play · 1–9 pass · ? caveats</div>}
    </div>
  )
}
