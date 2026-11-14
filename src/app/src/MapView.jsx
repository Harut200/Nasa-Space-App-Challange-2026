import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import 'maplibre-gl/dist/maplibre-gl.css'

// The bundler moves the worker file, so MapLibre cannot find it by itself.
maplibregl.setWorkerUrl(workerUrl)

const STYLE = 'https://tiles.openfreemap.org/styles/dark' // no API key needed

// One map with one radar image on it. `bounds` is [west, south, east, north].
export default function MapView({ bounds, url, spots = [], colors = [], onMap, style }) {
  const el = useRef(null)
  const map = useRef(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const [w, s, e, n] = bounds || [-180, -60, 180, 75]
    // Start on the area, not the whole world: fewer basemap tiles to fetch first.
    const m = new maplibregl.Map({ container: el.current, style: STYLE, bounds: [[w, s], [e, n]], fitBoundsOptions: { padding: 40 } })
    m.addControl(new maplibregl.NavigationControl())
    // 'style.load' is enough to add the radar image; 'load' waits for every basemap
    // tile, so on slow Wi-Fi the radar would appear late or not at all.
    const go = () => setReady(true)
    m.once('style.load', go)
    m.once('load', go)
    map.current = m
    onMap?.(m)
    return () => { onMap?.(null); m.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // New area: replace the image source and fly there.
  const key = bounds?.join()
  useEffect(() => {
    const m = map.current
    if (!ready || !bounds) return
    const [w, s, e, n] = bounds
    if (m.getLayer('sar')) m.removeLayer('sar')
    if (m.getSource('sar')) m.removeSource('sar')
    m.addSource('sar', { type: 'image', url, coordinates: [[w, n], [e, n], [e, s], [w, s]] })
    m.addLayer({ id: 'sar', type: 'raster', source: 'sar', paint: { 'raster-fade-duration': 0 } })
    m.fitBounds([[w, s], [e, n]], { padding: 40, animate: false })
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

  return <div ref={el} className="map" style={style} />
}
