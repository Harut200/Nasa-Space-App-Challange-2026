import { useMemo, useState } from 'react'
import './parts.css'
import { placeStats, thumbUrl, openWater, fmtDate, fmtNum, fmtSigned } from './util.js'
import { goodViews } from '../data.js'

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
const plain = (t) => String(t).replace(/(\d) (m|km|cm)\b/g, '$1 $2')
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s)

// What a season card shows, from summary.json when there, else from the loaded manifest and timeseries.
function seasonData(e) {
  const d = seasonCore(e)
  const caution = e.s?.caution || e.m?.caution || null
  const cap = e.s ? e.s.note : e.m?.headlineNote // a note beside a headline is a caption; with no headline it is the card text itself
  return { ...d, caution, caption: !d.note && cap ? cap : null }
}
function seasonCore(e) {
  const s = e.s, m = e.m
  if (s) {
    const h = s.headline
    if (!h) return { note: s.note || m?.headlineNote || null, name: s.name }
    const sp = (s.spark || []).filter(finite)
    if (h.unit === '%') return { name: s.name, spark: s.spark, pct: sp.length ? [sp[0], sp[sp.length - 1]] : null, label: h.label }
    return { name: s.name, spark: s.spark, peak: finite(h.value) ? h.value : null, peakDate: h.date, delta: s.change?.delta ?? null, first: s.change?.first ?? null, pctChange: s.change?.pct ?? null }
  }
  const ts = e.m.ts
  if (m.headline === 'none') return { note: m.headlineNote || null, name: m.name }
  const st = placeStats(ts), f = openWater(ts?.facts)
  const pf = (ts?.facts || []).find((x) => x.unit === '%' && (x.values || []).some(finite))
  const pv = pf && (st.peak ?? 0) < 1 ? pf.values.filter(finite) : null
  if (pv) return { name: m.name, spark: pf.values, pct: [pv[0], pv[pv.length - 1]], label: pf.label }
  return { name: m.name, spark: f?.values, peak: st.peak, peakDate: st.peakDate, delta: st.delta, first: st.first, pctChange: st.pct }
}

// A picture that prefers the small prepared thumbnail and falls back to the overlay frame.
function Pic({ e, base }) {
  const [step, setStep] = useState(0)
  const alt = e.m ? thumbUrl({ ...e.m, id: e.id }, base) : null
  const own = e.s?.thumb ? base + e.s.thumb : null
  const src = step === 0 && own ? own : alt
  if (!src || step > 1) return null
  return <img src={src} alt="" width="240" height="160" loading="lazy" decoding="async" onError={(ev) => { if (step === 0 && own && alt) setStep(1); else ev.currentTarget.style.visibility = 'hidden' }} />
}

function SeasonCard({ e, d, cur, onPick, base }) {
  const flat = d.delta != null && Math.abs(d.delta) < Math.max(1, (d.first || 0) * 0.005)
  return (
    <button type="button" className={'pt-card' + (cur ? ' pt-card-cur' : '')} data-id={e.id} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(e.id)}>
      <span className="pt-card-img"><Pic e={e} base={base} /></span>
      <span className="pt-card-name">{plain(d.name || e.id)}{d.caution && <span className="pt-card-caution" title={d.caution} aria-label={`Caution: ${d.caution}`}> ⚠︎</span>}</span>
      {d.caption && <span className="pt-card-m pt-card-fine">{d.caption}</span>}
      {d.note ? (
        <span className="pt-card-m pt-card-fine">{d.note}</span>
      ) : d.pct ? (
        <>
          <Spark values={d.spark} />
          <span className="pt-card-row"><span className="pt-card-k">Changed</span><span>{fmtNum(d.pct[0], 0)} % → {fmtNum(d.pct[1], 0)} %</span></span>
          <span className="pt-card-m pt-card-fine">{d.label}</span>
        </>
      ) : (
        <>
          <Spark values={d.spark} />
          <span className="pt-card-row">
            <span className="pt-card-k">Peak</span>
            <span>{d.peak != null ? <>{fmtNum(d.peak)} km²{d.peakDate && d.peak > 0 ? <span className="pt-card-m"> · {fmtDate(d.peakDate)}</span> : null}</> : '–'}</span>
          </span>
          <span className="pt-card-row">
            <span className="pt-card-k">Change</span>
            <span className={flat || d.delta == null ? 'pt-card-m' : d.delta > 0 ? 'pt-card-up' : 'pt-card-down'}>
              {d.delta == null ? '–' : flat ? 'Steady' : <>{fmtSigned(d.delta)}&nbsp;km²{d.pctChange != null ? <span className="pt-nw"> ({fmtSigned(d.pctChange, Math.abs(d.pctChange) < 10 ? 1 : 0)}&nbsp;%)</span> : ''}</>}
            </span>
          </span>
        </>
      )}
    </button>
  )
}

