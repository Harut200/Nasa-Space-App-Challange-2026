import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import { styleFor } from './basemap.js'
import { coords } from './meta.js'

// Second map for "compare two dates": same basemap, the other date's radar frame, clipped to the right of the divider.
// Its camera is kept identical to the main map by the caller.
export default function CompareMap({ mode, bounds, url, opacity, onMap, style }) {
  const el = useRef(null)
  const map = useRef(null)
  const [ready, setReady] = useState(false)
  const latest = useRef({})
  latest.current = { bounds, url, opacity }
  useEffect(() => {
    const [w, s, e, n] = bounds
    const m = new maplibregl.Map({ container: el.current, style: styleFor(mode), bounds: [[w, s], [e, n]], maxPitch: 0, dragRotate: false, attributionControl: false, fadeDuration: 0 })
    map.current = m
    m.on('style.load', () => {
      const { bounds: b, url: u, opacity: o } = latest.current
      m.addSource('cmp', { type: 'image', url: u, coordinates: coords(b) })
      m.addLayer({ id: 'cmp', type: 'raster', source: 'cmp', paint: { 'raster-opacity': o, 'raster-fade-duration': 0 } })
      setReady(true)
    })
    onMap?.(m)
    return () => { onMap?.(null); m.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { if (ready) map.current.getSource('cmp')?.updateImage({ url }) }, [ready, url])
  useEffect(() => { if (ready) map.current.setPaintProperty('cmp', 'raster-opacity', opacity) }, [ready, opacity])
  return <div ref={el} className="compare-map" style={style} />
}
