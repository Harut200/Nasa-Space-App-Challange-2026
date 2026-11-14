// Timeline strip and backscatter chart. Both place things by real date, so the
// uneven passes and the instrument gap are visible.
const t = (d) => Date.parse(d)
const W = 400
const short = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })

// Fraction 0..1 along the season for a date.
function frac(dates) {
  const t0 = t(dates[0]), span = t(dates[dates.length - 1]) - t0 || 1
  return (d) => Math.min(1, Math.max(0, (t(d) - t0) / span))
}

// Month boundaries that fall inside the season, for the labels under the strip.
function months(dates) {
  const out = [], a = new Date(dates[0]), b = t(dates[dates.length - 1])
  for (let y = a.getUTCFullYear(), mo = a.getUTCMonth() + 1; ; mo++) {
    const d = Date.UTC(y, mo, 1)
    if (d > b) break
    out.push(new Date(d).toISOString().slice(0, 10))
  }
  return out
}

// HTML, not SVG: text stays the same size on a phone and on a projector.
export function Strip({ dates, gaps, index, onPick }) {
  const f = frac(dates)
  const pct = (d) => `${(f(d) * 100).toFixed(2)}%`
  return (
    <div className="strip" role="group" aria-label="Radar passes by date">
      <div className="strip-track">
        {gaps.map(([a, b]) => (
          <div key={a} className="strip-gap" style={{ left: pct(a), width: `calc(${pct(b)} - ${pct(a)})` }}
            title={`No data: instrument outage ${short(a)} – ${short(b)}`}>
            <span>Outage<span className="wide-only"> {short(a)} – {short(b)}</span></span>
          </div>
        ))}
        {dates.map((d, i) => (
          <button key={d} type="button" className="strip-pass" style={{ left: pct(d) }}
            aria-pressed={i === index} aria-label={`Pass ${i + 1}: ${short(d)}`} title={short(d)}
            onClick={() => onPick(i)} />
        ))}
      </div>
      <div className="strip-axis" aria-hidden="true">
        {months(dates).map((d) => (
          <span key={d} className={f(d) > 0.9 ? 'end' : undefined} style={{ left: pct(d) }}>{new Date(d).toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' })}</span>
        ))}
      </div>
    </div>
  )
}

export function Chart({ ts, pol, gaps, index, onPick, colors, spots }) {
  const H = 180, L = 34, R = 8
  const f = frac(ts.dates)
  const x = (d) => L + f(d) * (W - L - R)
  const all = ts.series.flatMap((s) => s[pol] || []).filter((v) => v != null)
  if (!all.length) return null
  const unit = ts.unit || 'dB'
  const lo = Math.min(...all), hi = Math.max(...all)
  const y = (v) => H - 22 - ((v - lo) / (hi - lo || 1)) * (H - 40)
  const label = (id) => spots.find((s) => s.id === id)?.label || id
  const fmt = (v) => (Math.abs(hi - lo) >= 10 ? v.toFixed(0) : v.toFixed(1))
  return (
    <figure className="chart-figure">
      <figcaption>
        <strong>Average {pol.toUpperCase()} brightness at each spot</strong>
        <span className="chart-unit">{unit}</span>
        <ul className="chart-keys">
          {ts.series.map((s, i) => (
            <li key={s.spot}><i style={{ background: colors[i % colors.length] }} />{label(s.spot)}</li>
          ))}
        </ul>
      </figcaption>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Brightness over time, ${unit}`}>
        {gaps.map(([a, b]) => (
          <rect key={a} className="chart-gap" x={x(a)} y="8" width={x(b) - x(a)} height={H - 30} />
        ))}
        <line className="chart-grid" x1={L} x2={W - R} y1={y(hi)} y2={y(hi)} />
        <line className="chart-grid" x1={L} x2={W - R} y1={y(lo)} y2={y(lo)} />
        <text className="chart-tick" x={L - 4} y={y(hi) + 4} textAnchor="end">{fmt(hi)}</text>
        <text className="chart-tick" x={L - 4} y={y(lo) + 4} textAnchor="end">{fmt(lo)}</text>
        <text className="chart-tick" x={x(ts.dates[0])} y={H - 4}>{short(ts.dates[0])}</text>
        <text className="chart-tick" x={x(ts.dates[ts.dates.length - 1])} y={H - 4} textAnchor="end">{short(ts.dates[ts.dates.length - 1])}</text>
        <line className="chart-now" x1={x(ts.dates[index])} x2={x(ts.dates[index])} y1="4" y2={H - 20} />
        {ts.series.map((s, i) => {
          const c = colors[i % colors.length], vals = s[pol] || []
          // null = no valid pixels: break the line there
          const runs = vals.reduce((r, v, j) => {
            if (v == null) r.push([]); else r[r.length - 1].push(`${x(ts.dates[j])},${y(v)}`)
            return r
          }, [[]])
          return (
            <g key={s.spot}>
              {runs.map((r, k) => <polyline key={k} points={r.join(' ')} fill="none" stroke={c} strokeWidth="2.5" strokeLinejoin="round" />)}
              {vals.map((v, j) => v != null && (
                <circle key={j} cx={x(ts.dates[j])} cy={y(v)} r={j === index ? 6 : 4} fill={c} stroke="#0f1115" strokeWidth="1.5"
                  className="chart-dot" onClick={() => onPick(j)}>
                  <title>{label(s.spot)}, {short(ts.dates[j])}: {v} {unit}</title>
                </circle>
              ))}
            </g>
          )
        })}
      </svg>
    </figure>
  )
}
