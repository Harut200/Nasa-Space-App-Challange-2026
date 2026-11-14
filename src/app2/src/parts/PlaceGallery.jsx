import { useMemo, useState } from 'react'
import './parts.css'
import { placeStats, thumbUrl, openWater, fmtDate, fmtNum, fmtSigned } from './util.js'

export { placeStats, thumbUrl }

const finite = (v) => typeof v === 'number' && Number.isFinite(v)

function Spark({ values, w = 120, h = 30 }) {
  const v = (values || []).map((x) => (finite(x) ? x : null))
  const ok = v.filter((x) => x != null)
  if (ok.length < 2) return <svg className="pt-spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true"><line x1="0" x2={w} y1={h - 2} y2={h - 2} className="pt-spark-base" /></svg>
  const mx = Math.max(...ok), mn = Math.min(...ok)
  const lo = Math.min(0, mn), span = mx - lo
  const X = (i) => 2 + (i / (v.length - 1)) * (w - 4)
  const Y = (x) => (span > 0 ? h - 3 - ((x - lo) / span) * (h - 6) : h - 3)
  let d = '', pen = false
  v.forEach((x, i) => { if (x == null) { pen = false; return } d += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(x).toFixed(1); pen = true })
  return (
    <svg className="pt-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" x2={w} y1={h - 2} y2={h - 2} className="pt-spark-base" />
      <path d={d} className="pt-spark-line" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

const SORTS = [
  { id: 'change', label: 'Most change' },
  { id: 'water', label: 'Most water' },
  { id: 'az', label: 'A–Z' },
]

export default function PlaceGallery({ places, currentId, onPick, base = import.meta.env.BASE_URL + 'data/' }) {
  const [sort, setSort] = useState('change')
  const rows = useMemo(() => (places || []).map((p) => ({ p, s: placeStats(p.timeseries) })), [places])
  const sorted = useMemo(() => {
    const a = [...rows]
    const name = (r) => r.p.manifest?.name || r.p.id
    if (sort === 'az') a.sort((x, y) => name(x).localeCompare(name(y)))
    else if (sort === 'water') a.sort((x, y) => (y.s.peak ?? -1) - (x.s.peak ?? -1) || name(x).localeCompare(name(y)))
    else a.sort((x, y) => Math.abs(y.s.delta ?? 0) - Math.abs(x.s.delta ?? 0) || name(x).localeCompare(name(y)))
    return a
  }, [rows, sort])

  const isQ = (p) => p.manifest?.kind === 'event' && !!p.manifest?.event
  const seasons = sorted.filter(({ p }) => !isQ(p))
  const quakes = useMemo(() => rows.filter(({ p }) => isQ(p)).sort((x, y) => String(y.p.manifest.event.time).localeCompare(String(x.p.manifest.event.time))), [rows])

  return (
    <section className="pt-gal" aria-label="Places">
      {seasons.length > 0 && (
        <>
          <h3 className="pt-gal-h">Seasons of water</h3>
          <div className="pt-gal-bar" role="group" aria-label="Sort seasons of water">
            {SORTS.map((o) => (
              <button key={o.id} type="button" className="pt-gal-sort" aria-pressed={sort === o.id} onClick={() => setSort(o.id)}>{o.label}</button>
            ))}
          </div>
          <div className="pt-gal-grid">
            {seasons.map(({ p, s }) => {
              const m = p.manifest || {}
              const url = thumbUrl({ ...m, id: m.id || p.id }, base)
              const f = openWater(p.timeseries?.facts)
              const cur = p.id === currentId
              const flat = s.delta != null && Math.abs(s.delta) < Math.max(1, (s.first || 0) * 0.005)
              return (
                <button key={p.id} type="button" className={'pt-card' + (cur ? ' pt-card-cur' : '')} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(p.id)}>
                  <span className="pt-card-img">
                    {url ? <img src={url} alt="" loading="lazy" decoding="async" onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} /> : null}
                  </span>
                  <span className="pt-card-name">{m.name || p.id}</span>
                  <Spark values={f?.values} />
                  <span className="pt-card-row">
                    <span className="pt-card-k">Peak</span>
                    <span>{s.peak != null ? <>{fmtNum(s.peak)} km²{s.peakDate ? <span className="pt-card-m"> · {fmtDate(s.peakDate)}</span> : null}</> : '–'}</span>
                  </span>
                  <span className="pt-card-row">
                    <span className="pt-card-k">Change</span>
                    <span className={flat || s.delta == null ? 'pt-card-m' : s.delta > 0 ? 'pt-card-up' : 'pt-card-down'}>
                      {s.delta == null ? '–' : flat ? 'Steady' : <>{fmtSigned(s.delta)} km²{s.pct != null ? ` (${fmtSigned(s.pct, Math.abs(s.pct) < 10 ? 1 : 0)}%)` : ''}</>}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </>
      )}
      {quakes.length > 0 && (
        <>
          <h3 className="pt-gal-h pt-gal-h2">Earthquakes</h3>
          <div className="pt-gal-grid">
            {quakes.map(({ p }) => {
              const m = p.manifest, e = m.event
              const url = thumbUrl({ ...m, id: m.id || p.id }, base)
              const cur = p.id === currentId
              const cmv = e.views?.[0]?.p2p_cm
              const mag = Number(e.magnitude)
              return (
                <button key={p.id} type="button" className={'pt-card' + (cur ? ' pt-card-cur' : '')} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(p.id)}>
                  <span className="pt-card-img">
                    {url ? <img src={url} alt="" loading="lazy" decoding="async" onError={(e2) => { e2.currentTarget.style.visibility = 'hidden' }} /> : null}
                  </span>
                  <span className="pt-card-name">{m.name || p.id}</span>
                  <span className="pt-card-row">
                    <span className="pt-card-k">{Number.isFinite(mag) ? `M ${mag.toFixed(1)}` : 'Earthquake'}</span>
                    <span className="pt-card-m">{e.time ? `${fmtDate(e.time)} ${String(e.time).slice(0, 4)}` : ''}</span>
                  </span>
                  {cmv != null && Number.isFinite(cmv) && (
                    <span className="pt-card-row">
                      <span className="pt-card-k">Ground motion</span>
                      <span className="pt-card-up">≈ {fmtNum(cmv)} cm</span>
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
