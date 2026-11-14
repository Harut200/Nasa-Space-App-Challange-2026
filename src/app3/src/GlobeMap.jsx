import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { centreOf, classInfo, classOf, dotSize, headline, isEvent, placeColor } from './data.js'

maplibregl.setWorkerUrl(workerUrl)

const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'
const GIBS_PLACE = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg' // the place map asks for the same tiles under another name
const EOX = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg'
const STILL = new URLSearchParams(location.search).has('still') // ?still: no auto-rotation, and the map is reachable as window.__globe (screenshots, tests)
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const FLY = (t) => t * t * (3 - 2 * t)
const BIG = (m) => m === 'full' || m === 'landing'
const HIT = 16 // px: how far from a dot's centre a click still counts
const SAME = 6 // px: dots this close are the same spot on the globe; a click zooms in first so they come apart

const style = () => ({
  version: 8, projection: { type: 'globe' },
  sky: { 'sky-color': '#06080c', 'horizon-color': '#3a6fb0', 'fog-color': '#06080c', 'sky-horizon-blend': 0.7, 'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.8, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 5, 0.5, 8, 0] },
  sources: {
    bm: { type: 'raster', tiles: [GIBS], tileSize: 256, maxzoom: 8, attribution: 'Imagery: NASA GIBS · Blue Marble' },
    s2: { type: 'raster', tiles: [EOX], tileSize: 256, maxzoom: 13, attribution: 'Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)' },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#06080c' } },
    { id: 'bm', type: 'raster', source: 'bm', paint: { 'raster-fade-duration': 300 } },
    { id: 's2', type: 'raster', source: 's2', minzoom: 4, paint: { 'raster-fade-duration': 300, 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0, 6, 1] } },
  ],
})

// One 64 px picture per event shape and colour; the symbol layer draws them. Unknown event types get the diamond.
function drawIcon(shape, color) {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const x = c.getContext('2d')
  x.lineJoin = 'round'
  const path = () => {
    x.beginPath()
    if (shape === 'ring' || shape === 'dot') x.arc(32, 32, 15, 0, 7)
    else if (shape === 'triangle') { x.moveTo(32, 10); x.lineTo(55, 52); x.lineTo(9, 52); x.closePath() }
    else if (shape === 'square') x.roundRect(12, 12, 40, 40, 6)
    else if (shape === 'flame') { x.moveTo(32, 6); x.bezierCurveTo(34, 18, 52, 26, 52, 42); x.arc(32, 42, 20, 0, Math.PI, false); x.bezierCurveTo(12, 34, 20, 30, 24, 22); x.bezierCurveTo(26, 28, 29, 29, 32, 30); x.bezierCurveTo(26, 22, 30, 14, 32, 6) }
    else if (shape === 'drop') { x.moveTo(32, 8); x.bezierCurveTo(26, 22, 14, 30, 14, 41); x.arc(32, 41, 18, Math.PI, 0, true); x.bezierCurveTo(50, 30, 38, 22, 32, 8) }
    else { x.moveTo(32, 7); x.lineTo(57, 32); x.lineTo(32, 57); x.lineTo(7, 32); x.closePath() }
  }
  if (shape === 'ring') { x.beginPath(); x.arc(32, 32, 28, 0, 7); x.strokeStyle = color; x.globalAlpha = 0.85; x.lineWidth = 4; x.stroke(); x.globalAlpha = 1 }
  path(); x.fillStyle = color; x.fill()
  x.lineWidth = 5; x.strokeStyle = 'rgba(0,0,0,.55)'; x.stroke()
  x.lineWidth = 3; x.strokeStyle = '#fff'; x.stroke()
  return x.getImageData(0, 0, 64, 64)
}

const features = (places, ids, currentId) => ({
  type: 'FeatureCollection',
  features: places.map((p) => {
    const ev = isEvent(p), t = classInfo(classOf(p))
    return { type: 'Feature', properties: { id: p.id, kind: ev ? 'e' : 's', r: dotSize(p), s: dotSize(p) / 10.5, color: placeColor(p), icon: `ev-${t.shape}-${placeColor(p)}`, dim: ids && !ids.has(p.id) ? 1 : 0, cur: p.id === currentId ? 1 : 0 }, geometry: { type: 'Point', coordinates: centreOf(p.bounds) } }
  }),
})

