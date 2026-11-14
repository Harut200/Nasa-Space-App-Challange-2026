import { useEffect, useMemo, useRef, useState } from 'react'
import { dayNum, fmtDate, fmtDateLong, fmtKm } from './data.js'

const useSize = (ref) => {
  const [s, setS] = useState(null)
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setS({ w: Math.max(240, e.contentRect.width), h: Math.max(110, e.contentRect.height) }))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return s
}

const niceMax = (v) => { const p = Math.pow(10, Math.floor(Math.log10(v))); const f = v / p; return ([1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((x) => f <= x)) * p }

/** The timeline is the chart: primary curve with its uncertainty band, ten passes as beats, the outage as a rest. */
export default function TimelineChart({ place, index, onScrub, showFlood = true }) {
  const box = useRef(null)
  const size = useSize(box)
  const { w, h } = size || { w: 800, h: 180 }
  const [hover, setHover] = useState(null)
  const [drag, setDrag] = useState(false)
  const P = place.primary
  const dates = place.dates
  const M = { l: 46, r: 18, t: 20, b: 30 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b

  const g = useMemo(() => {
    const t0 = dayNum(dates[0]), t1 = dayNum(dates[dates.length - 1])
    const x = (d) => M.l + ((dayNum(d) - t0) / (t1 - t0)) * iw
    const top = P.fixedMax || niceMax(Math.max(...P.values.filter((v) => v != null), ...(P.hi || []).filter((v) => v != null)) * 1.05)
    const y = (v) => M.t + ih - (v / top) * ih
    const flood = showFlood && P.kind === 'water' && place.flood && Math.max(...place.flood.values) >= 0.04 * top ? place.flood : null
    return { x, y, top, flood, t0, t1 }
  }, [dates, P, place.flood, iw, ih, showFlood])

  const pts = dates.map((d, i) => (P.values[i] == null ? null : { i, d, x: g.x(d), y: g.y(P.values[i]), v: P.values[i] }))
  // dash the line where passes are far apart (an outage between them)
  const seg = []
  for (let i = 1; i < pts.length; i++) if (pts[i] && pts[i - 1]) seg.push({ a: pts[i - 1], b: pts[i], gap: dayNum(dates[i]) - dayNum(dates[i - 1]) > 20 })
  const band = P.lo && P.hi ? pts.filter(Boolean) : null
  const bandPath = band && 'M' + band.map((p) => `${p.x},${g.y(P.hi[p.i])}`).join('L') + 'L' + [...band].reverse().map((p) => `${p.x},${g.y(P.lo[p.i])}`).join('L') + 'Z'
  const ticks = [0, 0.5, 1].map((f) => f * g.top)

  const at = (clientX) => {
    const r = box.current.getBoundingClientRect()
    const px = clientX - r.left
    let best = 0, bd = 1e9
    dates.forEach((d, i) => { const dd = Math.abs(g.x(d) - px); if (dd < bd) { bd = dd; best = i } })
    return best
  }
  const cur = pts[index]
  const hv = hover != null ? hover : null
  const hp = hv != null ? pts[hv] : null
  const q = (i) => place.quality[dates[i]]
  const every = w < 560 ? 2 : 1

  return (
    <div className="chart" ref={box} data-ready={!!size}
      onPointerDown={(e) => { setDrag(true); e.currentTarget.setPointerCapture(e.pointerId); onScrub(at(e.clientX)) }}
      onPointerMove={(e) => { const i = at(e.clientX); setHover(i); if (drag) onScrub(i) }}
      onPointerUp={() => setDrag(false)} onPointerLeave={() => { setHover(null) }}>
      {size && <svg width={w} height={h} role="img" aria-label={`${P.label} over the ten radar passes. Drag to move through time.`}>
        <defs>
          <pattern id="rest" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="7" height="7" fill="none" /><line x1="0" y1="0" x2="0" y2="7" stroke="var(--ink-3)" strokeWidth="1.2" opacity=".5" />
          </pattern>
        </defs>
        {ticks.map((tv) => (
          <g key={tv}>
            <line x1={M.l} x2={w - M.r} y1={g.y(tv)} y2={g.y(tv)} stroke="var(--grid)" strokeWidth="1" />
            <text x={M.l - 8} y={g.y(tv) + 4} textAnchor="end" className="ax">{fmtKm(tv)}</text>
          </g>
        ))}
        <text x={M.l} y={M.t - 5} textAnchor="start" className="ax unit">{P.unit === 'km²' ? 'km²' : 'relative brightness'}</text>
        {place.gaps.map(([a, b], i) => {
          const x0 = Math.max(M.l, g.x(a)), x1 = Math.min(w - M.r, g.x(b))
          return (
            <g key={i}>
              <rect x={x0} y={M.t} width={Math.max(2, x1 - x0)} height={ih} fill="url(#rest)" />
              <text x={(x0 + x1) / 2} y={M.t + 12} textAnchor="middle" className="ax rest-label">no passes</text>
            </g>
          )
        })}
        {bandPath && <path d={bandPath} fill="var(--water)" opacity=".18" />}
        {g.flood && (() => {
          const fp = dates.map((d, i) => ({ x: g.x(d), y: g.y(g.flood.values[i] || 0), i }))
          return (
            <g>
              <path d={'M' + fp.map((p) => `${p.x},${p.y}`).join('L')} fill="none" stroke="var(--veg)" strokeWidth="2" strokeLinejoin="round" />
              {fp.map((p) => <circle key={p.i} cx={p.x} cy={p.y} r="3" fill="var(--surface)" stroke="var(--veg)" strokeWidth="2" />)}
              <text x={fp[fp.length - 1].x - 6} y={fp[fp.length - 1].y - 9} textAnchor="end" className="dl" fill="var(--veg)">Flooded vegetation</text>
            </g>
          )
        })()}
        {seg.map((s, i) => (
          <line key={i} x1={s.a.x} y1={s.a.y} x2={s.b.x} y2={s.b.y} stroke="var(--water)" strokeWidth="2.5" strokeLinecap="round" strokeDasharray={s.gap ? '2 6' : undefined} />
        ))}
        {pts.map((p, i) => p && (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r="4.5" fill="var(--water)" stroke="var(--surface)" strokeWidth="2" />
            {q(i) && <path d={`M${p.x - 4},${h - M.b + 2}h8l-4,-7z`} fill={q(i).quality === 'fail' ? 'var(--bad)' : 'var(--warn)'} />}
          </g>
        ))}
        {dates.map((d, i) => ((i % every === 0 && Math.abs(i - index) >= every) || i === index) && (
          <text key={d} x={g.x(d)} y={h - 6} textAnchor="middle" className={`ax ${i === index ? 'now' : ''}`}>{fmtDate(d)}</text>
        ))}
        {/* playhead */}
        {cur && (
          <g className="playhead" style={{ transform: `translateX(${cur.x}px)` }}>
            <line x1="0" x2="0" y1={M.t} y2={M.t + ih} stroke="var(--accent)" strokeWidth="1.5" />
            <circle cx="0" cy={cur.y} r="7.5" fill="var(--accent)" stroke="var(--surface)" strokeWidth="2.5" />
          </g>
        )}
        {!cur && <line x1={g.x(dates[index])} x2={g.x(dates[index])} y1={M.t} y2={M.t + ih} stroke="var(--accent)" strokeWidth="1.5" />}
        {P.lo && cur && <text x={cur.x > w - 130 ? cur.x - 14 : cur.x + 14} y={Math.max(M.t + 10, cur.y - 12)} textAnchor={cur.x > w - 130 ? 'end' : 'start'} className="dl" fill="var(--ink)">{fmtKm(cur.v)} km²</text>}
      </svg>}
      {hp && hv !== index && (
        <div className="tip" style={{ left: Math.min(w - 190, Math.max(0, hp.x - 80)), top: Math.max(0, hp.y - 64) }}>
          <b>{fmtDateLong(hp.d)}</b>
          <span>{fmtKm(hp.v)} {P.unit === 'km²' ? 'km²' : ''}{P.lo ? ` (${fmtKm(P.lo[hp.i])}–${fmtKm(P.hi[hp.i])})` : ''}</span>
          {q(hv) && <em>{q(hv).quality === 'fail' ? 'Flagged pass' : 'Caution'}</em>}
        </div>
      )}
    </div>
  )
}
