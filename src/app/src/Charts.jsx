// Timeline strip and backscatter chart. Both place things by real date, so the
// uneven passes and the instrument gap are visible.
const t = (d) => Date.parse(d)
const W = 600

function scale(dates) {
  const t0 = t(dates[0]), span = t(dates[dates.length - 1]) - t0 || 1
  return (d) => 10 + ((t(d) - t0) / span) * (W - 20)
}

function Gaps({ gaps, x, h }) {
  return gaps.map(([a, b]) => (
    <g key={a}>
      <rect x={x(a)} y="0" width={x(b) - x(a)} height={h} fill="currentColor" opacity="0.2" />
      <text x={(x(a) + x(b)) / 2} y="10" fontSize="9" textAnchor="middle" fill="currentColor">no data (instrument)</text>
    </g>
  ))
}

export function Strip({ dates, gaps, index, onPick }) {
  const x = scale(dates)
  return (
    <svg className="strip" viewBox={`0 0 ${W} 30`} role="img" aria-label="Radar passes by date">
      <Gaps gaps={gaps} x={x} h={30} />
      {dates.map((d, i) => (
        <line key={d} x1={x(d)} x2={x(d)} y1={i === index ? 12 : 18} y2="30" stroke="currentColor"
          strokeWidth={i === index ? 3 : 1} onClick={() => onPick(i)}><title>{d}</title></line>
      ))}
    </svg>
  )
}

export function Chart({ ts, pol, gaps, index, onPick, colors, spots }) {
  const H = 160, x = scale(ts.dates)
  const all = ts.series.flatMap((s) => s[pol] || []).filter((v) => v != null)
  if (!all.length) return null
  const lo = Math.min(...all), hi = Math.max(...all)
  const y = (v) => H - 15 - ((v - lo) / (hi - lo || 1)) * (H - 35)
  const label = (id) => spots.find((s) => s.id === id)?.label || id
  return (
    <figure>
      <figcaption>
        Average {pol.toUpperCase()} backscatter (dB), {lo.toFixed(1)} to {hi.toFixed(1)}:{' '}
        {ts.series.map((s, i) => <span key={s.spot} style={{ color: colors[i % colors.length] }}>■ {label(s.spot)} </span>)}
      </figcaption>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Backscatter over time">
        <Gaps gaps={gaps} x={x} h={H} />
        <line x1={x(ts.dates[index])} x2={x(ts.dates[index])} y1="0" y2={H} stroke="currentColor" strokeDasharray="3" />
        {ts.series.map((s, i) => {
          const c = colors[i % colors.length], vals = s[pol] || []
          // null = no valid pixels: break the line there
          const runs = vals.reduce((r, v, j) => {
            if (v == null) r.push([]); else r[r.length - 1].push(`${x(ts.dates[j])},${y(v)}`)
            return r
          }, [[]])
          return (
            <g key={s.spot}>
              {runs.map((r, k) => <polyline key={k} points={r.join(' ')} fill="none" stroke={c} strokeWidth="2" />)}
              {vals.map((v, j) => v != null && (
                <circle key={j} cx={x(ts.dates[j])} cy={y(v)} r="5" fill={c} onClick={() => onPick(j)}>
                  <title>{label(s.spot)}, {ts.dates[j]}: {v} dB</title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>
    </figure>
  )
}
