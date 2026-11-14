import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { getJson, peakWater, centre, nearestPlace, distanceKm, parseHash, km, words, dayMonth, defaultLayer, currentTheme, basemapMode, DATA } from './meta.js'
import { nearest, frame } from './data.js'
import { Landing, PlaceUI, COLORS } from './Panels.jsx'
import About from './About.jsx'
import MapView from './MapView.jsx'
import { Divider, useExtent } from './Compare.jsx'

const Stage = lazy(() => import('./Stage.jsx'))
const reduced = matchMedia('(prefers-reduced-motion: reduce)')
const initial = (m, link, theme) => {
  const idx = link.date ? m.dates.indexOf(nearest(m.dates, link.date)) : 0 // a date a place does not have snaps to its nearest pass
  return { id: m.id, dateIndex: idx, layerId: m.layers.some((l) => l.id === link.layer) ? link.layer : defaultLayer(m), playing: false, touched: !!link.date,
    compare: link.compare, otherIndex: 0, split: link.split, opacity: theme === 'earth' ? 0.9 : 1, sheet: null, list: false, layersOpen: innerWidth >= 900 }
}

export default function App() {
  const [manifests, setManifests] = useState(null)
  const [series, setSeries] = useState({})
  const [error, setError] = useState(null)
  const [route, setRoute] = useState(parseHash)
  const [selected, setSelected] = useState(null)
  const [target, setTarget] = useState(null)
  const [tip, setTip] = useState(null)
  const [near, setNear] = useState(null)
  const [aboutOpen, setAboutOpen] = useState(() => parseHash().about)
  const [theme, setTheme] = useState(currentTheme)
  const [imagery, setImagery] = useState(true)
  const [v, setV] = useState(null)
  const [maps, setMaps] = useState({})

  useEffect(() => {
    getJson('index.json').then((idx) => Promise.all(idx.areas.map((a) => getJson(`${a}/manifest.json`)))).then(setManifests).catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    if (!manifests) return
    Promise.all(manifests.map((m) => getJson(`${m.id}/timeseries.json`).catch(() => null).then((ts) => [m.id, ts]))).then((l) => setSeries(Object.fromEntries(l)))
  }, [manifests])
  useEffect(() => {
    const on = () => setRoute(parseHash())
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])

  const places = useMemo(() => (manifests || []).map((m) => ({ id: m.id, name: m.name, lngLat: centre(m.bounds), peak: peakWater(series[m.id]) })), [manifests, series])
  const go = useCallback((id) => { setTip(null); setSelected(null); location.hash = id ? `#/${id}` : '#/' }, [])
  const placeId = manifests?.some((m) => m.id === route.id) ? route.id : null
  const m = manifests?.find((x) => x.id === placeId)

  // New place: fresh viewer state (set during render, the React-sanctioned way to reset state on a prop change).
  if (manifests && (placeId ? v?.id !== placeId : v)) setV(placeId ? initial(m, route, theme) : null)
  const patch = useCallback((p) => setV((s) => (s ? { ...s, ...p } : s)), [])

  const layer = m && v ? m.layers.find((l) => l.id === v.layerId) || m.layers[0] : null
  const date = m && v ? m.dates[v.dateIndex] : null
  const n = m?.dates.length || 0
  // Compare only makes sense on a dated layer: on a whole-season layer both sides would be the same image.
  const cmp = !!(v?.compare && layer?.dates)
  const extent = useExtent(cmp ? maps.a : null, m?.bounds)

  // Play: a step every 1.1 s, once through, then stop on the last pass.
  useEffect(() => {
    if (!v?.playing || n < 2) return
    const t = setInterval(() => setV((s) => (s ? (s.dateIndex >= n - 1 ? { ...s, playing: false } : { ...s, dateIndex: s.dateIndex + 1 }) : s)), 1100)
    return () => clearInterval(t)
  }, [v?.playing, n])
  // On entering a place (without a date in the link) play once after the fly-in, unless the person already touched something.
  useEffect(() => {
    if (!placeId || route.date || reduced.matches) return
    const t = setTimeout(() => setV((s) => (s && s.id === placeId && !s.touched ? { ...s, playing: true } : s)), 3000)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placeId])
  useEffect(() => {
    if (v && m && layer) history.replaceState(null, '', `${location.pathname}${location.search}#/${m.id}?${new URLSearchParams({ layer: layer.id, date })}`)
  }, [v?.id, v?.layerId, v?.dateIndex]) // eslint-disable-line react-hooks/exhaustive-deps

  // Compare: keep the two cameras identical.
  useEffect(() => {
    const { a, b } = maps
    if (!a || !b) return
    const follow = (from, to) => () => {
      if (to._syncing) return
      from._syncing = true
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() })
      from._syncing = false
    }
    const ab = follow(a, b), ba = follow(b, a)
    a.on('move', ab); b.on('move', ba); ab()
    return () => { a.off('move', ab); b.off('move', ba) }
  }, [maps])
  const setA = useCallback((x) => setMaps((s) => ({ ...s, a: x })), [setMaps])
  const setB = useCallback((x) => setMaps((s) => ({ ...s, b: x })), [setMaps])

  if (error) return <p role="alert" className="splash">Could not load the data: {error}. Reload the page to try again.</p>
  if (!manifests) return <p className="splash"><span className="spin" /> Loading</p>

  const random = () => {
    const pool = places.filter((p) => p.id !== placeId)
    const p = pool[Math.floor(Math.random() * pool.length)]
    if (placeId) go(p.id); else setTarget({ id: p.id, n: Date.now() })
  }
  const nearMe = () => {
    if (!navigator.geolocation) return setNear('Your browser cannot share a location.')
    setNear('Finding the nearest place we cover…')
    navigator.geolocation.getCurrentPosition((pos) => {
      const here = [pos.coords.longitude, pos.coords.latitude]
      const p = nearestPlace(places, here)
      setNear(`Nearest place we cover: ${p.name}, ${km(distanceKm(here, p.lngLat))} km away.`)
      setTarget({ id: p.id, n: Date.now() })
    }, () => setNear('Location was not shared, so we cannot find the nearest place. Pick a dot instead.'), { timeout: 8000 })
  }

  const shown = m && layer?.dates ? frame(DATA, m.id, layer, date).shown : null
  const other = m && v ? (layer?.dates ? words(frame(DATA, m.id, layer, m.dates[v.otherIndex]).shown) : 'whole season') : null
  const note = shown && shown !== date ? `No “${layer.label}” image for ${words(date)}. Showing the nearest one, ${words(shown)}.` : null
  const mode = basemapMode(theme)

  return (
    <div className={`app view-${placeId ? 'place' : 'globe'}`}>
      <Suspense fallback={<p className="splash"><span className="spin" /> Loading</p>}>
        <Stage mode={mode} places={places} view={placeId ? 'place' : 'globe'} manifest={m} layer={layer} date={date} opacity={v?.opacity ?? 1} spots={m?.spots} colors={COLORS}
          onOpen={go} onHover={setTip} onSelect={setSelected} target={target} onMap={setA} onImagery={setImagery} />
      </Suspense>
      {cmp && m && layer && extent && (
        <MapView mode={mode} bounds={m.bounds} url={frame(DATA, m.id, layer, m.dates[v.otherIndex]).url} opacity={v.opacity} onMap={setB}
          style={{ clipPath: `inset(${extent.top}px 0 ${innerHeight - extent.top - extent.height}px ${extent.left + (extent.width * v.split) / 100}px)` }} />
      )}
      {cmp && extent && <Divider rect={extent} split={v.split} onSplit={(s) => patch({ split: s })} left={dayMonth(shown)} right={dayMonth(frame(DATA, m.id, layer, m.dates[v.otherIndex]).shown)} />}
      {!imagery && mode === 'natural' && <p className="glass imagery-note" role="status">Satellite imagery unavailable. Showing the radar data only.</p>}
      {placeId && v ? (
        <PlaceUI m={m} ts={series[m.id]} v={v} patch={patch} places={places} layer={layer} date={date} preview={m.source === 'preview'} otherDates={other} note={note}
          onBack={() => go(null)} onPlace={go} onRandom={random} onAbout={() => setAboutOpen(true)} />
      ) : (
        <Landing places={places} tip={tip} selected={selected} onOpen={go} onRandom={random} onNear={nearMe} near={near} onAbout={() => setAboutOpen(true)} />
      )}
      <About open={aboutOpen} onClose={() => setAboutOpen(false)} manifest={m} theme={theme} onTheme={setTheme} />
    </div>
  )
}
