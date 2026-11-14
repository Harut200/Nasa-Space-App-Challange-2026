import { memo, useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'
import './globe.css'
import { PLANETS } from './planets.js'

maplibregl.setWorkerUrl(workerUrl) // same two lines as MapView; calling twice is fine

const GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf'
const FONT = ['Noto Sans Regular']
const reduced = matchMedia('(prefers-reduced-motion: reduce)') // MediaQueryList.matches is live

const label = (id, filter, size, minzoom = 0) => ({
  id, type: 'symbol', source: 'ofm', 'source-layer': 'place', minzoom, filter,
  layout: { 'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']], 'text-font': FONT, 'text-size': size,
    'text-transform': 'uppercase', 'text-letter-spacing': 0.12 },
  paint: { 'text-color': '#8b8b8b', 'text-halo-color': '#000', 'text-halo-width': 1 },
})

function styleFor(p) {
  const base = {
    version: 8, glyphs: GLYPHS, projection: { type: 'globe' },
    sky: { 'sky-color': '#000', 'horizon-color': p.tint, 'fog-color': '#000', 'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.5, 'fog-ground-blend': 0.9, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.9, 5, 0.4, 7, 0] },
  }
  if (p.tiles) return { ...base,
    sources: { img: { type: 'raster', tiles: [p.tiles], tileSize: 256, maxzoom: p.maxzoom, attribution: p.credit } },
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#000' } },
      { id: 'img', type: 'raster', source: 'img', paint: { 'raster-saturation': -0.15, 'raster-fade-duration': 200 } }] }
  const c = (id, layer, extra) => ({ id, source: 'ofm', 'source-layer': layer, ...extra })
  return { ...base,
    sources: { ofm: { type: 'vector', url: 'https://tiles.openfreemap.org/planet', attribution: p.credit } },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#191919' } },
      c('landcover', 'landcover', { type: 'fill', paint: { 'fill-color': '#1a1a1a' } }),
      c('water', 'water', { type: 'fill', paint: { 'fill-color': '#000' } }),
      c('coast', 'water', { type: 'line', paint: { 'line-color': '#3a3a3a', 'line-width': 0.6, 'line-opacity': 0.6 } }),
      c('border', 'boundary', { type: 'line', filter: ['==', ['get', 'admin_level'], 2],
        paint: { 'line-color': '#5a5a5a', 'line-width': 0.5, 'line-dasharray': [2, 2] } }),
      c('roads', 'transportation', { type: 'line', minzoom: 6, filter: ['in', ['get', 'class'], ['literal', ['motorway', 'trunk', 'primary']]],
        paint: { 'line-color': '#2e2e2e', 'line-width': 0.6 } }),
      label('country', ['==', ['get', 'class'], 'country'], 11),
      label('city', ['in', ['get', 'class'], ['literal', ['city']]], 10, 4),
      label('town', ['in', ['get', 'class'], ['literal', ['town', 'village']]], 9, 8),
    ] }
}

