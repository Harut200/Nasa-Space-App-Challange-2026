import { useEffect, useId, useMemo, useRef, useState } from 'react'
import './parts.css'
import {
  dayNum, fmtDate, fmtNum, niceMax, makeXScale, nearestIndex, monthTicks,
  openWater, floodedVeg, qualityFor,
} from './util.js'

const finite = (v) => typeof v === 'number' && Number.isFinite(v)

export default function TimelineChart({ dates, facts, gaps, quality, index, onIndex, playing, height = 150, initialWidth = 640 }) {
  const uid = useId().replace(/:/g, '')
  const wrap = useRef(null)
  const svgRef = useRef(null)
  const drag = useRef(false)
  const [W, setW] = useState(initialWidth)

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const set = () => { const w = el.clientWidth; if (w > 0) setW(Math.max(260, Math.round(w))) }
    set()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const g = useMemo(() => {
    const ds = dates || []
    const H = height
    const m = { l: 40, r: 14, t: 24, b: 20 }
    const x0 = m.l, x1 = W - m.r, y0 = m.t, y1 = H - m.b
    const sx = makeXScale(ds, x0 + 6, x1 - 6)
    const ow = openWater(facts), fv = floodedVeg(facts)
    const owV = ow?.values || []
    const showFv = !!fv && (fv.values || []).some((v) => finite(v) && v > 0)
    let mx = 0
    const take = (arr) => (arr || []).forEach((v) => { if (finite(v) && v > mx) mx = v })
    take(owV); take(ow?.hi); if (showFv) take(fv.values)
    const ymax = niceMax(mx)
    const sy = (v) => y1 - (v / ymax) * (y1 - y0)
    const xs = sx.days.map(sx.f)
    const pts = (arr) => arr.map((v, i) => (finite(v) && xs[i] != null ? [xs[i], sy(v)] : null))
    const path = (p) => {
      let d = '', pen = false
      p.forEach((q) => { if (!q) { pen = false; return } d += (pen ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1); pen = true })
      return d
    }
    const owP = pts(owV)
    const hasBand = Array.isArray(ow?.lo) && Array.isArray(ow?.hi) && ow.lo.length === owV.length &&
      ds.some((_, i) => finite(ow.lo[i]) && finite(ow.hi[i]) && ow.hi[i] - ow.lo[i] > 0)
    let band = ''
    if (hasBand) {
      const up = [], dn = []
      ds.forEach((_, i) => { if (finite(ow.lo[i]) && finite(ow.hi[i])) { up.push([xs[i], sy(ow.hi[i])]); dn.push([xs[i], sy(ow.lo[i])]) } })
      band = 'M' + up.map((q) => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join('L') + 'L' + dn.reverse().map((q) => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join('L') + 'Z'
    }
    const gapRects = (gaps || []).map(([a, b]) => {
      if (!a || !b || !(ds.length > 1)) return null
      const ga = Math.max(dayNum(a), sx.a), gb = Math.min(dayNum(b), sx.b)
      if (!(gb > ga)) return null
      return { xa: sx.f(ga), xb: sx.f(gb), a, b }
    }).filter(Boolean)
    return { H, m, x0, x1, y0, y1, ymax, sy, xs, sx, owP, owPath: path(owP), fvP: showFv ? pts(fv.values) : null, fvPath: showFv ? path(pts(fv.values)) : '', band, gapRects, ow, ticks: monthTicks(sx.a, sx.b) }
  }, [dates, facts, gaps, W, height])

  const n = (dates || []).length
  const i = n ? Math.min(Math.max(index || 0, 0), n - 1) : 0
  const cur = g.owP[i]
  const curX = g.xs[i] ?? g.x0
  const curVal = g.ow?.values?.[i]
  const label = n ? `${finite(curVal) ? fmtNum(curVal) + ' km² · ' : ''}${fmtDate(dates[i])}` : ''
  const q = n ? qualityFor(quality, dates[i]) : null
  const valueText = n ? `Pass ${i + 1} of ${n}, ${fmtDate(dates[i])}${finite(curVal) ? ', ' + fmtNum(curVal) + ' square kilometres of open water' : ''}${q ? ', quality ' + q.level : ''}` : 'No passes'

  const pick = (e) => {
    if (!n || !onIndex) return
    const r = svgRef.current.getBoundingClientRect()
    const x = ((e.clientX - r.left) / (r.width || 1)) * W
    const k = nearestIndex(g.xs, x)
    if (k !== i) onIndex(k)
  }
  const onKey = (e) => {
    if (!n || !onIndex) return
    let k = null
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') k = Math.max(0, i - 1)
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') k = Math.min(n - 1, i + 1)
    else if (e.key === 'Home') k = 0
    else if (e.key === 'End') k = n - 1
    if (k != null) { e.preventDefault(); if (k !== i) onIndex(k) }
  }

  const half = label.length * 3.1 + 6
  const lx = Math.min(Math.max(curX, g.x0 + half), g.x1 + 4 - half) - curX
  const yTicks = [0, g.ymax / 2, g.ymax]
  const colW = (k) => {
    const l = k === 0 ? g.xs[0] - 14 : (g.xs[k - 1] + g.xs[k]) / 2
    const r = k === n - 1 ? g.xs[k] + 14 : (g.xs[k] + g.xs[k + 1]) / 2
    return [l, Math.max(r - l, 8)]
  }
  const gap0 = g.gapRects[0]
  const gapLabel = gap0 ? `No passes ${fmtDate(gap0.a)} – ${fmtDate(gap0.b)}` : ''
  const gapMid = gap0 ? (gap0.xa + gap0.xb) / 2 : 0
  const gl = gap0 ? Math.min(Math.max(gapMid, g.x0 + gapLabel.length * 2.6), g.x1 - gapLabel.length * 2.6) : 0

  return (
    <div ref={wrap} className={'pt-tl' + (playing ? ' pt-playing' : '')}>
      <div
        className="pt-tl-slider"
        role="slider"
        tabIndex={n ? 0 : -1}
        aria-label="Satellite pass timeline"
        aria-orientation="horizontal"
        aria-valuemin={0}
        aria-valuemax={Math.max(0, n - 1)}
        aria-valuenow={i}
        aria-valuetext={valueText}
        onKeyDown={onKey}
      >
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${g.H}`}
          width="100%"
          height={g.H}
          className="pt-tl-svg"
          onPointerDown={(e) => { drag.current = true; svgRef.current.setPointerCapture?.(e.pointerId); wrap.current.classList.add('pt-dragging'); pick(e) }}
          onPointerMove={(e) => { if (drag.current) pick(e) }}
          onPointerUp={() => { drag.current = false; wrap.current?.classList.remove('pt-dragging') }}
          onPointerCancel={() => { drag.current = false; wrap.current?.classList.remove('pt-dragging') }}
        >
          <defs>
            <pattern id={`pt-h-${uid}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" className="pt-tl-hatch" />
            </pattern>
          </defs>

          {yTicks.map((v, k) => (
            <g key={k}>
              <line x1={g.x0} x2={g.x1} y1={g.sy(v)} y2={g.sy(v)} className={k === 0 ? 'pt-tl-base' : 'pt-tl-grid'} />
              <text x={g.x0 - 6} y={g.sy(v) + 3} textAnchor="end" className="pt-tl-ytxt">{fmtNum(v, v > 0 && g.ymax < 10 ? 1 : 0)}</text>
            </g>
          ))}
          <text x={g.x0 - 6} y={g.y0 - 10} textAnchor="end" className="pt-tl-ytxt">km²</text>

          {g.gapRects.map((r, k) => (
            <rect key={k} x={r.xa} y={g.y0} width={r.xb - r.xa} height={g.y1 - g.y0} fill={`url(#pt-h-${uid})`} className="pt-tl-gap">
              <title>{`No passes ${fmtDate(r.a)} – ${fmtDate(r.b)} (instrument outage)`}</title>
            </rect>
          ))}

          {g.ticks.map((t) => {
            const x = g.sx.f(t.day)
            return (
              <g key={t.day}>
                <line x1={x} x2={x} y1={g.y1} y2={g.y1 + 4} className="pt-tl-grid" />
                <text x={x} y={g.H - 5} textAnchor="middle" className="pt-tl-xtxt">{t.label}</text>
              </g>
            )
          })}

          {g.band && <path d={g.band} className="pt-tl-band" />}
          {g.fvPath && <path d={g.fvPath} className="pt-tl-fv" />}
          {g.owPath && <path d={g.owPath} className="pt-tl-line" />}

          {gap0 && <text x={gl} y={g.y1 - 6} textAnchor="middle" className="pt-tl-gaptxt">{gapLabel}</text>}

          {/* playhead */}
          {n > 0 && (
            <g className="pt-tl-head" style={{ transform: `translateX(${curX}px)` }}>
              <line x1="0" x2="0" y1={g.y0 - 4} y2={g.y1} className="pt-tl-headline" />
              <text x={lx} y={g.y0 - 9} textAnchor="middle" className="pt-tl-val">{label}</text>
            </g>
          )}

          {/* beats */}
          {(dates || []).map((d, k) => {
            const p = g.owP[k]
            const qq = qualityFor(quality, d)
            const [cx, cw] = n ? colW(k) : [0, 0]
            const isCur = k === i
            return (
              <g key={d + k} className="pt-tl-beat">
                <rect x={cx} y={g.y0 - 6} width={cw} height={g.y1 - g.y0 + 6} fill="transparent" className="pt-tl-hit">
                  <title>{`${fmtDate(d)}${finite(g.ow?.values?.[k]) ? ' · ' + fmtNum(g.ow.values[k]) + ' km²' : ''}${qq ? ` · ${qq.level === 'fail' ? 'Fail' : 'Warning'}: ${qq.note}` : ''}`}</title>
                </rect>
                {p && (
                  <circle
                    cx={p[0]} cy={p[1]} r={isCur ? 5.5 : 3.4}
                    className={'pt-tl-dot' + (qq ? ' pt-tl-dot-' + qq.level : '') + (isCur ? ' pt-tl-dot-cur' : '')}
                  />
                )}
              </g>
            )
          })}
          {n === 0 && <text x={W / 2} y={g.H / 2} textAnchor="middle" className="pt-tl-ytxt">No passes</text>}
        </svg>
      </div>
    </div>
  )
}
