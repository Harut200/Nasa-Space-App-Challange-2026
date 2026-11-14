import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FLAGSHIP, defaultLayer, firstLabel, fmtDate, frameUrl, isDated, isEvent, isQuake, loadIndex, loadPlace, loadSummary, startIndex } from './data.js'
import { useRoute } from './route.js'
import { buildScenes } from './scenes.js'
import MapStage from './MapStage.jsx'
import Opening from './Opening.jsx'
import Explore from './Explore.jsx'
import { EventExplore } from './EventParts.jsx'
import Story from './Story.jsx'
import Gallery from './Gallery.jsx'
import Sheet from './Sheet.jsx'
import Legend from './Legend.jsx'
import { Icon, KeyHint, reducedMotion } from './ui.jsx'

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
  const [summary, setSummary] = useState(null) // summary.json: classes, counts, thumbnails (null until it arrives, or if it is missing)
  const [indexState, setIndexRaw] = useState(0)
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
  useEffect(() => { loadSummary().then((j) => setSummary(j && Array.isArray(j.places) ? j : null)) }, [])
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

  const ev = isEvent(place) // an earthquake: one before/after pair, no dated passes
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
    setActiveSpot(null)
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
  const url = place && layer ? frameUrl(place, layerId, date) : null
  const dated = place ? isDated(place, layerId) : false
  const firstOfLayer = layer?.dates?.[0]
  // seasons: first pass vs now. Earthquakes: the layer vs bare terrain (explore) or ascending vs descending (story)
  const cmpAvailable = ev ? !!layer : !!(layer?.dates && layer.dates.length > 1)
  const cmpOn = cmp.on && cmpAvailable && mode !== 'open'
  const cmpLayerId = ev ? (mode === 'story' ? scene?.cmpLayer : null) : layerId
  const cmpUrl = cmpOn && cmpLayerId ? frameUrl(place, cmpLayerId, firstOfLayer) : null

  const cmpTags = !cmpOn ? null : ev
    ? (scene?.cmpTags || ['Bare terrain', layer?.label.split(/ \(|:/)[0] || 'Layer'])
    : [`${firstLabel(place, layer)} · ${firstOfLayer ? fmtDate(firstOfLayer) : ''}`, date ? fmtDate(date) : '']

  // Camera
  const focusSpot = scene?.focus ? place?.spots.find((s) => s.id === scene.focus) : scene?.focusLL ? { id: 'epicentre', lngLat: scene.focusLL } : null
  const padding = mode === 'places' ? { top: 60, bottom: 60, left: 40, right: 40 }
    : mode === 'open' ? (phone ? { top: 90, bottom: 430, left: 24, right: 24 } : { top: 76, bottom: 150, left: Math.round(width * 0.46), right: 36 })
    : phone ? { top: mode === 'story' ? 70 : 200, left: 20, right: 20, bottom: mode === 'story' ? 380 : 300 }
    : mode === 'story' ? { top: 80, bottom: 70, left: 520, right: 60 }
    : { top: 80, bottom: 340, left: 330, right: 340 }
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
      if (e.key === 'Escape') { if (sheet) setSheet(false); else if (mode === 'places') go({ place: null, mode: 'open' }); return }
      if (sheet) return
      const k = e.key
      if (k === '?' || k === 'h') { setSheet(true); return }
      if (mode === 'open') { if (k === 'Enter') { openPlace(FLAGSHIP) } else if (k === 'p') go({ place: null, mode: 'places' }); return }
      if (mode === 'places') { if (k === 'Escape') go({ place: null, mode: 'open' }); return }
      if (!place) return
      if (ev) { // earthquakes have one pass: no playhead, so these keys do nothing
        if (k === ' ' || k === '0' || /^[1-9]$/.test(k)) { if (k === ' ') e.preventDefault(); return }
        if ((k === 'ArrowRight' || k === 'PageDown' || k === 'ArrowLeft' || k === 'PageUp') && mode !== 'story') { e.preventDefault(); return }
      }
      if (k === ' ' ) { e.preventDefault(); if (tag === 'BUTTON') e.target.blur(); setPlaying((p) => (!p && index >= nDates - 1 ? (setIndexRaw(startIndex(place)), true) : !p)); return }
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
  const quakeId = ids.includes('tibet-m70-2026') ? 'tibet-m70-2026' : ids.find((id) => places[id] && isEvent(places[id])) || null

  if (failed) return <div className="fatal"><h1>Data not available</h1><p>The place list could not be loaded.</p></div>

  return (
    <div className={`app mode-${mode} ${phone ? 'phone' : ''} ${ev && !heroPlace ? 'ev' : ''}`}>
      <MapStage
        bounds={bounds} url={mode === 'places' ? null : url} opacity={opacity} padding={padding} camera={camera}
        spots={(ev ? place.spots.filter((s) => s.id !== 'epicentre' && s.lngLat.join() !== place.event.lngLat?.join()) : place?.spots) || []} showSpots={!!place && !heroPlace && spotsOn} activeSpot={activeSpot}
        onSpot={(id) => { if (mode === 'explore' || mode === 'story') setActiveSpot((a) => (a === id ? null : id)) }}
        interactive={!heroPlace} star={ev && !heroPlace ? place.event.lngLat : null} starLabel={ev && !isQuake(place) ? 'Event site' : 'Epicentre'}
        compare={{ on: cmpOn, split: cmp.split, url: cmpUrl, tags: cmpTags }} onSplit={(s) => setCmp((c) => ({ ...c, split: s }))}
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

      {mode === 'open' && <Opening place={shownPlace} summary={summary} total={ids.length} index={index} onWatch={() => openPlace(FLAGSHIP)} onFeel={quakeId ? () => openPlace(quakeId) : null} onChoose={() => go({ place: null, mode: 'places' })} />}
      {mode === 'places' && <Gallery places={places} ids={ids} summary={summary} current={placeId} onPick={(id) => openPlace(id)} onClose={() => go({ place: null, mode: 'open' })} />}
      {mode === 'story' && place && scene && (
        <Story key={place.id} place={place} scenes={scenes} n={sceneN} index={index} playing={playing} setPlaying={setPlaying} phone={phone}
          onScene={setScene} onIndex={(i) => { setPlaying(false); setIndex(i) }} onSheet={() => setSheet(true)}
          onExplore={() => go({ mode: 'explore', layer: null, date: null })} missing={missing} />
      )}
      {mode === 'explore' && place && ev && (
        <EventExplore key={place.id} place={place} layerId={layerId} layer={layer} setLayer={setLayer} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} setActiveSpot={setActiveSpot} phone={phone} />
      )}
      {mode === 'explore' && place && !ev && (
        <Explore key={place.id} place={place} layerId={layerId} layer={layer} setLayer={setLayer} index={index} setIndex={(i) => { setPlaying(false); setIndex(i) }}
          playing={playing} setPlaying={setPlaying} opacity={opacity} setOpacity={setOpacity} cmp={cmp} setCmp={setCmp} cmpAvailable={cmpAvailable}
          spotsOn={spotsOn} setSpotsOn={setSpotsOn} activeSpot={activeSpot} setActiveSpot={setActiveSpot} missing={missing} dated={dated} phone={phone} />
      )}
      {mode === 'story' && ev && scene?.legend && layer && <Legend key={layer.id} layer={layer} phone={phone} />}
      {mode !== 'places' && !place && mode !== 'open' && <div className="loading" role="status">Loading…</div>}
      <Sheet open={sheet} onClose={() => setSheet(false)} place={place || shownPlace} pass={pass} />
      {mode !== 'open' && mode !== 'places' && !phone && <KeyHint text={ev ? (mode === 'story' ? '← → scenes · M explore · P places · ? caveats' : 'C compare · M story · P places · ? caveats') : (mode === 'story' ? '← → scenes · Space play · M explore · P places · ? caveats' : '← → passes · Space play · C compare · M story · P places · ? caveats')} />}
    </div>
  )
}
