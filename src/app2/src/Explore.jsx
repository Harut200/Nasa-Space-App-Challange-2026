import { useState } from 'react'
import { tidy, dataNotice, fmtDate, fmtDateLong, fmtKm } from './data.js'
import TimelineChart from './TimelineChart.jsx'
import Legend from './Legend.jsx'
import { Icon, Num } from './ui.jsx'

const COH = [
  { id: 'coh', label: 'Coherence', hint: 'Coherence per 12-day pair: dark = changed, bright = unchanged' },
  { id: 'coh_change', label: 'Where the fields settled', hint: 'Summer to autumn: where the fields settled' },
]
const MODES = [
  { id: 'water', label: 'Water', hint: 'Open water and flooded vegetation, pass by pass' },
  { id: 'when', label: 'When it changed', hint: 'The pass on which each place changed for good' },
  { id: 'change', label: 'Change', hint: 'Brightness change since the first pass' },
  { id: 'hh', label: 'Radar', hint: 'The radar image itself (HH)' },
]
// Places with no open-water story (wildfires): the change and the radar lead, water goes under "More".
const BURN = [
  { id: 'change', label: 'Change', hint: 'Brightness change since the first pass' },
  { id: 'hv', label: 'Radar HV', hint: 'The cross-polarised radar image (HV)' },
  { id: 'hh', label: 'Radar HH', hint: 'The radar image itself (HH)' },
  { id: 'when', label: 'When it changed', hint: 'The pass on which each place changed for good' },
]

const niceStep = (span) => { const p = Math.pow(10, Math.floor(Math.log10(span / 3))); const f = span / 3 / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p }

function SpotCard({ place, spotId, index, onClose }) {
  const s = place.spots.find((x) => x.id === spotId)
  const ser = place.ts.series.find((x) => x.spot === spotId)
  if (!s || !ser) return null
  const db = place.ts.unit === 'dB'
  const W = 280, H = 120, m = { l: 34, r: 10, t: 10, b: 18 }
  const dates = place.dates
  const t = (d) => Date.parse(d)
  // axis from the values themselves: 0 to 100 for the preview brightness, decibels (negative) for calibrated data
  const all = [...ser.hh, ...ser.hv].filter((v) => v != null && Number.isFinite(v))
  let lo = 0, hi = 100
  if (db || all.some((v) => v < 0 || v > 100)) {
    const mn = Math.min(...all), mx = Math.max(...all), step = niceStep(Math.max(1, mx - mn))
    lo = Math.floor(mn / step) * step; hi = Math.ceil(mx / step) * step
    if (hi === lo) hi = lo + step
  }
  const ticks = [lo, (lo + hi) / 2, hi]
  const x = (d) => m.l + ((t(d) - t(dates[0])) / (t(dates.at(-1)) - t(dates[0]))) * (W - m.l - m.r)
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b)
  const line = (arr) => arr.map((v, i) => (v == null ? null : `${x(dates[i])},${y(v)}`)).filter(Boolean)
  const hh = line(ser.hh), hv = line(ser.hv)
  const val = (v) => (v == null ? '—' : db ? `${fmtKm(v)} dB` : v)
  return (
    <aside className="panel spot-card" aria-label={`Spot ${s.label}`}>
      <div className="row"><h3>{s.label}</h3><button className="icon-btn" onClick={onClose} aria-label="Close spot">{Icon.close}</button></div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`HH and HV ${db ? 'backscatter' : 'brightness'} at ${s.label}`}>
        {ticks.map((v) => <g key={v}><line x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" /><text x={m.l - 5} y={y(v) + 3} textAnchor="end" className="ax">{fmtKm(+v.toFixed(1))}</text></g>)}
        <polyline points={hh.join(' ')} fill="none" stroke="var(--hh)" strokeWidth="2.2" strokeLinejoin="round" />
        <polyline points={hv.join(' ')} fill="none" stroke="var(--hv)" strokeWidth="2.2" strokeLinejoin="round" strokeDasharray="5 3" />
        {dates.map((d, i) => ser.hh[i] != null && <circle key={d} cx={x(d)} cy={y(ser.hh[i])} r={i === index ? 4.5 : 2.5} fill={i === index ? 'var(--accent)' : 'var(--hh)'} />)}
        <line x1={x(dates[index])} x2={x(dates[index])} y1={m.t} y2={H - m.b} stroke="var(--accent)" strokeWidth="1" opacity=".7" />
        <text x={m.l} y={H - 3} className="ax">{fmtDate(dates[0])}</text><text x={W - m.r} y={H - 3} textAnchor="end" className="ax">{fmtDate(dates.at(-1))}</text>
      </svg>
      <div className="key"><span><i style={{ background: 'var(--hh)' }} />HH {val(ser.hh[index])}</span><span><i className="dash" style={{ borderColor: 'var(--hv)' }} />HV {val(ser.hv[index])}</span></div>
      <p className="fine">{db ? `HH and HV backscatter in decibels on ${fmtDate(dates[index])}. ${dataNotice(place)}` : `Relative brightness on ${fmtDate(dates[index])}, 0 to 100. Uncalibrated preview values.`}</p>
    </aside>
  )
}

