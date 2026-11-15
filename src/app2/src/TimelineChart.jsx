import { useEffect, useMemo, useRef, useState } from 'react'
import { dayNum, fmtDate, fmtDateLong, fmtKm, fmtVal } from './data.js'

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
  const none = P.kind === 'none' // no series: only the pass beats and the outage
  const dates = place.dates
  const M = { l: 46, r: 18, t: 20, b: 30 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b

  const g = useMemo(() => {
    const t0 = dayNum(dates[0]), t1 = dayNum(dates[dates.length - 1])
    const x = (d) => M.l + ((dayNum(d) - t0) / (t1 - t0)) * iw
    const lo = P.fixedMin ?? 0
    const top = none ? 1 : P.fixedMax ?? niceMax(Math.max(...P.values.filter((v) => v != null), ...(P.hi || []).filter((v) => v != null)) * 1.05)
    const y = (v) => (none ? M.t + ih * 0.62 : M.t + ih - ((v - lo) / (top - lo)) * ih)
    const flood = showFlood && P.kind === 'water' && place.flood && Math.max(...place.flood.values) >= 0.04 * top ? place.flood : null
    return { x, y, top, lo, flood, t0, t1 }
  }, [dates, P, place.flood, iw, ih, showFlood, none])

  const pts = dates.map((d, i) => (none ? { i, d, x: g.x(d), y: g.y(0), v: null } : P.values[i] == null ? null : { i, d, x: g.x(d), y: g.y(P.values[i]), v: P.values[i] }))
  // dash the line where passes are far apart (an outage between them)
  const seg = []
  for (let i = 1; i < pts.length; i++) if (!none && pts[i] && pts[i - 1]) seg.push({ a: pts[i - 1], b: pts[i], gap: dayNum(dates[i]) - dayNum(dates[i - 1]) > 20 })
  const band = P.lo && P.hi ? pts.filter(Boolean) : null
  const bandPath = band && 'M' + band.map((p) => `${p.x},${g.y(P.hi[p.i])}`).join('L') + 'L' + [...band].reverse().map((p) => `${p.x},${g.y(P.lo[p.i])}`).join('L') + 'Z'
  const ticks = none ? [] : [0, 0.5, 1].map((f) => g.lo + f * (g.top - g.lo))

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
      {size && <svg width={w} height={h} role="img" aria-label={none ? `The ${dates.length} radar passes, one beat each. Drag to move through time.` : `${P.label}${P.unit ? `, in ${P.unit},` : ''} over the ${dates.length} radar passes. Drag to move through time.`}>
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
        {!none && <text x={M.l + 6} y={M.t - 7} textAnchor="start" className="ax unit">{P.unit}</text>}
        {none && <line x1={M.l} x2={w - M.r} y1={g.y(0)} y2={g.y(0)} stroke="var(--grid)" strokeWidth="1.5" />}
        {band && <g className="ax"><rect x={w - M.r - 124} y={M.t - 14} width="14" height="8" rx="2" fill="var(--water)" opacity=".4" /><text x={w - M.r - 104} y={M.t - 6} className="ax">plausible range</text></g>}
        {place.gaps.map(([a, b], i) => {
          const x0 = Math.max(M.l, g.x(a)), x1 = Math.min(w - M.r, g.x(b))
          return (
            <g key={i}>
              <rect x={x0} y={M.t} width={Math.max(2, x1 - x0)} height={ih} fill="url(#rest)" />
              <text x={(x0 + x1) / 2} y={M.t + 12} textAnchor="middle" className="ax rest-label">no passes</text>
            </g>
          )
        })}
        {bandPath && <path d={bandPath} fill="var(--water)" opacity=".28" />}
        {g.flood && (() => {
          const fp = dates.map((d, i) => ({ x: g.x(d), y: g.y(g.flood.values[i] || 0), i }))
          return (
            <g>
              <path d={'M' + fp.map((p) => `${p.x},${p.y}`).join('L')} fill="none" stroke="var(--veg)" strokeWidth="2" strokeLinejoin="round" />
              {fp.map((p) => <circle key={p.i} cx={p.x} cy={p.y} r="3" fill="var(--surface)" stroke="var(--veg)" strokeWidth="2" />)}
              {w >= 560
                ? <text x={fp[fp.length - 1].x - 6} y={fp[fp.length - 1].y - 9} textAnchor="end" className="dl" fill="var(--veg)">Flooded vegetation</text>
                : <text x={fp[0].x + 4} y={fp[0].y - 8} textAnchor="start" className="dl" fill="var(--veg)">Flooded vegetation</text>}
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
        {cur && !none && <text x={cur.x > w - 120 ? cur.x - 14 : cur.x + 14} y={Math.max(M.t + 12, Math.min(h - M.b - 6, cur.y - 12))} textAnchor={cur.x > w - 120 ? 'end' : 'start'} className="dl" fill="var(--ink)">{fmtVal(P, cur.v)}</text>}
      </svg>}
      {hp && hv !== index && (
        <div className="tip" style={{ left: Math.min(w - 170, Math.max(0, hp.x - 80)), top: Math.max(0, hp.y - 70) }}>
          <b>{fmtDateLong(hp.d)}</b>
          <span>{none ? `Pass ${hv + 1} of ${dates.length}` : fmtVal(P, hp.v)}{P.lo ? ` (${fmtKm(P.lo[hp.i])}–${fmtKm(P.hi[hp.i])})` : ''}</span>
          {q(hv) && <em>{q(hv).quality === 'fail' ? 'Flagged pass' : 'Caution'}</em>}
        </div>
      )}
    </div>
  )
}
