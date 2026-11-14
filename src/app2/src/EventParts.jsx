import { useState } from 'react'
import { cycleCm, fmtDate, goodViews, isNoisy, isQuake } from './data.js'
import Legend from './Legend.jsx'
import { Icon } from './ui.jsx'

const day = (s) => String(s).slice(0, 10)
const ms = (s) => (String(s).length === 10 ? Date.parse(`${s}T00:00:00Z`) : Date.parse(s))

// "Before -> quake -> after": for each pair, the two pass dates with the earthquake marked between them.
export function EventStrip({ event }) {
  const noun = (event.type ? event.type === 'earthquake' : event.magnitude != null) ? 'earthquake' : 'event'
  const views = (event.views || []).filter((v) => v.before && v.after)
  if (!views.length) return null
  const qt = ms(day(event.time)) // the day, as the passes are dated by day
  const all = [qt, ...views.flatMap((v) => [ms(v.before), ms(v.after)])].filter(Number.isFinite)
  const a = Math.min(...all), b = Math.max(...all), span = b - a || 1
  const pos = (t) => 12 + ((t - a) / span) * 76
  const looks = new Set(views.map((v) => v.label)).size
  return (
    <div className="ev-strip" role="group" aria-label={`Before, ${noun}, after: the pass dates with the ${noun} marked`}>
      {views.map((v) => {
        const q = pos(qt), b0 = pos(ms(v.before)), b1 = pos(ms(v.after))
        return (
          <div className="ev-row" key={v.id}>
            <div className="ev-name mono">{looks > 1 || views.length > 1 ? `${v.label}, track ${v.track}` : `${v.label} · track ${v.track}`}</div>
            <div className="ev-track">
              <i className="ev-line" style={{ left: `${b0}%`, width: `${b1 - b0}%` }} />
              <i className="ev-dot" style={{ left: `${b0}%` }} />
              <i className="ev-dot" style={{ left: `${b1}%` }} />
              <span className="ev-star" style={{ left: `${q}%` }} title={noun === 'event' ? 'Event' : 'Earthquake'} aria-hidden="true">★</span>
              <span className="ev-q mono" style={{ left: `${q}%` }}>{noun === 'event' ? 'event' : 'quake'} · {fmtDate(day(event.time))}</span>
              <span className="ev-d mono" style={{ left: `${b0}%` }}><b>before</b>{fmtDate(day(v.before))}</span>
              <span className="ev-d mono" style={{ left: `${b1}%` }}><b>after</b>{fmtDate(day(v.after))}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

const SHORT = { coh_loss: 'Changed area', fringes: 'Fringes', los_cm: 'Displacement', los_asc_cm: 'Displacement · ascending', los_desc_cm: 'Displacement · descending', coherence: 'Coherence' }
const ORDER = ['coh_loss', 'fringes', 'los_cm', 'los_asc_cm', 'los_desc_cm', 'coherence']
const HINT = {
  coh_loss: 'Where the radar picture changed completely between the passes',
  los_cm: 'Ground motion along the radar line of sight, in cm', los_asc_cm: 'Line-of-sight motion, ascending track', los_desc_cm: 'Line-of-sight motion, descending track',
  coherence: 'How comparable the two passes are',
}

export function EventExplore({ place, layerId, layer, setLayer, opacity, setOpacity, cmp, setCmp, spotsOn, setSpotsOn, setActiveSpot, phone }) {
  const [more, setMore] = useState(false)
  const modes = ORDER.filter((id) => place.layers.some((l) => l.id === id))
  const rest = place.layers.filter((l) => !ORDER.includes(l.id))
  const isMain = modes.includes(layerId)
  const quake = isQuake(place), e = place.event, cyc = cycleCm(place)
  const good = goodViews(e), noisy = (e.views || []).filter(isNoisy)
  return (
    <>
      <aside className="panel layers" aria-label="Map layers">
        <h2 className="panel-h">Show</h2>
        <div className="modes ev-modes" role="radiogroup" aria-label="Layer">
          {modes.map((id) => (
            <button key={id} role="radio" aria-checked={layerId === id} className={layerId === id ? 'on' : ''} onClick={() => { setLayer(id); setMore(false) }} title={id === 'fringes' ? (cyc ? `One colour cycle is ${cyc} cm of line-of-sight change` : 'Interferometric fringes') : HINT[id]}>{SHORT[id]}</button>
          ))}
          {rest.length > 0 && <button className={!isMain ? 'on' : ''} aria-expanded={more} onClick={() => setMore((x) => !x)}>More <span aria-hidden>▾</span></button>}
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
          <button className={`chip ${cmp.on ? 'on' : ''}`} aria-pressed={cmp.on} onClick={() => setCmp((c) => ({ ...c, on: !c.on }))} title="Drag the handle: before | after where the data has a before pair, else bare terrain | this layer (C)">{Icon.split}<span>Compare</span></button>
          <button className={`chip ${spotsOn ? 'on' : ''}`} aria-pressed={spotsOn} onClick={() => { setSpotsOn((x) => !x); setActiveSpot(null) }}>{Icon.pin}<span>Spots</span></button>
        </div>
      </aside>
      <Legend key={layer?.id} layer={layer} phone={phone} />
      <section className="dock panel ev-dock" aria-label="Before and after">
        <div className="ev-head">
          {quake ? <div className="ev-fig"><strong>{e.magnitude != null ? `M ${Number(e.magnitude).toFixed(1)}` : 'Earthquake'}</strong><span className="mono">{place.name.split(',')[0]}, {fmtDate(day(e.time))}</span></div>
            : <div className="ev-fig"><strong className="ev-title">{e.summary || e.title || place.name}</strong><span className="mono">{e.title || place.name}{String(e.title || '').includes(String(day(e.time)).slice(0, 4)) ? '' : `, ${fmtDate(day(e.time))}`}{e.time_note ? ` · ${e.time_note}` : ''}</span></div>}
          {quake && good[0] && <div className="ev-fig"><strong>≈ {good[0].p2p_cm}<small> cm</small></strong><span className="mono">ground motion along the line of sight{(e.views || []).length > 1 ? `, ${good[0].label}, track ${good[0].track}` : ''}</span></div>}
        </div>
        <EventStrip event={e} />
        {quake && noisy.length > 0 && <p className="ev-note">{noisy.map((v) => `${v.label}, track ${v.track}`).join('; ')}: too noisy to use. Its control pair shows nearly as much motion as the earthquake pair.</p>}
        {quake && cyc && <p className="ev-note">Each full colour cycle is {cyc} cm of ground motion toward or away from the satellite.</p>}
        {!quake && <p className="ev-note">Provisional data: this shows where the surface changed, not what it is made of or how thick it is.</p>}
      </section>
    </>
  )
}