export default function Explore({ place, layerId, layer, setLayer, index, setIndex, playing, setPlaying, opacity, setOpacity, cmp, setCmp, cmpAvailable, spotsOn, setSpotsOn, activeSpot, setActiveSpot, missing, dated, phone }) {
  const [more, setMore] = useState(false)
  const P = place.primary
  const v = P.values[index]
  const n = place.dates.length
  const avail = (id) => place.layers.some((l) => l.id === id)
  const none = P.kind === 'none'
  const lead = P.kind === 'pct' ? COH : []
  const base = [...lead, ...(none ? BURN : MODES)].filter((m) => avail(m.id))
  // the layer the manifest opens on, when it is none of the usual ones (burn_*, ev_*): first, with its manifest label
  const dl = place.defaultLayer && place.layers.find((l) => l.id === place.defaultLayer)
  const modes = dl && !base.some((m) => m.id === dl.id) ? [{ id: dl.id, label: dl.label.split(/ \(|:/)[0], hint: dl.label }, ...base] : base
  const isMain = modes.some((m) => m.id === layerId)
  const rest = place.layers.filter((l) => !modes.some((m) => m.id === l.id))
  const q = place.quality[place.dates[index]]
  const unit = P.unit === 'km²' || P.unit === '%' || P.unit === 'dB' ? P.unit : ''

  return (
    <>
      <aside className="panel layers" aria-label="Map layers">
        <h2 className="panel-h">Show</h2>
        <div className="modes" role="radiogroup" aria-label="Layer">
          {modes.map((m) => (
            <button key={m.id} role="radio" aria-checked={layerId === m.id} className={layerId === m.id ? 'on' : ''} onClick={() => { setLayer(m.id); setMore(false) }} title={m.hint}>{m.label}</button>
          ))}
          {rest.length > 0 && <button className={!isMain ? 'on' : ''} aria-expanded={more} onClick={() => setMore((x) => !x)}>More{!isMain && layer ? ': ' + layer.label.split(/ \(|:/)[0] : ''} <span aria-hidden>▾</span></button>}
        </div>
        {more && (
          <div className="more" role="listbox" aria-label="More layers">
            {rest.map((l) => <button key={l.id} role="option" aria-selected={layerId === l.id} className={layerId === l.id ? 'on' : ''} onClick={() => { setLayer(l.id); setMore(false) }}>{l.label}</button>)}
          </div>
        )}
        <label className="slider"><span>Overlay</span>
          <input type="range" min="0" max="100" value={Math.round(opacity * 100)} onChange={(e) => setOpacity(+e.target.value / 100)} onPointerUp={(e) => e.currentTarget.blur()} aria-label="Overlay opacity" />
          <output>{Math.round(opacity * 100)}%</output></label>
        <div className="toggles">
          <button className={`chip ${cmp.on && cmpAvailable ? 'on' : ''}`} disabled={!cmpAvailable} aria-pressed={cmp.on && cmpAvailable} onClick={() => setCmp((c) => ({ ...c, on: !c.on }))} title={cmpAvailable ? 'Drag the handle: first pass | current pass (C)' : 'Compare needs a layer with several passes'}>{Icon.split}<span>Compare</span></button>
          <button className={`chip ${spotsOn ? 'on' : ''}`} aria-pressed={spotsOn} onClick={() => { setSpotsOn((x) => !x); setActiveSpot(null) }}>{Icon.pin}<span>Spots</span></button>
        </div>
      </aside>

      {activeSpot && <SpotCard place={place} spotId={activeSpot} index={index} onClose={() => setActiveSpot(null)} />}
      <Legend key={layer?.id} layer={layer} phone={phone} />
      {missing && <div className="pill note" role="status">{layerId === 'change' ? 'The first pass is the reference, so it has no change image.' : layerId === 'coh' ? 'The first pass has no earlier pass to pair with, so it has no coherence image.' : 'This layer has no image for this pass.'}</div>}
      {layer && !dated && <div className="pill note" role="status">This layer covers the whole season, so the timeline does not change it.</div>}

      <section className="dock panel" aria-label="Timeline">
        <div className="dock-head">
          <div className="transport">
            <button className="icon-btn" onClick={() => setIndex(index - 1)} disabled={index === 0} aria-label="Previous pass">{Icon.prev}</button>
            <button className="play" onClick={() => { if (!playing && index >= n - 1) setIndex(0); setPlaying(!playing) }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? Icon.pause : Icon.play}</button>
            <button className="icon-btn" onClick={() => setIndex(index + 1)} disabled={index >= n - 1} aria-label="Next pass">{Icon.next}</button>
          </div>
          <div className="readout">
            <span className="mono date">{fmtDateLong(place.dates[index])} · pass {index + 1} of {n}</span>
            {none ? <strong className="note-text">{P.label}</strong> : <strong>{v != null ? <><Num value={v} /> <small>{unit}</small></> : '—'}</strong>}
            {place.caution && <span className="caution">{place.caution}</span>}
            <span className="mono dim">{none ? 'one beat per radar pass' : P.label}{P.lo && v != null ? ` · range ${fmtKm(P.lo[index])}–${fmtKm(P.hi[index])}` : ''}{v == null && P.kind === 'pct' ? ' · the first pass has no earlier pass to compare with' : ''}</span>
          </div>
          {q && <div className={`pill q-${q.quality}`} title={tidy(q.quality_note)} role="status">{q.quality === 'fail' ? 'Flagged pass' : 'Caution'}: {tidy(q.quality_note.split(';')[0])}</div>}
        </div>
        <div className="chart-wrap"><TimelineChart place={place} index={index} onScrub={setIndex} /></div>
      </section>
    </>
  )
}
