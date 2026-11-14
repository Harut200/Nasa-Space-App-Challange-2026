import { useState } from 'react'
import { fmtDate } from './data.js'
import Legend from './Legend.jsx'
import { Icon } from './ui.jsx'

const day = (s) => String(s).slice(0, 10)
const ms = (s) => (String(s).length === 10 ? Date.parse(`${s}T00:00:00Z`) : Date.parse(s))

// Compact "before -> after" strip: the two pass dates of each pair, with the earthquake marked between them.
export function EventStrip({ event }) {
  const views = (event.views || []).filter((v) => v.before && v.after)
  if (!views.length) return null
  const all = [ms(event.time), ...views.flatMap((v) => [ms(v.before), ms(v.after)])].filter(Number.isFinite)
  const a = Math.min(...all), b = Math.max(...all), span = b - a || 1
  const pos = (t) => 14 + ((t - a) / span) * 72
  return (
    <div className="ev-strip" role="group" aria-label="Before and after: pass dates with the earthquake marked">
      {views.map((v) => (
        <div className="ev-row" key={v.id}>
          <div className="ev-name mono">{views.length > 1 ? `${v.label}, track ${v.track}` : `${v.label} · track ${v.track}`}</div>
          <div className="ev-track">
            <i className="ev-line" style={{ left: `${pos(ms(v.before))}%`, width: `${pos(ms(v.after)) - pos(ms(v.before))}%` }} />
            <i className="ev-dot" style={{ left: `${pos(ms(v.before))}%` }} />
            <i className="ev-dot" style={{ left: `${pos(ms(v.after))}%` }} />
            <span className="ev-star" style={{ left: `${pos(ms(event.time))}%` }} title="Earthquake" aria-hidden="true">★</span>
            <span className="ev-q mono" style={{ left: `${pos(ms(event.time))}%` }}>quake {fmtDate(day(event.time))}</span>
            <span className="ev-d mono" style={{ left: `${pos(ms(v.before))}%` }}>before {fmtDate(day(v.before))}</span>
            <span className="ev-d mono" style={{ left: `${pos(ms(v.after))}%` }}>after {fmtDate(day(v.after))}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

const SHORT = { fringes: 'Fringes', los_cm: 'Displacement', los_asc_cm: 'Displacement · ascending', los_desc_cm: 'Displacement · descending', coherence: 'Coherence' }
const ORDER = ['fringes', 'los_cm', 'los_asc_cm', 'los_desc_cm', 'coherence']
const HINT = {
  fringes: 'One colour cycle is 10 cm of line-of-sight change',
  los_cm: 'Ground motion along the radar line of sight, in cm', los_asc_cm: 'Line-of-sight motion, ascending track', los_desc_cm: 'Line-of-sight motion, descending track',
  coherence: 'How comparable the two passes are',
}

export function EventExplore({ place, layerId, layer, setLayer, opacity, setOpacity, cmp, setCmp, spotsOn, setSpotsOn, setActiveSpot, phone }) {
  const [more, setMore] = useState(false)
  const modes = ORDER.filter((id) => place.layers.some((l) => l.id === id))
  const rest = place.layers.filter((l) => !ORDER.includes(l.id))
  const isMain = modes.includes(layerId)
  return (
    <>
      <aside className="panel layers" aria-label="Map layers">
        <h2 className="panel-h">Show</h2>
        <div className="modes ev-modes" role="radiogroup" aria-label="Layer">
          {modes.map((id) => (
            <button key={id} role="radio" aria-checked={layerId === id} className={layerId === id ? 'on' : ''} onClick={() => { setLayer(id); setMore(false) }} title={HINT[id]}>{SHORT[id]}</button>
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
          <button className={`chip ${cmp.on ? 'on' : ''}`} aria-pressed={cmp.on} onClick={() => setCmp((c) => ({ ...c, on: !c.on }))} title="Drag the handle: bare terrain | this layer (C)">{Icon.split}<span>Compare</span></button>
          <button className={`chip ${spotsOn ? 'on' : ''}`} aria-pressed={spotsOn} onClick={() => { setSpotsOn((x) => !x); setActiveSpot(null) }}>{Icon.pin}<span>Spots</span></button>
        </div>
      </aside>
      {!phone && <Legend layer={layer} />}
      <section className="dock panel ev-dock" aria-label="Before and after">
        <EventStrip event={place.event} />
        <p className="ev-note">Each full colour cycle is 10 cm of ground motion toward or away from the satellite.</p>
      </section>
    </>
  )
}
