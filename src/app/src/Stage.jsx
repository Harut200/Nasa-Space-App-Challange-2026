import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { styleFor, IMAGERY_SOURCES } from './basemap.js'
import { placeStyle, placeRadius, DOT_COLOR, SHAPE_CLASSES, DATA, coords } from './meta.js'
import { frame } from './data.js'
import { withoutGrey, GREY_BACKGROUND } from './transparent.js'

maplibregl.setWorkerUrl(workerUrl)

const STILL = new URLSearchParams(location.search).has('still') // ?still: no auto-rotation (screenshots, demo recording)
const reduced = matchMedia('(prefers-reduced-motion: reduce)')
const wide = () => innerWidth >= 900
const FLY = (t) => t * t * (3 - 2 * t) // one calm ease for every flight (in and out)
// Dots shrink when the globe is small, so neighbouring places (Tonle Sap / Mekong, Sevan / Ararat) stay apart. `extra` adds a ring offset.
const scaled = (extra = 0) => ['interpolate', ['linear'], ['zoom'], 1, ['+', ['max', 3.4, ['*', ['get', 'r'], 0.45]], extra * 0.6], 4, ['+', ['get', 'r'], extra]]
const features = (places) => ({ type: 'FeatureCollection', features: places.map((p) => ({ type: 'Feature', properties: { id: p.id, r: placeRadius(p), kind: placeStyle(p) }, geometry: { type: 'Point', coordinates: p.lngLat } })) })
// Shaped dots (fire, volcano, landslide, other classes) are small white-and-colour glyphs: an SDF image painted in the class colour.
function glyph(shape) {
  const n = 64, c = document.createElement('canvas')
  c.width = c.height = n
  const x = c.getContext('2d'), h = n / 2
  x.fillStyle = '#fff'
  x.beginPath()
  const pts = { diamond: [[h, 3], [n - 3, h], [h, n - 3], [3, h]], triangle: [[h, 4], [n - 3, n - 8], [3, n - 8]], square: [[8, 8], [n - 8, 8], [n - 8, n - 8], [8, n - 8]], hexagon: [[h, 3], [n - 6, h / 2 + 8], [n - 6, h + h / 2 - 8], [h, n - 3], [6, h + h / 2 - 8], [6, h / 2 + 8]] }[shape]
  pts.forEach(([a, b], i) => (i ? x.lineTo(a, b) : x.moveTo(a, b)))
  x.closePath(); x.fill()
  return x.getImageData(0, 0, n, n)
}
const SHAPES = ['diamond', 'triangle', 'square', 'hexagon']
// Glyph size follows the dot radius at each zoom (the glyph fills about 56 of its 64 px).
const glyphSize = (extra = 0) => ['interpolate', ['linear'], ['zoom'], 1, ['/', ['+', ['*', ['max', 3.4, ['*', ['get', 'r'], 0.45]], 2.3], extra * 1.2], 56], 4, ['/', ['+', ['*', ['get', 'r'], 2.3], extra * 1.2], 56]]
const HIT = 16 // px: how far from a dot's centre a click still counts