// The live globe. 'mini': a small round globe in the corner, facing the current place, a click expands it.
// 'full': the whole screen, every place a dot, hover and click. 'hidden': kept alive but out of sight.
const small = (v) => v === 'mini' || v === 'landing'
// Zoom at which a box of lng/lat bounds fits a w x h area (512 px tiles, mercator)
const fitZoom = (b, w, h) => {
  const mer = (la) => Math.log(Math.tan(Math.PI / 4 + (Math.max(-85, Math.min(85, la)) * Math.PI) / 360))
  const dx = Math.max(0.02, b[2] - b[0]), dy = Math.max(1e-4, mer(b[3]) - mer(b[1]))
  return Math.min(Math.log2((Math.max(80, w) * 360) / (512 * dx)), Math.log2((Math.max(80, h) * 2 * Math.PI) / (512 * dy)))
}

// Warm the browser cache with the basemap tiles of a place, so the flight and the place view meet sharp images instead of blocks.
const warmed = new Set()
function warmTiles(b, fz, w, h) {
  const key = b.join()
  if (warmed.has(key)) return
  warmed.add(key)
  const lat = (b[1] + b[3]) / 2, lng = (b[0] + b[2]) / 2
  const urls = []
  for (let z = 3; z <= Math.min(13, Math.ceil(fz) + 1); z++) {
    const n = 2 ** z, wx = ((lng + 180) / 360) * n, la = (lat * Math.PI) / 180, wy = ((1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2) * n
    const rx = Math.min(3, Math.ceil(w / 2 / 512 * 2 ** (z - fz) + 0.5)), ry = Math.min(3, Math.ceil(h / 2 / 512 * 2 ** (z - fz) + 0.5))
    for (let dx = -rx; dx <= rx; dx++) for (let dy = -ry; dy <= ry; dy++) {
      const x = ((Math.floor(wx) + dx) % n + n) % n, y = Math.floor(wy) + dy
      if (y < 0 || y >= n) continue
      if (z <= 8) for (const g of [GIBS, GIBS_PLACE]) urls.push(g.replace('{z}', z).replace('{y}', y).replace('{x}', x))
      if (z >= 4) urls.push(EOX.replace('{z}', z).replace('{y}', y).replace('{x}', x))
    }
  }
  urls.slice(0, 120).forEach((u) => fetch(u, { priority: 'low' }).then((r) => r.blob()).catch(() => {}))
}

export default function GlobeMap({ mode, places, currentId, matchIds, paused, phase, faceLL, faceKey, onOpen, onDiveStart, onDive, onDiveEnd, onExpand, onSelect, selectedId, divePad }) {
  const shell = useRef(null), el = useRef(null), map = useRef(null)
  const live = useRef({})
  const [tip, setTip] = useState(null)
  const [ready, setReady] = useState(0)
  const [noGL, setNoGL] = useState(false)
  const fit = useRef({ until: 0 })
  const prevMode = useRef('hidden')
  // vis is the shape on screen. It follows mode, except that a closing globe stays full-size until its circle has shrunk into the corner.
  const [vis, setVis] = useState(mode)
  const visRef = useRef(vis); visRef.current = vis
  const anim = useRef(null), pendingFlip = useRef(null)
  live.current = { places, mode, vis, phase, currentId, matchIds, paused, onOpen, onDiveStart, onDive, onDiveEnd, onSelect, divePad, tipOn: !!tip }

  // In the full view the globe sits clear of the search panel (left on a desktop, top on a phone).
  const pads = () => {
    if (visRef.current !== 'full') return { top: 0, bottom: 0, left: 0, right: 0 }
    return innerWidth >= 1000 ? { top: 0, bottom: 0, left: 240, right: 0 } : innerWidth >= 760 ? { top: 0, bottom: 0, left: 0, right: 0 } : { top: 130, bottom: 40, left: 0, right: 0 }
  }
  const idleZoom = () => {
    const { clientWidth: w, clientHeight: h } = shell.current
    const full = visRef.current === 'full', p = pads()
    return Math.log2((Math.PI * (full ? 0.82 : 0.9) * Math.max(80, Math.min(w - p.left - p.right, h - p.top - p.bottom))) / 512)
  }
  const placeLL = (id) => { const p = live.current.places.find((x) => x.id === id); return p ? centreOf(p.bounds) : null }

  // Box of the small shapes, read from a hidden probe wearing the same class, so it is right at any window size.
  const measure = (v) => {
    const probe = document.createElement('div')
    probe.className = `globe-shell ${v} hidden`
    shell.current.parentElement.appendChild(probe)
    const r = { x: probe.offsetLeft, y: probe.offsetTop, w: probe.offsetWidth, h: probe.offsetHeight }
    probe.remove()
    return r
  }
  // The globe grows from (or shrinks into) its small shape with transform and clip-path only: the canvas is full size throughout, so nothing is laid out or resized while it moves.
  const runFlip = (from, reverse, done) => {
    const el = shell.current
    if (!el || reduced()) { done?.(); return }
    const W = innerWidth, H = innerHeight, p = pads()
    const Df = 0.82 * Math.min(W - p.left - p.right, H - p.top - p.bottom), cx = p.left + (W - p.left - p.right) / 2, cy = p.top + (H - p.top - p.bottom) / 2
    const ds = 0.9 * Math.min(from.w, from.h), rx = from.x + from.w / 2, ry = from.y + from.h / 2
    const origin = `${cx}px ${cy}px`
    el.style.transformOrigin = origin
    anim.current?.cancel()
    const dir = reverse ? 'reverse' : 'normal', dur = reverse ? 560 : 720
    // the sphere grows with the transform; the circle that holds it opens to the whole screen only in the last part
    const T = 'translate(0px, 0px) scale(1)'
    const a = el.animate([
      { transform: `translate(${rx - cx}px, ${ry - cy}px) scale(${ds / Df})`, clipPath: `circle(${Df / 2}px at ${origin})`, easing: 'cubic-bezier(0.65, 0, 0.35, 1)' },
      { transform: T, clipPath: `circle(${Df / 2 + 4}px at ${origin})`, offset: 0.56, easing: 'cubic-bezier(0.5, 0, 0.3, 1)' },
      { transform: T, clipPath: `circle(${Math.hypot(W, H)}px at ${origin})` },
    ], { duration: dur, fill: 'both', direction: dir })
    anim.current = a
    const end = () => { if (anim.current !== a) return; done?.(); a.cancel(); anim.current = null; el.style.transformOrigin = '' }
    a.onfinish = end; a.oncancel = () => { if (anim.current === a) { anim.current = null; el.style.transformOrigin = '' } }
  }
  // mode changes: grow from the corner / landing, or close back into it
  useLayoutEffect(() => {
    const from = visRef.current
    if (mode === from) return
    if (!reduced() && !live.current.phase && shell.current && map.current) {
      if (small(from) && mode === 'full') { pendingFlip.current = { from: measure(from), reverse: false }; setVis(mode); return }
      if (from === 'full' && small(mode)) { runFlip(measure(mode), true, () => flushSync(() => setVis(mode))); return }
    }
    anim.current?.cancel()
    setVis(mode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])
  // a dive that ends without the app changing the mode must not leave the globe in the wrong shape
  useEffect(() => { if (!anim.current && !live.current.diving && !pendingFlip.current && mode !== vis && !phase) setVis(mode) }) // eslint-disable-line react-hooks/exhaustive-deps

  // build the map once
  useEffect(() => {
    let m
    try { m = new maplibregl.Map({ container: el.current, style: style(), center: [88, 24], zoom: 0.5, maxPitch: 0, dragRotate: false, pitchWithRotate: false, attributionControl: false, fadeDuration: 0, pixelRatio: Math.min(devicePixelRatio, 2) }) } catch { setNoGL(true); return }
    map.current = m
    if (STILL) window.__globe = m
    m.touchZoomRotate.disableRotation()
    m.getCanvas().tabIndex = -1
    m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    m.on('error', () => {}) // offline tiles must not make noise
    m.on('load', () => {
      el.current.querySelector('.maplibregl-ctrl-attrib')?.removeAttribute('open')
      m.addSource('places', { type: 'geojson', data: features([], null, null) })
      const dim = ['case', ['==', ['get', 'dim'], 1], 0.14, 1]
      m.addLayer({ id: 'halo', type: 'circle', source: 'places', filter: ['==', ['get', 'kind'], 's'], paint: { 'circle-radius': ['+', 6, ['get', 'r']], 'circle-color': ['get', 'color'], 'circle-opacity': ['case', ['==', ['get', 'dim'], 1], 0, 0.24], 'circle-blur': 0.7 } })
      m.addLayer({ id: 'season', type: 'circle', source: 'places', filter: ['==', ['get', 'kind'], 's'], paint: { 'circle-radius': ['get', 'r'], 'circle-color': ['get', 'color'], 'circle-opacity': dim, 'circle-opacity-transition': { duration: 150 }, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': dim } })
      m.addLayer({ id: 'event', type: 'symbol', source: 'places', filter: ['==', ['get', 'kind'], 'e'], layout: { 'icon-image': ['get', 'icon'], 'icon-size': ['get', 's'], 'icon-allow-overlap': true, 'icon-ignore-placement': true }, paint: { 'icon-opacity': dim, 'icon-opacity-transition': { duration: 150 } } })
      m.addLayer({ id: 'current', type: 'circle', source: 'places', filter: ['==', ['get', 'id'], ''], paint: { 'circle-radius': ['+', 6, ['get', 'r']], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#ff6a3d', 'circle-stroke-width': 2.5 } })
      m.addLayer({ id: 'hover', type: 'circle', source: 'places', filter: ['==', ['get', 'id'], ''], paint: { 'circle-radius': ['+', 5, ['get', 'r']], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.95 } })
      setReady(1)
    })

    const visible = (c, ll) => {
      const r = Math.PI / 180
      return Math.sin(c.lat * r) * Math.sin(ll[1] * r) + Math.cos(c.lat * r) * Math.cos(ll[1] * r) * Math.cos((c.lng - ll[0]) * r) > 0.12
    }
    // The place whose dot centre is nearest to the pointer (not whichever circle happens to be drawn on top).
    const pick = (pt) => {
      const { places: ps, matchIds: ids } = live.current
      const c = m.getCenter(), hits = []
      for (const p of ps) {
        if (ids && !ids.has(p.id)) continue
        const ll = centreOf(p.bounds)
        if (!visible(c, ll)) continue
        const s = m.project(ll), d = Math.hypot(s.x - pt.x, s.y - pt.y)
        if (d <= HIT) hits.push({ p, d, s })
      }
      hits.sort((a, b) => a.d - b.d)
      if (!hits.length) return null
      // dots lying on top of each other at this zoom: the nearest centre wins, the others are named in the tooltip
      const also = hits.filter((h) => h !== hits[0] && Math.hypot(h.s.x - hits[0].s.x, h.s.y - hits[0].s.y) <= SAME).map((h) => h.p.id)
      return { id: hits[0].p.id, also }
    }
    const setRing = (layer, id) => m.getLayer(layer) && m.setFilter(layer, ['==', ['get', 'id'], id || ''])
    const fade = (v) => { for (const [l, p] of [['season', 'circle-opacity'], ['event', 'icon-opacity'], ['halo', 'circle-opacity']]) if (m.getLayer(l)) m.setPaintProperty(l, p, v ? (l === 'halo' ? ['case', ['==', ['get', 'dim'], 1], 0, 0.24] : ['case', ['==', ['get', 'dim'], 1], 0.14, 1]) : 0)
      if (v && m.getLayer('hover')) { m.setPaintProperty('hover', 'circle-radius-transition', { duration: 0 }); m.setPaintProperty('hover', 'circle-radius', ['+', 5, ['get', 'r']]); m.setPaintProperty('hover', 'circle-stroke-opacity-transition', { duration: 0 }); m.setPaintProperty('hover', 'circle-stroke-opacity', 0.95); m.setFilter('hover', ['==', ['get', 'id'], '']) }
    }
    // One continuous shot: the other dots fade, the clicked one pulses, the camera follows a great-circle flight, and half way the place opens underneath (it is already fitted when the globe dissolves).
    const fly = (id) => {
      const ll = placeLL(id)
      if (!ll) return
      live.current.diving = true
      let fired = false
      const done = () => { if (fired) return; fired = true; fade(true); live.current.diving = false; live.current.onDiveEnd?.(id) }
      live.current.onDiveStart?.(id)
      if (reduced()) { live.current.onDive?.(id); return done() }
      // from the landing the globe first opens to the whole screen (the same grow as the expand), so the dive has the room it needs
      if (visRef.current === 'landing') { pendingFlip.current = { from: measure('landing'), reverse: false }; flushSync(() => setVis('full')) }
      const c = m.getCenter(), rad = Math.PI / 180
      const dist = Math.acos(Math.min(1, Math.max(-1, Math.sin(c.lat * rad) * Math.sin(ll[1] * rad) + Math.cos(c.lat * rad) * Math.cos(ll[1] * rad) * Math.cos((c.lng - ll[0]) * rad)))) / rad
      const dur = Math.round(Math.min(2200, Math.max(1300, 1100 + dist * 14)))
      // end the flight where the place view will begin: same centre on screen, nearly the same scale
      const L = live.current, pl = L.places.find((x) => x.id === id), dp = L.divePad || { top: 0, bottom: 0, left: 0, right: 0 }
      const W = shell.current.clientWidth, H = shell.current.clientHeight
      const fz = pl ? fitZoom(pl.bounds, W - dp.left - dp.right, H - dp.top - dp.bottom) : 6
      const zEnd = Math.min(8.2, Math.max(4.6, fz - 0.35))
      if (pl) warmTiles(pl.bounds, fz, W - dp.left - dp.right, H - dp.top - dp.bottom)
      const root = document.documentElement.style
      root.setProperty('--dive-s', String(Math.max(0.5, Math.min(1, 2 ** (zEnd - fz)))))
      root.setProperty('--dive-ox', `${dp.left + (W - dp.left - dp.right) / 2}px`); root.setProperty('--dive-oy', `${dp.top + (H - dp.top - dp.bottom) / 2}px`)
      setTip(null); fade(false)
      if (m.getLayer('hover')) { m.setFilter('hover', ['==', ['get', 'id'], id]); m.setPaintProperty('hover', 'circle-radius-transition', { duration: 500 }); m.setPaintProperty('hover', 'circle-radius', 26); m.setPaintProperty('hover', 'circle-stroke-opacity-transition', { duration: 500 }); m.setPaintProperty('hover', 'circle-stroke-opacity', 0) }
      m.flyTo({ center: ll, zoom: zEnd, padding: dp, duration: dur, curve: 1.35, easing: (t) => (t < 0.5 ? 4 * t * t * t : 1 - ((-2 * t + 2) ** 3) / 2), essential: true })
      setTimeout(() => { if (!fired) live.current.onDive?.(id) }, dur * 0.42)
      m.once('moveend', done)
      setTimeout(done, dur + 600)
    }
    let moveRaf = 0
    m.on('mousemove', (e) => {
      if (!BIG(live.current.mode) || live.current.diving || moveRaf) return
      moveRaf = requestAnimationFrame(() => { moveRaf = 0 })
      const h = pick(e.point)
      if (h && h.id !== live.current.warmId) { live.current.warmId = h.id; clearTimeout(live.current.warmT); live.current.warmT = setTimeout(() => live.current.warm?.(h.id), 150) }
      m.getCanvas().style.cursor = h ? 'pointer' : ''
      live.current.hover = !!h
      setRing('hover', h?.id)
      setTip((t) => (h ? (t && t.id === h.id && t.x === e.point.x && t.y === e.point.y ? t : { id: h.id, also: h.also, x: e.point.x, y: e.point.y }) : t ? null : t))
    })
    m.getCanvas().addEventListener('mouseleave', () => { live.current.hover = false; setRing('hover', ''); setTip(null) })
    m.on('click', (e) => {
      if (!BIG(live.current.mode) || live.current.diving) return
      const h = pick(e.point)
      if (!h) return live.current.onSelect?.(null)
      if (matchMedia('(hover: none)').matches) return live.current.onSelect?.(h.id)
      if (h.also.length && m.getZoom() < 7) { // overlapping dots: zoom in until they come apart, then the next click opens the nearest
        m.flyTo({ center: placeLL(h.id), zoom: Math.min(m.getZoom() + 3.5, 9), duration: reduced() ? 0 : 900, essential: true })
        return
      }
      fly(h.id)
    })
    live.current.fly = fly
    live.current.warm = (id) => {
      const pl = live.current.places.find((x) => x.id === id), dp = live.current.divePad
      if (!pl || !dp || !shell.current) return
      const W = shell.current.clientWidth - dp.left - dp.right, H = shell.current.clientHeight - dp.top - dp.bottom
      warmTiles(pl.bounds, fitZoom(pl.bounds, W, H), W, H)
    }

    // slow rotation until the globe is touched; pauses 4 s after any interaction
    let last = performance.now(), touched = 0, raf
    const touch = () => { touched = performance.now() }
    for (const ev of ['mousedown', 'touchstart', 'wheel']) m.getCanvas().addEventListener(ev, touch, { passive: true })
    const tick = (t) => {
      const dt = Math.min(t - last, 100)
      last = t
      const L = live.current
      if (!STILL && BIG(L.mode) && !reduced() && !L.hover && !L.diving && !L.paused && !document.hidden && t - touched > 4000 && !m.isMoving()) {
        const c = m.getCenter()
        m.jumpTo({ center: [c.lng + dt * 0.0028, c.lat] })
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    // while the shell grows or shrinks, keep the whole globe in view
    const ro = new ResizeObserver(() => {
      m.resize()
      if (performance.now() < fit.current.until) m.jumpTo({ zoom: idleZoom() })
    })
    ro.observe(shell.current)
    return () => { cancelAnimationFrame(raf); ro.disconnect(); m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // dots: data, current place, dimming
  useEffect(() => {
    const m = map.current
    if (!ready || !m) return
    for (const p of places) {
      if (!isEvent(p)) continue
      const t = classInfo(classOf(p))
      const name = `ev-${t.shape}-${placeColor(p)}`
      if (!m.hasImage(name)) m.addImage(name, drawIcon(t.shape, placeColor(p)), { pixelRatio: 2 })
    }
    m.getSource('places').setData(features(places, matchIds, currentId))
    m.setFilter('current', ['==', ['get', 'id'], currentId || ''])
  }, [ready, places, matchIds, currentId])
  useEffect(() => { const m = map.current; if (ready && m?.getLayer('hover')) m.setFilter('hover', ['==', ['get', 'id'], selectedId || '']) }, [ready, selectedId])

  // mini or full: dot size, interaction, camera (before paint, so the first frame of a grow already has the right dots and size)
  useLayoutEffect(() => {
    const m = map.current
    if (!ready || !m) return
    const was = prevMode.current
    prevMode.current = vis
    if (vis === 'hidden') return
    const mini = vis === 'mini'
    const k = mini ? 0.42 : 1
    m.setPaintProperty('season', 'circle-radius', ['max', 1.8, ['*', ['get', 'r'], k]])
    m.setPaintProperty('halo', 'circle-radius', ['*', ['+', 6, ['get', 'r']], k])
    m.setLayoutProperty('event', 'icon-size', ['*', ['get', 's'], mini ? 0.4 : 1])
    m.setPaintProperty('current', 'circle-radius', mini ? 4.5 : ['+', 6, ['get', 'r']])
    m.setPaintProperty('current', 'circle-color', mini ? '#ff6a3d' : 'rgba(0,0,0,0)')
    m.setPaintProperty('current', 'circle-stroke-color', mini ? '#fff' : '#ff6a3d')
    m.setPaintProperty('current', 'circle-stroke-width', mini ? 1.5 : 2.5)
    if (mini) { m.setPaintProperty('season', 'circle-stroke-width', 0); m.setPaintProperty('halo', 'circle-opacity', 0) } else { m.setPaintProperty('season', 'circle-stroke-width', 1.5); m.setPaintProperty('halo', 'circle-opacity', ['case', ['==', ['get', 'dim'], 1], 0, 0.24]) }
    const land = vis === 'landing'
    for (const h of ['scrollZoom', 'doubleClickZoom', 'touchZoomRotate', 'keyboard']) m[h][mini || land ? 'disable' : 'enable']()
    m.dragPan[mini ? 'disable' : 'enable']()
    m.getCanvas().style.cursor = mini ? 'pointer' : ''
    if (mini) setTip(null)
    m.stop()
    m.resize() // synchronously: the canvas has its new size in this very frame
    m.setPadding(pads())
    const here = placeLL(currentId)
    if (mini) m.jumpTo({ center: here || m.getCenter(), zoom: idleZoom() })
    else if (was === 'hidden') m.jumpTo({ center: here || [88, 24], zoom: idleZoom() })
    else m.jumpTo({ center: m.getCenter(), zoom: idleZoom() })
    const f = pendingFlip.current
    if (f) { pendingFlip.current = null; runFlip(f.from, false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vis, ready])
  // choosing a class turns the globe to face the densest group of it
  useEffect(() => {
    const m = map.current
    if (!ready || !m || vis !== 'full' || !faceLL || anim.current) return
    m.easeTo({ center: faceLL, duration: reduced() ? 0 : 1300, easing: (t) => (t < 0.5 ? 4 * t * t * t : 1 - ((-2 * t + 2) ** 3) / 2) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [faceKey, ready])
  // the mini globe turns to the place being shown
  useEffect(() => {
    const m = map.current
    if (!ready || !m || vis !== 'mini') return
    const ll = placeLL(currentId)
    if (ll) m.easeTo({ center: ll, duration: reduced() ? 0 : 700 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, ready, places.length, vis])

  const tp = tip && places.find((p) => p.id === tip.id)
  const also = tip?.also?.map((id) => places.find((p) => p.id === id)?.name).filter(Boolean) || []
  const w = shell.current?.clientWidth || 0
  return (
    <div ref={shell} className={`globe-shell ${vis} ${phase || ''}`} aria-hidden={vis === 'hidden' ? 'true' : undefined}>
      <div ref={el} className="globe-canvas" />
      {noGL && (
        <div className="globe-fallback" role="group" aria-label="Places">
          <p className="mono">The 3D globe needs WebGL, which this browser does not offer. Pick a place:</p>
          <ul>{places.slice(0, 40).map((p) => <li key={p.id}><button onClick={() => onOpen(p.id)}>{p.name}</button></li>)}</ul>
        </div>
      )}
      {vis === 'mini' && !anim.current && <button className="globe-hit" onClick={onExpand} aria-label="Open the globe: where on Earth this place is" title="Open the globe (G)" />}
      {BIG(mode) && tp && (
        <div className="globe-tip" style={{ left: Math.min(tip.x + 16, Math.max(8, w - 320)), top: tip.y }} role="tooltip">
          <strong>{tp.name}</strong>
          <span>{headline(tp).short} <small>{headline(tp).tag}</small></span>
          {also.length > 0 && <em>Also here: {also.join(', ')}. Click to zoom in.</em>}
        </div>
      )}
    </div>
  )
}
