import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { dayNum, fmtDate, fmtKm } from './data.js'

const useSize = (ref, minH) => {
  const [s, setS] = useState(null)
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setS({ w: Math.max(220, e.contentRect.width), h: Math.max(minH, e.contentRect.height) }))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref, minH])
  return s
}

const nice = (lo, hi) => {
  const span = Math.max(1, hi - lo), p = Math.pow(10, Math.floor(Math.log10(span / 3))), f = span / 3 / p
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p
  return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step }
}

/** The evidence curve: the echo inside the changed patch against the echo outside it, the passes before and after shaded, flagged passes marked. */
export default function EvidenceChart({ place, index, onScrub, compact }) {
  const box = useRef(null)
  const uid = useId().replace(/:/g, '')
  const size = useSize(box, compact ? 150 : 110)
  const { w, h } = size || { w: 400, h: compact ? 170 : 180 }
  const e = place.evi, st = e.stats || {}
  const dates = place.dates
  const M = { l: 40, r: compact ? 12 : 64, t: 26, b: 26 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b
  const inside = e.inside || [], outside = e.outside || []
  const g = useMemo(() => {
    const all = [...inside, ...outside].filter((v) => v != null && Number.isFinite(v))
    const sc = nice(Math.min(...all) - 1, Math.max(...all) + 1)
    const t0 = dayNum(dates[0]), t1 = dayNum(dates[dates.length - 1])
    const x = (d) => M.l + ((dayNum(d) - t0) / (t1 - t0 || 1)) * iw
    const y = (v) => M.t + ih - ((v - sc.lo) / (sc.hi - sc.lo)) * ih
    return { ...sc, x, y }
  }, [dates, inside, outside, iw, ih]) // eslint-disable-line react-hooks/exhaustive-deps
  const group = (ds) => {
    if (!ds || !ds.length) return null
    const a = g.x(ds[0]), b = g.x(ds[ds.length - 1]), pad = 9
    return { x0: a - pad, x1: Math.max(b + pad, a + pad) }
  }
  const gb = group(st.before_dates), ga = group(st.after_dates)
  const excl = new Set(st.excluded_dates || [])
  const path = (arr) => {
    const pts = arr.map((v, i) => (v == null ? null : [g.x(dates[i]), g.y(v), i])).filter(Boolean)
    return { pts, d: pts.length ? 'M' + pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('L') : '' }
  }
  const A = path(inside), B = path(outside)
  const ticks = [g.lo, (g.lo + g.hi) / 2, g.hi]
  const every = w < 420 ? 2 : 1
  const at = (clientX) => {
    const r = box.current.getBoundingClientRect(), px = clientX - r.left
    let best = 0, bd = 1e9
    dates.forEach((d, i) => { const dd = Math.abs(g.x(d) - px); if (dd < bd) { bd = dd; best = i } })
    return best
  }
  const cur = index != null && index >= 0 ? dates[index] : null
  const lastA = A.pts.at(-1), lastB = B.pts.at(-1)
  // keep the two end labels apart
  let ya = lastA ? lastA[1] : 0, yb = lastB ? lastB[1] : 0
  if (Math.abs(ya - yb) < 13) { const m = (ya + yb) / 2; if (ya <= yb) { ya = m - 7; yb = m + 7 } else { ya = m + 7; yb = m - 7 } }
  const val = (arr) => (arr[index] != null ? arr[index].toFixed(1) : null)
  return (
    <div className={`ev-wrap ${compact ? 'compact' : ''}`}>
    <div className={`chart ev-chart ${compact ? 'compact' : ''}`} ref={box} data-ready={!!size} style={compact ? { height: 170 } : undefined}
      onPointerDown={(ev) => onScrub && onScrub(at(ev.clientX))}
      onPointerMove={(ev) => { if (ev.buttons && onScrub) onScrub(at(ev.clientX)) }}>
      {size && <svg width={w} height={h} role="img" aria-label={`Radar echo inside and outside the changed patch over the ${dates.length} passes; passes before and after the event are shaded.`}>
        <defs>
          <pattern id={`rest${uid}`} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="7" stroke="var(--ink-3)" strokeWidth="1.2" opacity=".5" />
          </pattern>
        </defs>
        {ticks.map((v) => (
          <g key={v}><line x1={M.l} x2={w - M.r} y1={g.y(v)} y2={g.y(v)} stroke="var(--grid)" /><text x={M.l - 7} y={g.y(v) + 4} textAnchor="end" className="ax">{fmtKm(+v.toFixed(1))}</text></g>
        ))}
        {gb && <g><rect x={gb.x0} y={M.t} width={gb.x1 - gb.x0} height={ih} fill="var(--water)" opacity=".13" /><text x={gb.x0 + 4} y={M.t - 8} className="ax">before</text></g>}
        {ga && <g><rect x={ga.x0} y={M.t} width={ga.x1 - ga.x0} height={ih} fill="var(--accent)" opacity=".11" /><text x={ga.x0 + 4} y={M.t - 8} className="ax">after</text></g>}
        {place.gaps.map(([a, b], i) => { const x0 = Math.max(M.l, g.x(a)), x1 = Math.min(w - M.r, g.x(b)); return x1 > x0 ? <rect key={i} x={x0} y={M.t} width={Math.max(2, x1 - x0)} height={ih} fill={`url(#rest${uid})`} /> : null })}
        <path d={B.d} fill="none" stroke="var(--ink-2)" strokeWidth="2.2" strokeLinejoin="round" strokeDasharray="5 4" />
        <path d={A.d} fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinejoin="round" />
        {B.pts.map((p) => <circle key={p[2]} cx={p[0]} cy={p[1]} r="3" fill="var(--ink-2)" />)}
        {A.pts.map((p) => <circle key={p[2]} cx={p[0]} cy={p[1]} r={p[2] === index ? 5 : 3.5} fill="var(--accent)" stroke="var(--surface)" strokeWidth="1.5" />)}
        {dates.map((d, i) => excl.has(d) && <g key={d}><circle cx={g.x(d)} cy={M.t + ih - 6} r="4.5" fill="none" stroke="var(--warn)" strokeWidth="1.6" /><path d={`M${g.x(d) - 2.5},${M.t + ih - 8.5}l5,5m0,-5l-5,5`} stroke="var(--warn)" strokeWidth="1.4" /></g>)}
        {cur && <line x1={g.x(cur)} x2={g.x(cur)} y1={M.t} y2={M.t + ih} stroke="var(--accent)" strokeWidth="1" opacity=".55" />}
        {dates.map((d, i) => (i % every === 0 || i === index) && <text key={d} x={g.x(d)} y={h - 7} textAnchor="middle" className={`ax ${i === index ? 'now' : ''}`}>{fmtDate(d)}</text>)}
        {!compact && lastA && <text x={lastA[0] + 9} y={ya + 4} className="dl" fill="var(--accent)">inside</text>}
        {!compact && lastB && <text x={lastB[0] + 9} y={yb + 4} className="dl" fill="var(--ink-2)">outside</text>}
      </svg>}
    </div>
      <div className="ev-key mono">
        <span><i style={{ background: 'var(--accent)' }} />inside the patch{index != null && val(inside) ? ` ${val(inside)}` : ''}</span>
        <span><i className="dash" />outside{index != null && val(outside) ? ` ${val(outside)}` : ''}</span>
        {excl.size > 0 && <span><b className="ex">×</b>left out</span>}
      </div>
    </div>
  )
}
