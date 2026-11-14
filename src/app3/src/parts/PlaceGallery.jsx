import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { classInfo, classOf, classesIn } from '../data.js'
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
const plain = (t) => String(t).replace(/(\d) (m|km|cm)\b/g, '$1\u00a0$2')

// A picture that prefers the small prepared thumbnail and falls back to the overlay frame.
function Pic({ id, manifest, base }) {
  const [step, setStep] = useState(0)
  const alt = thumbUrl({ ...manifest, id }, base)
  const src = step === 0 ? `${base}thumbs/${id}.webp` : alt
  if (step > 1 || !src) return null
  return <img src={src} alt="" width="240" height="160" loading="lazy" decoding="async" onError={(e) => { if (step === 0 && alt) setStep(1); else e.currentTarget.style.visibility = 'hidden' }} />
}

function SeasonCard({ r, currentId, onPick, base }) {
  const { p, s } = r, m = p.manifest || {}
  const f = openWater(p.timeseries?.facts)
  const cur = p.id === currentId
  const pf = (p.timeseries?.facts || []).find((x) => x.unit === '%' && (x.values || []).some(finite))
  const pv = pf && (s.peak ?? 0) < 1 ? pf.values.filter(finite) : null
  const none = m.headline === 'none'
  const flat = s.delta != null && Math.abs(s.delta) < Math.max(1, (s.first || 0) * 0.005)
  return (
    <button type="button" data-id={p.id} className={'pt-card' + (cur ? ' pt-card-cur' : '')} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(p.id)}>
      <span className="pt-card-img"><Pic id={p.id} manifest={m} base={base} /></span>
      <span className="pt-card-name" title={m.name || p.id}>{plain(m.name || p.id)}</span>
      {!none && <Spark values={pv ? pf.values : f?.values} />}
      {none ? (
        <span className="pt-card-row"><span className="pt-card-m">{m.headlineNote || 'Radar echo changed'}</span></span>
      ) : pv ? (
        <span className="pt-card-row"><span>{fmtNum(pv[0], 0)} % → {fmtNum(pv[pv.length - 1], 0)} %</span><span className="pt-card-m">{pf.label}</span></span>
      ) : (
        <span className="pt-card-row">
          <span>{s.peak != null ? <>{fmtNum(s.peak)} km²{s.peakDate && s.peak > 0 ? <span className="pt-card-m"> · {fmtDate(s.peakDate)}</span> : null}</> : '–'}</span>
          <span className={flat || s.delta == null ? 'pt-card-m' : s.delta > 0 ? 'pt-card-up' : 'pt-card-down'}>{s.delta == null ? '–' : flat ? 'Steady' : <>{fmtSigned(s.delta)}&nbsp;km²</>}</span>
        </span>
      )}
    </button>
  )
}

function EventCard({ p, currentId, onPick, base }) {
  const m = p.manifest, e = m.event
  const cur = p.id === currentId
  const cmv = e.views?.[0]?.p2p_cm
  const mag = Number(e.magnitude)
  const label = Number.isFinite(mag) && e.magnitude != null ? `M ${mag.toFixed(1)}` : classInfo(classOf(m)).label.replace(/(es|s)$/, '')
  return (
    <button type="button" data-id={p.id} className={'pt-card' + (cur ? ' pt-card-cur' : '')} aria-current={cur ? 'true' : undefined} onClick={() => onPick && onPick(p.id)}>
      <span className="pt-card-img"><Pic id={p.id} manifest={m} base={base} /></span>
      <span className="pt-card-name" title={m.name || p.id}>{plain(m.name || p.id)}</span>
      <span className="pt-card-row">
        <span>{label}{e.time ? <span className="pt-card-m"> · {fmtDate(e.time)}</span> : null}</span>
        {cmv != null && Number.isFinite(cmv) ? <span className="pt-card-up">≈ {fmtNum(cmv)} cm</span> : <span />}
      </span>
    </button>
  )
}