// Events: magnitude only where there is one; the ground-motion figure only from a view that is not too noisy (needs the manifest).
function EventCard({ e, cur, onPick, base }) {
  const ev = e.m?.event, h = e.s?.headline || {}
  const type = ev?.type || e.s?.eventType || e.category
  const mag = Number(ev ? ev.magnitude : h.value)
  const hasMag = (ev ? ev.magnitude : h.value) != null && Number.isFinite(mag)
  const time = ev?.time || h.date
  const summary = ev ? ev.summary : h.summary
  const cmv = ev ? goodViews(ev)[0]?.p2p_cm : null
  const name = e.m?.name || e.s?.name || e.id
  return (
    <button type="button" className={'pt-card' + (cur ? ' pt-card-cur' : '')} data-id={e.id} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(e.id)}>
      <span className="pt-card-img"><Pic e={e} base={base} /></span>
      <span className="pt-card-name">{plain(name)}</span>
      <span className="pt-card-row">
        <span className="pt-card-k">{hasMag ? `M ${mag.toFixed(1)}` : cap(String(type).replace(/[-_]/g, ' '))}</span>
        <span className="pt-card-m">{time ? `${fmtDate(time)} ${String(time).slice(0, 4)}` : ''}</span>
      </span>
      {summary && !hasMag && <span className="pt-card-row"><span className="pt-card-up">{summary}</span></span>}
      {cmv != null && Number.isFinite(cmv) && (
        <span className="pt-card-row">
          <span className="pt-card-k">Ground motion</span>
          <span className="pt-card-up">≈ {fmtNum(cmv)} cm</span>
        </span>
      )}
    </button>
  )
}

export default function PlaceGallery({ classes, entries, currentId, onPick, base = import.meta.env.BASE_URL + 'data/' }) {
  const [sort, setSort] = useState('change')
  const rows = useMemo(() => (entries || []).map((e) => ({ e, d: e.event ? null : seasonData(e) })), [entries])
  const groups = useMemo(() => (classes || []).map((c) => {
    const name = (r) => r.e.s?.name || r.e.m?.name || r.e.id
    const when = (r) => String(r.e.m?.event?.time || r.e.s?.headline?.date || '')
    const seasons = rows.filter((r) => r.e.category === c.id && !r.e.event)
    if (sort === 'az') seasons.sort((x, y) => name(x).localeCompare(name(y)))
    else if (sort === 'water') seasons.sort((x, y) => (y.d?.peak ?? -1) - (x.d?.peak ?? -1) || name(x).localeCompare(name(y)))
    else seasons.sort((x, y) => Math.abs(y.d?.delta ?? 0) - Math.abs(x.d?.delta ?? 0) || name(x).localeCompare(name(y)))
    const events = rows.filter((r) => r.e.category === c.id && r.e.event).sort((x, y) => when(y).localeCompare(when(x)))
    const list = [...seasons, ...events] // a class that has both kinds: the seasons first, then the single events
    return { ...c, list }
  }), [classes, rows, sort])
  const hasSeasons = rows.some((r) => !r.e.event && !r.d.note)

  return (
    <section className="pt-gal" aria-label="Places">
      {hasSeasons && (
        <div className="pt-gal-bar" role="group" aria-label="Sort the seasons of water">
          {SORTS.map((o) => (
            <button key={o.id} type="button" className="pt-gal-sort" aria-pressed={sort === o.id} onClick={() => setSort(o.id)}>{o.label}</button>
          ))}
        </div>
      )}
      {groups.map((g, gi) => (
        <div key={g.id} data-class={g.id}>
          <h3 className={'pt-gal-h' + (gi ? ' pt-gal-h2' : '')}>{g.label} <span className="pt-gal-n mono">{g.list.length}</span></h3>
          <div className="pt-gal-grid">
            {g.list.map(({ e, d }) => (e.event
              ? <EventCard key={e.id} e={e} cur={e.id === currentId} onPick={onPick} base={base} />
              : <SeasonCard key={e.id} e={e} d={d} cur={e.id === currentId} onPick={onPick} base={base} />))}
          </div>
        </div>
      ))}
    </section>
  )
}
