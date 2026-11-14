import { useState } from 'react'
import { fmtDate, fmtDateLong, fmtKm } from './data.js'
import TimelineChart from './TimelineChart.jsx'
import Legend from './Legend.jsx'
import { Icon, Num } from './ui.jsx'

const MODES = [
  { id: 'water', label: 'Water', hint: 'Open water and flooded vegetation, pass by pass' },
  { id: 'when', label: 'When it changed', hint: 'The pass on which each place changed for good' },
  { id: 'change', label: 'Change', hint: 'Brightness change since the first pass' },
  { id: 'hh', label: 'Radar', hint: 'The radar image itself (HH)' },
]

function SpotCard({ place, spotId, index, onClose }) {
  const s = place.spots.find((x) => x.id === spotId)
  const ser = place.ts.series.find((x) => x.spot === spotId)
  if (!s || !ser) return null
  const W = 280, H = 120, m = { l: 28, r: 10, t: 10, b: 18 }
  const dates = place.dates
  const t = (d) => Date.parse(d)
  const x = (d) => m.l + ((t(d) - t(dates[0])) / (t(dates.at(-1)) - t(dates[0]))) * (W - m.l - m.r)
  const y = (v) => m.t + (1 - v / 100) * (H - m.t - m.b)
  const line = (arr) => arr.map((v, i) => (v == null ? null : `${x(dates[i])},${y(v)}`)).filter(Boolean)
  const hh = line(ser.hh), hv = line(ser.hv)
  return (
    <aside className="panel spot-card" aria-label={`Spot ${s.label}`}>
      <div className="row"><h3>{s.label}</h3><button className="icon-btn" onClick={onClose} aria-label="Close spot">{Icon.close}</button></div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`HH and HV brightness at ${s.label}`}>
        {[0, 50, 100].map((v) => <g key={v}><line x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" /><text x={m.l - 5} y={y(v) + 3} textAnchor="end" className="ax">{v}</text></g>)}
        <polyline points={hh.join(' ')} fill="none" stroke="var(--hh)" strokeWidth="2.2" strokeLinejoin="round" />
        <polyline points={hv.join(' ')} fill="none" stroke="var(--hv)" strokeWidth="2.2" strokeLinejoin="round" strokeDasharray="5 3" />
        {dates.map((d, i) => ser.hh[i] != null && <circle key={d} cx={x(d)} cy={y(ser.hh[i])} r={i === index ? 4.5 : 2.5} fill={i === index ? 'var(--accent)' : 'var(--hh)'} />)}
        <line x1={x(dates[index])} x2={x(dates[index])} y1={m.t} y2={H - m.b} stroke="var(--accent)" strokeWidth="1" opacity=".7" />
        <text x={m.l} y={H - 3} className="ax">{fmtDate(dates[0])}</text><text x={W - m.r} y={H - 3} textAnchor="end" className="ax">{fmtDate(dates.at(-1))}</text>
      </svg>
      <div className="key"><span><i style={{ background: 'var(--hh)' }} />HH {ser.hh[index] != null ? ser.hh[index] : '—'}</span><span><i className="dash" style={{ borderColor: 'var(--hv)' }} />HV {ser.hv[index] != null ? ser.hv[index] : '—'}</span></div>
      <p className="fine">Relative brightness on {fmtDate(dates[index])}, 0 to 100. Uncalibrated preview values.</p>
    </aside>
  )
}

export default function Explore({ place, layerId, layer, setLayer, index, setIndex, playing, setPlaying, opacity, setOpacity, cmp, setCmp, cmpAvailable, spotsOn, setSpotsOn, activeSpot, setActiveSpot, missing, dated, phone }) {
  const [more, setMore] = useState(false)
  const P = place.primary
  const v = P.values[index]
  const n = place.dates.length
  const avail = (id) => place.layers.some((l) => l.id === id)
  const isMain = MODES.some((m) => m.id === layerId)
  const rest = place.layers.filter((l) => !MODES.some((m) => m.id === l.id))
  const q = place.quality[place.dates[index]]

  return (
    <>
      <aside className="panel layers" aria-label="Map layers">
        <h2 className="panel-h">Show</h2>
        <div className="modes" role="radiogroup" aria-label="Layer">
          {MODES.filter((m) => avail(m.id)).map((m) => (
            <button key={m.id} role="radio" aria-checked={layerId === m.id} className={layerId === m.id ? 'on' : ''} onClick={() => { setLayer(m.id); setMore(false) }} title={m.hint}>{m.label}</button>
          ))}
          {rest.length > 0 && <button className={!isMain ? 'on' : ''} aria-expanded={more} onClick={() => setMore((x) => !x)}>More{!isMain ? ': ' + (layer?.id) : ''} <span aria-hidden>▾</span></button>}
        </div>
        {more && (
          <div className="more" role="listbox" aria-label="More layers">
            {rest.map((l) => <button key={l.id} role="option" aria-selected={layerId === l.id} className={layerId === l.id ? 'on' : ''} onClick={() => { setLayer(l.id); setMore(false) }}>{l.label}</button>)}
          </div>
        )}
        <label className="slider"><span>Overlay</span>
          <input type="range" min="0" max="100" value={Math.round(opacity * 100)} onChange={(e) => setOpacity(+e.target.value / 100)} aria-label="Overlay opacity" />
          <output>{Math.round(opacity * 100)}%</output></label>
        <div className="toggles">
          <button className={`chip ${cmp.on && cmpAvailable ? 'on' : ''}`} disabled={!cmpAvailable} aria-pressed={cmp.on && cmpAvailable} onClick={() => setCmp((c) => ({ ...c, on: !c.on }))} title={cmpAvailable ? 'Drag the handle: first pass | current pass (C)' : 'Compare needs a layer with several passes'}>{Icon.split}<span>Compare</span></button>
          <button className={`chip ${spotsOn ? 'on' : ''}`} aria-pressed={spotsOn} onClick={() => { setSpotsOn((x) => !x); setActiveSpot(null) }}>{Icon.pin}<span>Spots</span></button>
        </div>
      </aside>

      {activeSpot && <SpotCard place={place} spotId={activeSpot} index={index} onClose={() => setActiveSpot(null)} />}
      {!phone && <Legend layer={layer} />}
      {missing && <div className="pill note" role="status">{layerId === 'change' ? 'The first pass is the reference, so it has no change image.' : 'This layer has no image for this pass.'}</div>}
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
            <strong>{v != null ? <><Num value={v} /> <small>{P.unit === 'km²' ? 'km²' : ''}</small></> : '—'}</strong>
            <span className="mono dim">{P.label}{P.lo && v != null ? ` · range ${fmtKm(P.lo[index])}–${fmtKm(P.hi[index])}` : ''}</span>
          </div>
          {q && <div className={`pill q-${q.quality}`} title={q.quality_note} role="status">{q.quality === 'fail' ? 'Flagged pass' : 'Caution'}: {q.quality_note.split(';')[0]}</div>}
        </div>
        <div className="chart-wrap"><TimelineChart place={place} index={index} onScrub={setIndex} /></div>
      </section>
    </>
  )
}
