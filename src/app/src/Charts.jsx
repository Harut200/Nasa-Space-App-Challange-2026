// Timeline strip and backscatter chart. Both place things by real date, so the
// uneven passes and the instrument gap are visible.
import { memo, useMemo } from 'react'

const t = (d) => Date.parse(d)
const W = 600

function scale(dates) {
  const t0 = t(dates[0]), span = t(dates[dates.length - 1]) - t0 || 1
  return (d) => 10 + ((t(d) - t0) / span) * (W - 20)
}

function Gaps({ gaps, x, h }) {
  return gaps.map(([a, b]) => (
    <g key={a}>
      <rect x={x(a)} y="0" width={x(b) - x(a)} height={h} fill="currentColor" opacity="0.14" />
      <text x={(x(a) + x(b)) / 2} y="11" fontSize="9" textAnchor="middle" fill="currentColor" fontFamily="var(--mono)">no data (instrument)</text>
    </g>
  ))
}

// Only the old and new active tick re-render when index changes.
const Tick = memo(function Tick({ d, i, px, on, onPick }) {
  return (
    <g onClick={() => onPick(i)} style={{ cursor: 'pointer' }}>
      <line x1={px} x2={px} y1="0" y2="30" stroke="transparent" strokeWidth="14"><title>{d}</title></line>
      <line x1={px} x2={px} y1={on ? 12 : 18} y2="30" stroke={on ? 'var(--text)' : 'currentColor'}
        strokeWidth={on ? 3 : 1.5} />
    </g>
  )
})

export const Strip = memo(function Strip({ dates, gaps, index, onPick }) {
  const [x, xs] = useMemo(() => { const x = scale(dates); return [x, dates.map(x)] }, [dates])
  return (
    <svg className="strip" viewBox={`0 0 ${W} 30`} role="img" aria-label="Radar passes by date">
      <Gaps gaps={gaps} x={x} h={30} />
      {dates.map((d, i) => <Tick key={d} d={d} i={i} px={xs[i]} on={i === index} onPick={onPick} />)}
    </svg>
  )
})

export const Chart = memo(function Chart({ ts, pol, gaps, index, onPick, colors, spots }) {
  const H = 160
  // everything except the cursor is independent of index
  const m = useMemo(() => {
    const x = scale(ts.dates), xs = ts.dates.map(x)
    const all = ts.series.flatMap((s) => s[pol] || []).filter((v) => v != null)
    if (!all.length) return null
    const lo = Math.min(...all), hi = Math.max(...all)
    const y = (v) => H - 15 - ((v - lo) / (hi - lo || 1)) * (H - 35)
    const label = (id) => spots.find((s) => s.id === id)?.label || id
    const key = ts.series.map((s, i) => <span key={s.spot} style={{ color: colors[i % colors.length] }}>● {label(s.spot)}</span>)
    const gapEls = <Gaps gaps={gaps} x={x} h={H} />
    const lines = ts.series.map((s, i) => {
      const c = colors[i % colors.length], vals = s[pol] || []
      // null = no valid pixels: break the line there
      const runs = vals.reduce((r, v, j) => {
        if (v == null) r.push([]); else r[r.length - 1].push(`${xs[j]},${y(v)}`)
        return r
      }, [[]])
      return (
        <g key={s.spot}>
          {runs.map((r, k) => <polyline key={k} points={r.join(' ')} fill="none" stroke={c} strokeWidth="2" />)}
          {vals.map((v, j) => v != null && (
            <circle key={j} cx={xs[j]} cy={y(v)} r="4.5" fill={c} stroke="var(--bg)" strokeWidth="1.5" style={{ cursor: 'pointer' }} onClick={() => onPick(j)}>
              <title>{`${label(s.spot)}, ${ts.dates[j]}: ${v} dB`}</title>
            </circle>
          ))}
        </g>
      )
    })
    return { xs, lo, hi, key, gapEls, lines }
  }, [ts, pol, gaps, colors, spots, onPick])
  if (!m) return null
  return (
    <figure>
      <figcaption>
        <span className="label">Average {pol.toUpperCase()} backscatter (dB), {m.lo.toFixed(1)} to {m.hi.toFixed(1)}</span>
        <span className="key">{m.key}</span>
      </figcaption>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Backscatter over time">
        {m.gapEls}
        <line x1={m.xs[index]} x2={m.xs[index]} y1="0" y2={H} stroke="var(--text)" strokeDasharray="2 4" />
        {m.lines}
      </svg>
    </figure>
  )
})