// The one map of the app: a globe when zoomed out, the place when zoomed in, our radar frames draped on top.
export default function Stage({ mode, places, view, manifest, layer, date, opacity, spots, epicentre, colors, onOpen, onHover, onSelect, target, onMap, onImagery }) {
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
  const globePad = () => ({ top: 0, left: 0, right: 0, bottom: wide() ? 150 : 270 })
  const placeCamera = (m, b) => {
    const phone = !wide()
    const two = (manifest?.event?.views?.length || 0) > 1
    // On a phone an earthquake's headline card and before/after bar are taller than a season's, so the map keeps clear of them.
    // On a desktop the earthquake's headline card (left) and layer card (right) are wide, so the image keeps clear of both.
    const padding = phone ? (manifest?.event ? { top: two ? 356 : 304, bottom: two ? 300 : 260, left: 24, right: 24 } : { top: 130, bottom: 230, left: 24, right: 24 }) : manifest?.event ? { top: 96, bottom: 220, left: 480, right: 360 } : { top: 110, bottom: 190, left: 60, right: 60 }
    return m.cameraForBounds([[b[0], b[1]], [b[2], b[3]]], { padding })
  }

  // Create the map once.
  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current, style: styleFor(mode), center: [88, 24], zoom: 1.5, maxPitch: 0, dragRotate: false, pixelRatio: Math.min(devicePixelRatio, 2),
      attributionControl: { compact: true }, scrollZoom: true, fadeDuration: 0,
    })
    map.current = m
    if (STILL) window.__map = m // test and screenshot hook (only with ?still)
    // On a phone the open attribution text would sit on top of the timeline; start it collapsed (the (i) button opens it).
    if (!wide()) m.once('load', () => el.current?.querySelector('.maplibregl-ctrl-attrib')?.removeAttribute('open'))
    m.touchZoomRotate.disableRotation()
    m.jumpTo({ zoom: idleZoom(), padding: globePad() })
    onMap?.(m)
    let wired = false
    m.on('style.load', () => {
      m.addSource('places', { type: 'geojson', data: features(live.current.places) })
      for (const sh of SHAPES) if (!m.hasImage(`g-${sh}`)) m.addImage(`g-${sh}`, glyph(sh), { sdf: true })
      const color = ['match', ['get', 'kind'], 'big', DOT_COLOR.big, 'some', DOT_COLOR.some, 'quake', DOT_COLOR.quake, 'fire', DOT_COLOR.fire, 'volcano', DOT_COLOR.volcano, 'landslide', DOT_COLOR.landslide, 'other', DOT_COLOR.other, DOT_COLOR.land]
      const shaped = ['in', ['get', 'kind'], ['literal', SHAPE_CLASSES]]
      const shapeOf = ['match', ['get', 'kind'], 'fire', 'g-diamond', 'volcano', 'g-triangle', 'landslide', 'g-square', 'g-hexagon']
      const vis = live.current.view === 'globe' ? 'visible' : 'none'
      // Earthquakes get a pulsing ring so they read differently from water places even at a glance.
      m.addLayer({ id: 'quake-ring', type: 'circle', source: 'places', filter: ['==', ['get', 'kind'], 'quake'], layout: { visibility: vis }, paint: { 'circle-radius': scaled(6), 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': DOT_COLOR.quake, 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.8 } })
      m.addLayer({ id: 'quake-static', type: 'circle', source: 'places', filter: ['==', ['get', 'kind'], 'quake'], layout: { visibility: vis }, paint: { 'circle-radius': scaled(4), 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': 0.9 } })
      m.addLayer({ id: 'halo', type: 'circle', source: 'places', layout: { visibility: vis, 'circle-sort-key': ['-', 0, ['get', 'r']] }, paint: { 'circle-radius': scaled(8), 'circle-color': color, 'circle-opacity': 0.22, 'circle-blur': 0.7 } })
      m.addLayer({ id: 'dots', type: 'circle', source: 'places', layout: { visibility: vis, 'circle-sort-key': ['-', 0, ['get', 'r']] }, paint: { 'circle-radius': scaled(), 'circle-color': color, 'circle-opacity': ['case', shaped, 0, 0.95], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': ['case', shaped, 0, 1] } })
      // A white outline glyph under, the coloured glyph over; the invisible circle above still takes the hover and click.
      m.addLayer({ id: 'shape-edge', type: 'symbol', source: 'places', filter: shaped, layout: { visibility: vis, 'icon-image': shapeOf, 'icon-size': glyphSize(3), 'icon-allow-overlap': true, 'icon-ignore-placement': true }, paint: { 'icon-color': '#ffffff' } })
      m.addLayer({ id: 'shape', type: 'symbol', source: 'places', filter: shaped, layout: { visibility: vis, 'icon-image': shapeOf, 'icon-size': glyphSize(), 'icon-allow-overlap': true, 'icon-ignore-placement': true }, paint: { 'icon-color': color } })
      m.addLayer({ id: 'hover-ring', type: 'circle', source: 'places', filter: ['==', ['get', 'id'], ''], layout: { visibility: vis }, paint: { 'circle-radius': scaled(4), 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.95 } })
      if (!wired) {
        wired = true
        // Hit test: the place whose centre is nearest to the pointer, not whichever circle happens to be drawn on top.
        const pick = (pt) => {
          const fs = m.queryRenderedFeatures([[pt.x - HIT, pt.y - HIT], [pt.x + HIT, pt.y + HIT]], { layers: ['dots'] })
          let best = null, bd = HIT
          for (const f of new Set(fs.map((x) => x.properties.id))) {
            const p = live.current.places.find((x) => x.id === f)
            if (!p) continue
            const c = m.project(p.lngLat), d = Math.hypot(c.x - pt.x, c.y - pt.y)
            if (d <= bd) { bd = d; best = p.id }
          }
          return best
        }
        m.on('mousemove', (e) => {
          if (live.current.view !== 'globe') return
          const id = pick(e.point)
          m.getCanvas().style.cursor = id ? 'pointer' : ''
          live.current.hover = !!id
          if (m.getLayer('hover-ring')) m.setFilter('hover-ring', ['==', ['get', 'id'], id || ''])
          live.current.onHover(id ? { id, x: e.point.x, y: e.point.y } : null)
        })
        m.getCanvas().addEventListener('mouseleave', () => { live.current.hover = false; m.getCanvas().style.cursor = ''; if (m.getLayer('hover-ring')) m.setFilter('hover-ring', ['==', ['get', 'id'], '']); live.current.onHover(null) })
        m.on('click', (e) => {
          if (live.current.view !== 'globe') return
          const id = pick(e.point)
          if (!id) return live.current.onSelect(null)
          if (matchMedia('(hover: none)').matches) live.current.onSelect(id); else live.current.onOpen(id)
        })
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
      if (live.current.view === 'globe' && m.getLayer('quake-ring') && !reduced.matches) {
        const k = 1 - (1 - (t % 2600) / 2600) ** 2 // a slow breath: the ring opens quickly, then fades as it widens
        m.setPaintProperty('quake-ring', 'circle-radius', scaled(4 + 10 * k))
        m.setPaintProperty('quake-ring', 'circle-stroke-opacity', 0.85 * (1 - k))
      }
      if (!STILL && !reduced.matches && live.current.view === 'globe' && !live.current.hover && t - touched > 4000 && !m.isMoving()) {
        const c = m.getCenter()
        m.jumpTo({ center: [c.lng + dt * 0.0028, c.lat] })
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
      if (cam) { if (jump) m.jumpTo(cam); else m.flyTo({ ...cam, duration: reduced.matches ? 0 : 2800, curve: 1.5, easing: FLY, essential: true }) }
    } else if (view === 'globe') {
      const c = m.getCenter()
      const o = { center: [c.lng, 24], zoom: idleZoom(), padding: globePad() }
      if (jump) m.jumpTo(o); else m.flyTo({ ...o, duration: reduced.matches ? 0 : 2600, curve: 1.5, easing: FLY, essential: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, manifest?.id])

  // Dots follow the data: the places can gain their numbers after the map has started.
  const placesKey = places.map((p) => `${p.id}:${placeStyle(p)}:${Math.round(placeRadius(p) * 10)}`).join('|')
  useEffect(() => {
    const src = styleVer && map.current.getSource('places')
    if (src) src.setData(features(live.current.places))
  }, [placesKey, styleVer])

  // Dots only on the globe.
  useEffect(() => {
    const m = map.current
    if (!styleVer) return
    for (const id of ['halo', 'dots', 'shape-edge', 'shape', 'quake-ring', 'quake-static', 'hover-ring']) m.setLayoutProperty(id, 'visibility', view === 'globe' ? 'visible' : 'none')
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

  // An earthquake's epicentre: a star that is always visible in the place view.
  useEffect(() => {
    const m = map.current
    if (view !== 'place' || !epicentre) return
    const el = document.createElement('div')
    el.className = 'epicentre'
    el.setAttribute('aria-hidden', 'true')
    el.textContent = '★'
    const k = new maplibregl.Marker({ element: el }).setLngLat(epicentre).setPopup(new maplibregl.Popup({ offset: 16 }).setText('USGS epicentre')).addTo(m)
    return () => k.remove()
  }, [view, epicentre, styleVer])

  useEffect(() => { first.current = false }, [])

  return (
    <div className="stage-map" ref={box}>
      <div className="stage-canvas" ref={el} />
    </div>
  )
}