export default function PlaceGallery({ places, currentId, onPick, base = import.meta.env.BASE_URL + 'data/' }) {
  const [sort, setSort] = useState('change')
  const rows = useMemo(() => (places || []).map((p) => ({ p, s: placeStats(p.timeseries) })), [places])
  const name = (r) => r.p.manifest?.name || r.p.id
  const sortRows = (list, isEv) => {
    const a = [...list]
    if (isEv) return a.sort((x, y) => String(y.p.manifest.event.time).localeCompare(String(x.p.manifest.event.time)))
    if (sort === 'az') a.sort((x, y) => name(x).localeCompare(name(y)))
    else if (sort === 'water') a.sort((x, y) => (y.s.peak ?? -1) - (x.s.peak ?? -1) || name(x).localeCompare(name(y)))
    else a.sort((x, y) => Math.abs(y.s.delta ?? 0) - Math.abs(x.s.delta ?? 0) || name(x).localeCompare(name(y)))
    return a
  }
  // sections: one per class present, in display order, titled with the class label
  const groups = useMemo(() => classesIn(rows.map((r) => r.p.manifest)).map((c) => ({ ...c, list: sortRows(rows.filter((r) => classOf(r.p.manifest) === c.id), c.id === 'earthquake' || c.id === 'volcano' || c.id === 'landslide' || !!rows.find((r) => classOf(r.p.manifest) === c.id)?.p.manifest.event) })), [rows, sort]) // eslint-disable-line react-hooks/exhaustive-deps
  const hasSeasons = groups.some((g) => g.list.some((r) => !r.p.manifest.event))

  // FLIP: cards that stay glide to their new place, new cards rise in
  const root = useRef(null), last = useRef(new Map())
  useLayoutEffect(() => {
    const els = [...root.current.querySelectorAll('[data-id]')], now = new Map()
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    for (const el of els) {
      const b = el.getBoundingClientRect(); now.set(el.dataset.id, b)
      const o = last.current.get(el.dataset.id)
      if (reduce || !last.current.size) continue
      if (o && (Math.abs(o.left - b.left) > 1 || Math.abs(o.top - b.top) > 1)) el.animate([{ transform: `translate(${o.left - b.left}px, ${o.top - b.top}px)` }, { transform: 'none' }], { duration: 460, easing: 'cubic-bezier(0.65, 0, 0.35, 1)' })
      else if (!o) el.animate([{ opacity: 0, transform: 'scale(.96)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' })
    }
    last.current = now
  })

  return (
    <section className="pt-gal" aria-label="Places" ref={root}>
      {groups.map((g, gi) => (
        <div key={g.id} data-class={g.id}>
          <div className={'pt-gal-head' + (gi ? ' pt-gal-h2' : '')}>
            <h3 className="pt-gal-h"><KindMark shape={g.shape} color={g.color} />{g.label} <span className="pt-gal-n mono">{g.list.length}</span></h3>
            {hasSeasons && g.list.some((r) => !r.p.manifest.event) && g.list.length > 1 && (
              <button type="button" className="pt-gal-sorttoggle" onClick={() => setSort(SORTS[(SORTS.findIndex((o) => o.id === sort) + 1) % SORTS.length].id)} title="Change the order of the cards">Sorted by {SORTS.find((o) => o.id === sort).label.toLowerCase()}</button>
            )}
          </div>
          <div className="pt-gal-grid">
            {g.list.map((r) => (r.p.manifest.event ? <EventCard key={r.p.id} p={r.p} currentId={currentId} onPick={onPick} base={base} /> : <SeasonCard key={r.p.id} r={r} currentId={currentId} onPick={onPick} base={base} />))}
          </div>
        </div>
      ))}
    </section>
  )
}

function KindMark({ shape, color }) {
  const r = shape === 'square' ? 2 : shape === 'dot' || shape === 'ring' ? 6 : 1
  return <i className="kind-mark" aria-hidden="true" style={{ background: color, borderRadius: r, clipPath: shape === 'triangle' ? 'polygon(50% 0, 100% 100%, 0 100%)' : shape === 'diamond' ? 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)' : undefined }} />
}
