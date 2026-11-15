// Timeline strip and backscatter chart. Both place things by real date, so the
// uneven passes and the instrument gap are visible.
import { memo, useMemo } from 'react'

const t = (d) => Date.parse(d)
const W = 600

const short = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })

function scale(dates, l = 10) {
  const t0 = t(dates[0]), span = t(dates[dates.length - 1]) - t0 || 1
  return (d) => l + ((t(d) - t0) / span) * (W - l - 10)
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

export const Chart = memo(function Chart({ ts, pol: wanted, gaps, index, onPick, colors, spots }) {
  const H = 180
  // A place without the wanted polarity (a calibrated product may carry one channel) falls back to the one it has.
  const has = (k) => ts.series.some((s) => (s[k] || []).some((v) => v != null))
  const pol = has(wanted) ? wanted : ['hh', 'hv'].find(has) || wanted
  const unit = ts.unit || 'dB'
  // everything except the cursor is independent of index
  const m = useMemo(() => {
    const x = scale(ts.dates, 44), xs = ts.dates.map(x)
    const all = ts.series.flatMap((s) => s[pol] || []).filter((v) => v != null)
    if (!all.length) return null
    const lo = Math.min(...all), hi = Math.max(...all)
    const y = (v) => H - 24 - ((v - lo) / (hi - lo || 1)) * (H - 44)
    const num = (v) => (hi - lo >= 10 ? v.toFixed(0) : v.toFixed(1)).replace(/^-(?=\d)/, '\u2212')
    const last = ts.dates.length - 1
    const axes = (
      <g className="chart-axes">
        <line x1="44" x2={W - 10} y1={y(hi)} y2={y(hi)} />
        <line x1="44" x2={W - 10} y1={y(lo)} y2={y(lo)} />
        <text x="38" y={y(hi) + 4} textAnchor="end">{num(hi)}</text>
        {hi !== lo && <text x="38" y={y(lo) + 4} textAnchor="end">{num(lo)}</text>}
        <text x={xs[0]} y={H - 4}>{short(ts.dates[0])}</text>
        {last > 0 && <text x={xs[last]} y={H - 4} textAnchor="end">{short(ts.dates[last])}</text>}
      </g>
    )
    const label = (id) => spots.find((s) => s.id === id)?.label || id
    const key = ts.series.map((s, i) => <span key={s.spot} style={{ color: colors[i % colors.length] }}>● {label(s.spot)}</span>)
    const gapEls = <Gaps gaps={gaps} x={x} h={H - 20} />
    const lines = ts.series.map((s, i) => {
      const c = colors[i % colors.length], vals = s[pol] || []
      // null = no valid pixels: break the line there
      const runs = vals.reduce((r, v, j) => {
        if (v == null) r.push([]); else r[r.length - 1].push(`${xs[j]},${y(v)}`)
        return r
      }, [[]])
      return (
        <g key={s.spot}>
          {runs.map((r, k) => <polyline key={k} points={r.join(' ')} fill="none" stroke={c} strokeWidth="2.5" strokeLinejoin="round" />)}
          {vals.map((v, j) => v != null && (
            <circle key={j} cx={xs[j]} cy={y(v)} r="4.5" fill={c} stroke="var(--bg)" strokeWidth="1.5" style={{ cursor: 'pointer' }} onClick={() => onPick(j)}>
              <title>{`${label(s.spot)}, ${short(ts.dates[j])}: ${v} ${unit}`}</title>
            </circle>
          ))}
        </g>
      )
    })
    return { xs, key, gapEls, lines, axes }
  }, [ts, pol, unit, gaps, colors, spots, onPick])
  if (!m) return null
  return (
    <figure>
      <figcaption>
        <span className="label"><strong>Average {pol.toUpperCase()} brightness at each spot</strong> · {unit}</span>
        <span className="key">{m.key}</span>
      </figcaption>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Brightness over time, ${unit}`}>
        {m.gapEls}
        {m.axes}
        <line x1={m.xs[index]} x2={m.xs[index]} y1="0" y2={H - 20} stroke="var(--teal)" strokeDasharray="3 3" />
        {m.lines}
      </svg>
    </figure>
  )
})
