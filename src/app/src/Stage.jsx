import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { styleFor, IMAGERY_SOURCES } from './basemap.js'
import { dotKind, dotRadius, DOT_COLOR, DATA, coords } from './meta.js'
import { frame } from './data.js'
import { withoutGrey, GREY_BACKGROUND } from './transparent.js'

maplibregl.setWorkerUrl(workerUrl)

const STILL = new URLSearchParams(location.search).has('still') // ?still: no auto-rotation (screenshots, demo recording)
const reduced = matchMedia('(prefers-reduced-motion: reduce)')
const wide = () => innerWidth >= 900

// The one map of the app: a globe when zoomed out, the place when zoomed in, our radar frames draped on top.
export default function Stage({ mode, places, view, manifest, layer, date, opacity, spots, colors, onOpen, onHover, onSelect, target, onMap, onImagery }) {
  const box = useRef(null)
  const el = useRef(null)
  const map = useRef(null)
  const live = useRef({})
  const [styleVer, setStyleVer] = useState(0)
  const first = useRef(true)
  const radar = useRef({ ids: [], shown: null })
  const bounds = manifest?.bounds
  live.current = { places, view, onOpen, onHover, onSelect, opacity, onImagery }

  const idleZoom = () => {
    const { clientWidth: w, clientHeight: h } = box.current
    return Math.log2((Math.PI * 0.94 * Math.max(160, Math.min(w, h - globePad().bottom))) / 512)
  }
  // Keep the globe clear of the start card at the bottom.
  const globePad = () => ({ top: 0, left: 0, right: 0, bottom: wide() ? 210 : 270 })
  const placeCamera = (m, b) => {
    const phone = !wide()
    const padding = phone ? { top: 130, bottom: 230, left: 24, right: 24 } : { top: 110, bottom: 190, left: 60, right: 60 }
    return m.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding })
  }

  // Create the map once.
  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current, style: styleFor(mode), center: [75, 18], zoom: 1.5, maxPitch: 0, dragRotate: false, pixelRatio: Math.min(devicePixelRatio, 2),
      attributionControl: { compact: true }, scrollZoom: true, fadeDuration: 0,
    })
    map.current = m
    m.touchZoomRotate.disableRotation()
    m.jumpTo({ zoom: idleZoom(), padding: globePad() })
    onMap?.(m)
    let wired = false
    m.on('style.load', () => {
      m.addSource('places', { type: 'geojson', data: { type: 'FeatureCollection', features: live.current.places.map((p) => ({ type: 'Feature', properties: { id: p.id, r: dotRadius(p.peak), kind: dotKind(p.peak) }, geometry: { type: 'Point', coordinates: p.lngLat } })) } })
      const color = ['match', ['get', 'kind'], 'big', DOT_COLOR.big, 'some', DOT_COLOR.some, DOT_COLOR.land]
      const vis = live.current.view === 'globe' ? 'visible' : 'none'
      m.addLayer({ id: 'halo', type: 'circle', source: 'places', layout: { visibility: vis }, paint: { 'circle-radius': ['+', ['get', 'r'], 8], 'circle-color': color, 'circle-opacity': 0.25, 'circle-blur': 0.7 } })
      m.addLayer({ id: 'dots', type: 'circle', source: 'places', layout: { visibility: vis }, paint: { 'circle-radius': ['get', 'r'], 'circle-color': color, 'circle-opacity': 0.95, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } })
      if (!wired) {
        wired = true
        m.on('mousemove', 'dots', (e) => { m.getCanvas().style.cursor = 'pointer'; live.current.hover = true; live.current.onHover({ id: e.features[0].properties.id, x: e.point.x, y: e.point.y }) })
        m.on('mouseleave', 'dots', () => { m.getCanvas().style.cursor = ''; live.current.hover = false; live.current.onHover(null) })
        m.on('click', 'dots', (e) => {
          e.originalEvent.stopPropagation()
          const id = e.features[0].properties.id
          if (matchMedia('(hover: none)').matches) live.current.onSelect(id); else live.current.onOpen(id)
        })
        m.on('click', (e) => { if (live.current.view === 'globe' && !m.queryRenderedFeatures(e.point, { layers: ['dots'] }).length) live.current.onSelect(null) })
      }
      radar.current = { ids: [], shown: null }
      setStyleVer((v) => v + 1)
    })
    // Satellite tiles failing is not fatal: the radar still shows on a dark ground.
    let fails = 0
    m.on('error', (e) => {
      if (IMAGERY_SOURCES.includes(e.sourceId) && ++fails === 4) live.current.onImagery?.(false)
    })
    m.on('sourcedata', (e) => { if (IMAGERY_SOURCES.includes(e.sourceId) && e.tile && fails < 4) live.current.onImagery?.(true) })

    // Slow rotation until the user touches the globe; it pauses 4 s after any interaction.
    let last = performance.now(), touched = 0, raf
    const touch = () => { touched = performance.now() }
    for (const ev of ['mousedown', 'touchstart', 'wheel']) m.getCanvas().addEventListener(ev, touch, { passive: true })
    const tick = (t) => {
      const dt = Math.min(t - last, 100)
      last = t
      if (!STILL && !reduced.matches && live.current.view === 'globe' && !live.current.hover && t - touched > 4000 && !m.isMoving()) {
        const c = m.getCenter()
        m.jumpTo({ center: [c.lng + dt * 0.005, c.lat] })
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    const ro = new ResizeObserver(() => { m.resize() })
    ro.observe(box.current)
    return () => { cancelAnimationFrame(raf); ro.disconnect(); onMap?.(null); m.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Look changed (theme): swap the basemap; style.load re-adds our layers.
  useEffect(() => {
    if (first.current) return
    map.current.setStyle(styleFor(mode))
  }, [mode])

  // Globe <-> place camera.
  useEffect(() => {
    const m = map.current
    const jump = first.current
    if (view === 'place' && bounds) {
      m.setPadding({ top: 0, bottom: 0, left: 0, right: 0 })
      const cam = placeCamera(m, bounds)
      if (cam) { if (jump) m.jumpTo(cam); else m.flyTo({ ...cam, duration: reduced.matches ? 0 : 3200, curve: 1.6, essential: true }) }
    } else if (view === 'globe') {
      const c = m.getCenter()
      const o = { center: [c.lng, 15], zoom: idleZoom(), padding: globePad() }
      if (jump) m.jumpTo(o); else m.flyTo({ ...o, duration: reduced.matches ? 0 : 2600, curve: 1.4, essential: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, manifest?.id])

  // Dots only on the globe.
  useEffect(() => {
    const m = map.current
    if (!styleVer) return
    for (const id of ['halo', 'dots']) m.setLayoutProperty(id, 'visibility', view === 'globe' ? 'visible' : 'none')
  }, [view, styleVer])

  // Random / Near me: fly to a place and open it.
  useEffect(() => {
    const p = target && live.current.places.find((x) => x.id === target.id)
    if (p) live.current.onOpen(p.id)
  }, [target])

  // Radar: every frame of the layer is its own image layer; changing the date cross-fades them, so the water appears to spread.
  const lkey = manifest && layer ? `${manifest.id}/${layer.id}` : ''
  useEffect(() => {
    const m = map.current
    if (!styleVer) return
    for (const id of radar.current.ids) { if (m.getLayer(id)) m.removeLayer(id); if (m.getSource(id)) m.removeSource(id) }
    radar.current = { ids: [], shown: null }
    if (!manifest || !layer) return
    const dates = layer.dates || [null]
    let alive = true
    const urls = dates.map((d) => frame(DATA, manifest.id, layer, d || undefined).url)
    Promise.all(GREY_BACKGROUND.includes(layer.id) ? urls.map(withoutGrey) : urls).then((us) => {
      if (!alive || !m.getStyle()) return
      const ids = dates.map((d, i) => {
        const id = `radar-${d || 'all'}`
        m.addSource(id, { type: 'image', url: us[i], coordinates: coords(manifest.bounds) })
        m.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': 0, 'raster-opacity-transition': { duration: 800, delay: 0 }, 'raster-fade-duration': 0 } })
        return id
      })
      radar.current = { ids, dates }
      show()
    })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lkey, styleVer])
  const show = () => {
    const m = map.current, r = radar.current
    if (!r.ids.length) return
    const want = r.dates[0] === null ? r.ids[0] : r.ids[Math.max(0, r.dates.indexOf(live.current.shownDate))]
    for (const id of r.ids) if (m.getLayer(id)) m.setPaintProperty(id, 'raster-opacity', id === want ? live.current.opacity : 0)
  }
  const shownDate = layer?.dates ? (layer.dates.includes(date) ? date : layer.dates.reduce((a, b) => (Math.abs(Date.parse(b) - Date.parse(date)) < Math.abs(Date.parse(a) - Date.parse(date)) ? b : a))) : null
  live.current.shownDate = shownDate
  useEffect(show, [shownDate, opacity, lkey, styleVer]) // eslint-disable-line react-hooks/exhaustive-deps

  // Spot markers (the places we graph) only inside a place.
  useEffect(() => {
    const m = map.current
    if (view !== 'place' || !spots?.length) return
    const markers = spots.map((s, i) => new maplibregl.Marker({ color: colors[i % colors.length], scale: 0.8 }).setLngLat(s.lngLat).setPopup(new maplibregl.Popup({ offset: 24 }).setText(s.label)).addTo(m))
    return () => markers.forEach((k) => k.remove())
  }, [view, spots, colors, styleVer])

  useEffect(() => { first.current = false }, [])

  return (
    <div className="stage-map" ref={box}>
      <div className="stage-canvas" ref={el} />
    </div>
  )
}