function Globe({ planet = 'earth', regions = [], selectedId = null, onSelect, padding }) {
  const box = useRef(null)
  const mapEl = useRef(null)
  const map = useRef(null)
  const live = useRef({}) // latest props for long-lived listeners
  const pad = padding ?? { top: 0, right: 0, bottom: 0, left: 0 }
  const region = regions.find((r) => r.id === selectedId)
  live.current = { onSelect, selectedId, region, pad, planet }

  // Zoom at which the whole planet is ~97% of the free area (globe diameter = 512 * 2^z / pi px).
  const idleZoom = () => {
    const { clientWidth: w, clientHeight: h } = box.current
    const { top, right, bottom, left } = live.current.pad
    const d = 0.97 * Math.max(120, Math.min(w - left - right, h - top - bottom))
    return Math.log2((Math.PI * d) / 512)
  }
  // Focused: planet diameter 1.5x the longer container side, so it fills the view and the limb runs off-screen.
  const focusZoom = () => Math.log2((Math.PI * 1.5 * Math.max(box.current.clientWidth, box.current.clientHeight)) / 512)
  const goIdle = (animate = true) => {
    const m = map.current
    const o = { center: [m.getCenter().lng, 15], zoom: idleZoom(), pitch: 0, bearing: 0 }
    if (animate) m.flyTo({ ...o, duration: 2200, essential: true })
    else m.jumpTo(o)
  }

  // Create the map once.
  useEffect(() => {
    const p = PLANETS.find((x) => x.id === live.current.planet) ?? PLANETS[0]
    const m = new maplibregl.Map({
      container: mapEl.current, style: styleFor(p), center: [20, 15], zoom: 1, maxPitch: 0, dragRotate: false,
      attributionControl: { compact: true }, pixelRatio: Math.min(devicePixelRatio, 2), scrollZoom: false, // 3x screens: 2.25x fewer pixels, no visible loss // ponytail: wheel zoom is opt-in, see below
    })
    map.current = m
    m.setPadding(live.current.pad)
    m.jumpTo({ zoom: idleZoom() })

    // Scroll-zoom only after the globe is clicked/focused; leaving it gives page scroll back.
    m.getCanvas().tabIndex = 0
    m.on('click', () => m.scrollZoom.enable())
    const leave = () => m.scrollZoom.disable()
    box.current.addEventListener('mouseleave', leave)

    // Auto-rotate while idle; pause 4 s after any interaction.
    let last = performance.now(), touched = 0, raf
    const touch = () => { touched = performance.now() }
    for (const ev of ['mousedown', 'touchstart', 'wheel', 'keydown']) m.getCanvas().addEventListener(ev, touch, { passive: true })
    const resync = () => { last = performance.now() } // no rotation jump after a pause
    document.addEventListener('visibilitychange', resync)
    const tick = (t) => {
      const dt = t - last
      last = t
      if (!live.current.selectedId && !reduced.matches && t - touched > 4000 && !m.isMoving() && m.loaded()) {
        const c = m.getCenter()
        m.jumpTo({ center: [c.lng + dt * 0.006, c.lat] })
      }
      raf = requestAnimationFrame(tick)
    }
    // Rotate only while on screen (the observer's first callback starts the loop).
    const io = new IntersectionObserver(([e]) => {
      cancelAnimationFrame(raf)
      if (e.isIntersecting) { resync(); raf = requestAnimationFrame(tick) }
    })
    io.observe(box.current)

    // Click on empty globe (not a drag) deselects; zooming far out deselects.
    m.on('click', () => live.current.selectedId && live.current.onSelect?.(null))
    m.on('zoomend', (e) => {
      const r = live.current.region
      if (e.originalEvent && r && m.getZoom() < (r.zoom ?? focusZoom()) - 1.5) live.current.onSelect?.(null)
    })

    const ro = new ResizeObserver(() => { m.resize(); if (!live.current.selectedId) goIdle(false) })
    ro.observe(box.current)
    const el = box.current
    return () => {
      cancelAnimationFrame(raf); io.disconnect(); ro.disconnect(); document.removeEventListener('visibilitychange', resync)
      el.removeEventListener('mouseleave', leave)
      for (const ev of ['mousedown', 'touchstart', 'wheel', 'keydown']) m.getCanvas().removeEventListener(ev, touch)
      m.remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Padding or selection changed: fly to the region, or back to idle.
  const padKey = Object.values(pad).join()
  const regionKey = region ? `${region.lngLat}|${region.zoom}` : ''
  useEffect(() => {
    const m = map.current
    m.setPadding(pad)
    if (region) m.flyTo({ center: region.lngLat, zoom: region.zoom ?? focusZoom(), pitch: 0, bearing: 0, duration: 2800, curve: 1.6, essential: true })
    else goIdle(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionKey, padKey])

  // Planet switch: fade out, swap style, reset view, fade in.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    const m = map.current, p = PLANETS.find((x) => x.id === planet) ?? PLANETS[0]
    box.current.classList.add('switching')
    const t = setTimeout(() => {
      m.setStyle(styleFor(p))
      goIdle(false)
      setTimeout(() => box.current?.classList.remove('switching'), 150)
    }, 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planet])

  // Pins (Earth only), built per regions/planet; selection updates them in place. MapLibre fades far-side markers (0 is treated as default 0.2, so 0.01).
  const pins = useRef([])
  useEffect(() => {
    if (planet !== 'earth') return
    const markers = regions.map((r) => {
      const b = document.createElement('button')
      b.className = 'pin'
      b.dataset.severity = r.severity ?? 'none'
      b.setAttribute('aria-label', r.name)
      b.setAttribute('aria-pressed', 'false')
      b.onclick = (e) => { e.stopPropagation(); live.current.onSelect?.(r.id) }
      return new maplibregl.Marker({ element: b, occludedOpacity: 0.01 }).setLngLat(r.lngLat).addTo(map.current)
    })
    pins.current = markers.map((k) => k.getElement())
    return () => { markers.forEach((k) => k.remove()); pins.current = [] }
  }, [regions, planet])
  useEffect(() => {
    pins.current.forEach((b, i) => {
      const r = regions[i], on = r.id === selectedId
      if (b.getAttribute('aria-pressed') === String(on)) return
      b.setAttribute('aria-pressed', String(on))
      b.innerHTML = on ? `<span>${r.name.replace(/[<&]/g, '')}</span>` : ''
    })
  }, [regions, planet, selectedId])

  return (
    <div className="globe" ref={box}>
      <div className="globe-map" ref={mapEl} />
    </div>
  )
}

export default memo(Globe)
