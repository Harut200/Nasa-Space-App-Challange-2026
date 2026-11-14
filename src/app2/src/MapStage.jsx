import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'

maplibregl.setWorkerUrl(workerUrl)

const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'
const EOX = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg'
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// Works with no network: the radar images are our own sources on a plain dark background.
// Blue Marble carries the wide views; Sentinel-2 cloudless (EOX, CC BY-NC-SA 4.0) blends in from about zoom 6 for a sharper close view,
// dimmed and desaturated so the radar layers stay the hero.
const styleFor = () => ({
  version: 8,
  sources: {
    gibs: { type: 'raster', tiles: [GIBS], tileSize: 256, maxzoom: 8, attribution: 'Imagery: NASA GIBS · Blue Marble' },
    eox: { type: 'raster', tiles: [EOX], tileSize: 256, maxzoom: 13, attribution: 'Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)' },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#070a0f' } },
    { id: 'earth', type: 'raster', source: 'gibs',
      paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 6, 1, 8.5, 0.15], 'raster-brightness-max': 0.7, 'raster-saturation': -0.2, 'raster-contrast': 0.12, 'raster-fade-duration': 500 } },
    { id: 's2', type: 'raster', source: 'eox', minzoom: 5.5,
      paint: { 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0, 8, 0.9], 'raster-brightness-max': 0.5, 'raster-brightness-min': 0.02, 'raster-saturation': -0.45, 'raster-contrast': 0.18, 'raster-fade-duration': 500 } },
  ],
})

const quad = (b) => { const [w, s, e, n] = b; return [[w, n], [e, n], [e, s], [w, s]] }

