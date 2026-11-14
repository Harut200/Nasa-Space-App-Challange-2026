import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'

// The bundler moves the worker file, so MapLibre cannot find it by itself.
maplibregl.setWorkerUrl(workerUrl)

const STYLE = 'https://tiles.openfreemap.org/styles/dark' // no API key needed

// Plain dark background: our own radar content never waits for the online basemap.
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b0f14' } }] }
const BASEMAP_TIMEOUT = 4500 // ms to wait for the online style before giving up

// One map with one radar image on it. `bounds` is [west, south, east, north].
export default function MapView({ bounds, url, spots = [], colors = [], onMap, style }) {
  const el = useRef(null)
  const map = useRef(null)
  const latest = useRef({})
  latest.current = { bounds, url }
  const [ready, setReady] = useState(false)
  const [basemap, setBasemap] = useState('loading') // loading | ok | failed

  // (Re)build the radar source and layer. Safe to call any time, and after a style swap.
  const addOverlay = (m, fit) => {
    const { bounds: b, url: u } = latest.current
    if (!b) return
    const [w, s, e, n] = b
    if (m.getLayer('sar')) m.removeLayer('sar')
    if (m.getSource('sar')) m.removeSource('sar')
    m.addSource('sar', { type: 'image', url: u, coordinates: [[w, n], [e, n], [e, s], [w, s]] })
    m.addLayer({ id: 'sar', type: 'raster', source: 'sar', paint: { 'raster-fade-duration': 0 } })
    if (fit) m.fitBounds([[w, s], [e, n]], { padding: 40, animate: false })
  }

  useEffect(() => {
    const [w, s, e, n] = bounds || [-180, -60, 180, 75]
    const m = new maplibregl.Map({ container: el.current, style: PLAIN, bounds: [[w, s], [e, n]], fitBoundsOptions: { padding: 40 } })
    m.addControl(new maplibregl.NavigationControl())
    map.current = m
    onMap?.(m)
    let dead = false
    let swapping = false
    // The inline style is ready at once; the overlay goes on right away.
    m.once('style.load', () => setReady(true))
    // After the basemap replaces the style, put the radar back on top of it.
    m.on('style.load', () => { if (swapping) { swapping = false; addOverlay(m, false); setBasemap('ok') } })
    // Fetch the basemap separately; switch only if it arrives in time.
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), BASEMAP_TIMEOUT)
    fetch(STYLE, { signal: ctl.signal })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json() })
      .then((j) => {
        clearTimeout(timer)
        if (dead) return
        swapping = true
        m.setStyle(j, { diff: false })
      })
      .catch(() => { clearTimeout(timer); if (!dead) setBasemap('failed') })
    return () => { dead = true; clearTimeout(timer); ctl.abort(); onMap?.(null); m.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // New area: replace the image source and fly there.
  const key = bounds?.join()
  useEffect(() => {
    if (!ready || !bounds) return
    const m = map.current
    if (m.isStyleLoaded() || m.getStyle()) addOverlay(m, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, key])

  // New frame of the same area.
  useEffect(() => {
    if (ready && url) map.current.getSource('sar')?.updateImage({ url })
  }, [ready, url])

  useEffect(() => {
    if (!ready) return
    const markers = spots.map((s, i) =>
      new maplibregl.Marker({ color: colors[i % colors.length] })
        .setLngLat(s.lngLat).setPopup(new maplibregl.Popup().setText(s.label)).addTo(map.current))
    return () => markers.forEach((k) => k.remove())
  }, [ready, spots, colors])

  return (
    <div ref={el} className="map" style={style}>
      {basemap === 'failed' && !style && (
        <div style={{ position: 'absolute', left: 8, bottom: 22, zIndex: 2, padding: '3px 8px', borderRadius: 4,
          font: '12px system-ui, sans-serif', color: '#c8d0d8', background: 'rgba(11,15,20,0.8)', pointerEvents: 'none' }}>
          Basemap unavailable — showing radar data only
        </div>
      )}
    </div>
  )
}
