import { useEffect, useState } from 'react'

// Where the radar image sits on screen: the divider only lives inside this rectangle.
export function useExtent(map, bounds) {
  const [r, setR] = useState(null)
  useEffect(() => {
    if (!map || !bounds) return
    const [w, s, e, n] = bounds
    const calc = () => {
      const a = map.project([w, n]), b = map.project([e, s])
      const W = innerWidth, H = innerHeight
      const left = Math.max(0, Math.min(a.x, b.x)), right = Math.min(W, Math.max(a.x, b.x))
      const top = Math.max(76, Math.min(a.y, b.y)), bottom = Math.min(H - (W >= 900 ? 215 : 250), Math.max(a.y, b.y)) // stay clear of the top bar and the timeline
      setR(right - left > 40 && bottom - top > 40 ? { left, top, width: right - left, height: bottom - top } : null)
    }
    calc()
    map.on('move', calc); map.on('resize', calc)
    return () => { map.off('move', calc); map.off('resize', calc) }
  }, [map, bounds])
  return r
}

// A draggable divider with a round handle and a date at each end. `split` is 0..100 across the image.
export function Divider({ rect, split, onSplit, left, right }) {
  if (!rect) return null
  const x = rect.left + (rect.width * split) / 100
  const move = (e) => onSplit(Math.max(2, Math.min(98, ((e.clientX - rect.left) / rect.width) * 100)))
  const down = (e) => { e.currentTarget.setPointerCapture(e.pointerId); move(e) }
  const key = (e) => {
    if (e.key === 'ArrowLeft') onSplit(Math.max(2, split - 4))
    if (e.key === 'ArrowRight') onSplit(Math.min(98, split + 4))
  }
  return (
    <div className="cmp-divider" style={{ left: x, top: rect.top, height: rect.height }}>
      <span className="cmp-label l">{left}</span>
      <span className="cmp-label r">{right}</span>
      <div className="cmp-line" aria-hidden="true" />
      <button className="cmp-handle" aria-label="Drag to compare the two dates" role="slider" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split)}
        onPointerDown={down} onPointerMove={(e) => e.buttons && move(e)} onKeyDown={key}>
        <span aria-hidden="true">‹ ›</span>
      </button>
    </div>
  )
}