function addSlots(m, names, bounds) {
  names.forEach((n) => {
    if (m.getSource(n)) return
    m.addSource(n, { type: 'image', url: 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==', coordinates: quad(bounds) })
    m.addLayer({ id: n, type: 'raster', source: n, paint: { 'raster-opacity': 0, 'raster-opacity-transition': { duration: reduced() ? 0 : 500 }, 'raster-fade-duration': 0, 'raster-resampling': 'linear' } })
  })
}

// Swap the image of a slot pair with a cross-fade; stale requests are dropped.
function crossfade(st, m, url, bounds, opacity) {
  const token = ++st.token
  if (!url) { ['a', 'b'].forEach((k) => m.getLayer(`sar-${k}`) && m.setPaintProperty(`sar-${k}`, 'raster-opacity', 0)); return }
  const next = st.active === 'a' ? 'b' : 'a'
  const img = new Image()
  img.onload = img.onerror = () => {
    if (token !== st.token || !m.getSource(`sar-${next}`)) return
    m.getSource(`sar-${next}`).updateImage({ url, coordinates: quad(bounds) })
    let done = false
    const show = () => {
      if (done || token !== st.token) return
      done = true
      m.off('sourcedata', onData)
      m.setPaintProperty(`sar-${next}`, 'raster-opacity', opacity)
      m.setPaintProperty(`sar-${st.active}`, 'raster-opacity', 0)
      st.active = next
    }
    const onData = (e) => { if (e.sourceId === `sar-${next}` && m.isSourceLoaded(`sar-${next}`)) setTimeout(show, 30) }
    m.on('sourcedata', onData)
    setTimeout(show, 700)
  }
  img.src = url
}

/**
 * props: bounds, url, opacity, padding, camera ({key, focus:[lng,lat], zoomBoost}), spots, activeSpot, onSpot,
 *        interactive, compare {on, split, url}
 */
export default function MapStage({ bounds, url, opacity = 0.9, padding, camera, spots = [], showSpots, activeSpot, onSpot, interactive = true, star, compare, onSplit, onReady }) {
  const el = useRef(null), el2 = useRef(null)
  const map = useRef(null), map2 = useRef(null)
  const st = useRef({ token: 0, active: 'a', ready: false, token2: 0, active2: 'a' })
  const latest = useRef({})
  latest.current = { bounds, url, opacity, padding, camera }
  const markers = useRef(new Map())

  // build the main map once
  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current, style: styleFor(), attributionControl: false, bounds: [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
      fitBoundsOptions: { padding: padding || 40 }, fadeDuration: 0, dragRotate: false, pitchWithRotate: false, maxPitch: 0,
    })
    m.on('error', (e) => { if (import.meta.env.DEV || window.__dbg) console.log('MAPERR', e?.error?.message, e?.sourceId) }) // offline tiles must not make noise
    m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    m.touchZoomRotate.disableRotation()
    m.getCanvas().tabIndex = -1 // the app owns the arrow keys; Tab goes to real controls
    map.current = m
    m.on('load', () => {
      el.current.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show') // credits stay one tap away (the info button and the sheet)
      el.current.querySelector('.maplibregl-ctrl-attrib')?.removeAttribute('open')
      const { bounds: b, url: u, opacity: o } = latest.current
      addSlots(m, ['sar-a', 'sar-b'], b, o)
      st.current.ready = true
      crossfade(st.current, m, u, b, o)
      onReady?.(m)
    })
    const ro = new ResizeObserver(() => { m.resize(); map2.current?.resize() })
    ro.observe(el.current)
    return () => { ro.disconnect(); markers.current.forEach((k) => k.remove()); markers.current.clear(); map2.current?.remove(); map2.current = null; m.remove(); map.current = null; st.current.ready = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // interactivity (opening is a cinematic, hands-off backdrop)
  useEffect(() => {
    const m = map.current
    ;['scrollZoom', 'dragPan', 'doubleClickZoom', 'touchZoomRotate', 'keyboard'].forEach((h) => (interactive ? m[h].enable() : m[h].disable()))
    m.getCanvas().style.cursor = interactive ? '' : 'default'
  }, [interactive])

  // image frame / opacity
  const bkey = bounds.join()
  useEffect(() => {
    const m = map.current
    if (!st.current.ready) return
    ;['sar-a', 'sar-b'].forEach((n) => m.getSource(n)?.setCoordinates?.(quad(bounds)))
    crossfade(st.current, m, url, bounds, opacity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, bkey])
  useEffect(() => {
    const m = map.current
    if (st.current.ready) m.getLayer(`sar-${st.current.active}`) && url && m.setPaintProperty(`sar-${st.current.active}`, 'raster-opacity', opacity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opacity])

  // camera
  const ckey = `${bkey}|${camera?.key ?? ''}|${padding ? JSON.stringify(padding) : ''}`
  useEffect(() => {
    const m = map.current
    const go = () => {
      const dur = reduced() ? 0 : (camera?.duration ?? 1500)
      const pad = padding || 40
      if (camera?.focus) {
        const cam = m.cameraForBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: pad })
        const off = typeof pad === 'object' ? [((pad.left || 0) - (pad.right || 0)) / 2, ((pad.top || 0) - (pad.bottom || 0)) / 2] : [0, 0]
        m.easeTo({ center: camera.focus, zoom: (cam?.zoom ?? 7) + (camera.zoomBoost ?? 1.3), duration: dur, offset: off })
      } else {
        m.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: pad, duration: dur, maxZoom: 11 })
      }
    }
    go()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ckey])

  // spots
  useEffect(() => {
    const m = map.current
    markers.current.forEach((k) => k.remove()); markers.current.clear()
    if (!showSpots) return
    spots.forEach((s) => {
      const d = document.createElement('button')
      d.className = 'spot'; d.setAttribute('aria-label', `Spot: ${s.label}`); d.title = s.label
      d.innerHTML = `<i></i><span>${s.label.replace(/</g, '&lt;')}</span>`
      d.addEventListener('click', (ev) => { ev.stopPropagation(); onSpot?.(s.id) })
      markers.current.set(s.id, new maplibregl.Marker({ element: d }).setLngLat(s.lngLat).addTo(m))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spots, showSpots])
  useEffect(() => { markers.current.forEach((k, id) => k.getElement().classList.toggle('on', id === activeSpot)) }, [activeSpot, spots, showSpots])

  // epicentre star (earthquake places): always visible, label as text only
  const starKey = star ? star.join() : ''
  useEffect(() => {
    const m = map.current
    if (!star || !m) return
    const d = document.createElement('div')
    d.className = 'quake-star'; d.setAttribute('role', 'img'); d.setAttribute('aria-label', 'Epicentre (USGS)')
    d.innerHTML = '<svg width="34" height="34" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.2l2.9 6.4 7 .7-5.3 4.6 1.6 6.9L12 17.2 5.8 20.8l1.6-6.9L2.1 9.3l7-.7z" fill="#ff6a3d" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><span>Epicentre</span>'
    const mk = new maplibregl.Marker({ element: d }).setLngLat(star).addTo(m)
    return () => mk.remove()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [starKey])

  // compare map (first pass, clipped to the left of the divider)
  const cmpOn = !!compare?.on
  useEffect(() => {
    if (!cmpOn) { map2.current?.remove(); map2.current = null; return }
    const m = map.current
    const c = m.getCenter()
    const m2 = new maplibregl.Map({ container: el2.current, style: styleFor(), attributionControl: false, center: c, zoom: m.getZoom(), interactive: false, fadeDuration: 0 })
    m2.on('error', () => {})
    map2.current = m2
    st.current.active2 = 'a'
    m2.on('load', () => {
      addSlots(m2, ['sar-a', 'sar-b'], latest.current.bounds, 1)
      const u = latest.current.cmpUrl
      if (u) { m2.getSource('sar-a').updateImage({ url: u, coordinates: quad(latest.current.bounds) }); m2.setPaintProperty('sar-a', 'raster-opacity', latest.current.opacity) }
    })
    const sync = () => map2.current?.jumpTo({ center: m.getCenter(), zoom: m.getZoom() })
    m.on('move', sync)
    sync()
    return () => { m.off('move', sync); map2.current?.remove(); map2.current = null }
  }, [cmpOn])
  latest.current.cmpUrl = compare?.url
  useEffect(() => {
    const m2 = map2.current
    if (!cmpOn || !m2 || !m2.isStyleLoaded() || !m2.getSource('sar-a')) return
    if (!compare?.url) { m2.setPaintProperty('sar-a', 'raster-opacity', 0); return } // bare terrain on this side
    m2.getSource('sar-a').updateImage({ url: compare.url, coordinates: quad(bounds) })
    m2.setPaintProperty('sar-a', 'raster-opacity', opacity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compare?.url, opacity, cmpOn])

  const split = compare?.split ?? 50
  const drag = (e) => {
    const box = el.current.getBoundingClientRect()
    const mv = (ev) => onSplit?.(Math.max(2, Math.min(98, ((ev.clientX - box.left) / box.width) * 100)))
    const up = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up)
    e.preventDefault()
  }

  return (
    <div className="stage-map">
      <div ref={el} className="map" />
      <div ref={el2} className="map map2" style={{ display: cmpOn ? 'block' : 'none', clipPath: `inset(0 ${100 - split}% 0 0)` }} />
      {cmpOn && (
        <div className="divider" style={{ left: `${split}%` }}>
          {compare.tags && split > 16 && <span className="cmp-tag l mono">{compare.tags[0]}</span>}
          {compare.tags && split < 84 && <span className="cmp-tag r mono">{compare.tags[1]}</span>}
          <button className="handle" role="slider" aria-label={`Compare: drag the handle. ${compare.tags ? `${compare.tags[0]} on the left, ${compare.tags[1]} on the right.` : ''}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split)}
            onPointerDown={drag}
            onKeyDown={(e) => { if (e.key === 'ArrowLeft') { onSplit?.(Math.max(2, split - 4)); e.stopPropagation(); e.preventDefault() } if (e.key === 'ArrowRight') { onSplit?.(Math.min(98, split + 4)); e.stopPropagation(); e.preventDefault() } }}>
            <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><path d="M8 5 3 11l5 6M14 5l5 6-5 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
        </div>
      )}
    </div>
  )
}
