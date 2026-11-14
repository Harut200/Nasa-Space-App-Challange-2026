import { useState } from 'react'
import { fmtKm } from './data.js'

const num = (v) => (typeof v === 'number' ? fmtKm(v) : v)

// Legend for the layer on show, from manifest.layers[].legend. Collapsible; a chip on a phone.
export default function Legend({ layer, phone, strip, only, water }) {
  const [open, setOpen] = useState(!phone)
  if (!layer) return null
  const L = layer.legend || {}
  const grad = L.colors ? `linear-gradient(90deg, ${L.colors.map((c) => (c === '#00000000' ? '#2a2f3a' : c)).join(',')})` : null
  const title = layer.label
  return (
    <div className={`legend ${open ? 'open' : 'shut'} ${strip ? 'strip' : ''}`} role="group" aria-label={`Legend: ${layer.label}`}>
      <button className="legend-title" onClick={() => setOpen((o) => !o)} aria-expanded={open} title={open ? 'Hide the legend' : 'Show the legend'}>
        {!open && grad && <i className="legend-sw" style={{ background: grad }} />}
        <span>{open ? title : 'Legend'}</span>
        <span className="legend-chev" aria-hidden="true">{open ? '–' : '+'}</span>
      </button>
      {open && water && (
        <div className="water-seg" role="group" aria-label="Which water to show">
          {[['both', 'Both'], ['blue', 'Open water'], ['green', 'Under vegetation']].map(([k, t]) => (
            <button key={k} type="button" className={`${k} ${water.show === k ? 'on' : ''}`} aria-pressed={water.show === k} onClick={() => water.setShow(k)} title={k === 'blue' ? 'Show only open water (blue)' : k === 'green' ? 'Show only possible water under vegetation (green)' : 'Show both'}>{k !== 'both' && <i />}{t}</button>
          ))}
        </div>
      )}
      {open && L.type === 'classes' && (
        <ul className={L.items.length > 6 ? 'cols' : ''}>
          {L.items.map((it) => <li key={it.label} className={only && only !== it.color ? 'off' : ''}><i style={{ background: it.color }} />{it.label}{only && only !== it.color ? <em> (hidden)</em> : null}</li>)}
        </ul>
      )}
      {open && (L.type === 'ramp' || L.type === 'diverging') && (
        <>
          <div className="ramp" style={{ background: grad }} />
          <div className="ramp-labels">
            {L.type === 'diverging' && L.labels ? <><span>{L.labels[0]}</span><span>{L.labels[1]}</span></> : <><span>{num(L.min)}</span><span>{L.unit}</span><span>{num(L.max)}</span></>}
          </div>
        </>
      )}
      {open && L.type === 'text' && <p>{L.text}</p>}
    </div>
  )
}
