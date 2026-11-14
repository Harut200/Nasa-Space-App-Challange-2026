import { useState } from 'react'
import { fmtKm } from './data.js'

const num = (v) => (typeof v === 'number' ? fmtKm(v) : v)

// Legend for the layer on show, from manifest.layers[].legend. Collapsible; a chip on a phone.
const short = (t) => String(t).split(/[:,(]/)[0].trim()

// Legend for the layer on show, from manifest.layers[].legend. One quiet line of dots and words; opens to the full text.
// With `water`, clicking the blue or the green entry shows only that class; clicking it again shows both.
export default function Legend({ layer, phone, strip, only, water, open0 }) {
  const [open, setOpen] = useState(!!open0 && !!layer && layer.legend?.type !== 'classes')
  if (!layer) return null
  const L = layer.legend || {}
  const grad = L.colors ? `linear-gradient(90deg, ${L.colors.map((c) => (c === '#00000000' ? '#2a2f3a' : c)).join(',')})` : null
  const iso = (c) => (water && c === water.blue ? 'blue' : water && c === water.green ? 'green' : null)
  const pick = (c) => { const k = iso(c); if (k) water.setShow(water.show === k ? 'both' : k) }
  const Item = ({ it, full }) => {
    const k = iso(it.color), off = only && only !== it.color
    const inner = <><i style={{ background: it.color }} />{full ? it.label : short(it.label)}{off ? <em> (hidden)</em> : null}</>
    return k
      ? <li className={`${off ? 'off' : ''} pick`}><button type="button" onClick={() => pick(it.color)} aria-pressed={water.show === k} title={`${it.label}. ${water.show === k ? 'Click to show both again' : 'Click to show only this'}`}>{inner}</button></li>
      : <li className={off ? 'off' : ''} title={it.label}>{inner}</li>
  }
  const showAll = only && water ? <button type="button" className="legend-all" onClick={() => water.setShow('both')}>Show all</button> : null
  if (!open && L.type === 'classes' && !phone) {
    return (
      <div className={`legend line ${strip ? 'strip' : ''}`} role="group" aria-label={`Legend: ${layer.label}`}>
        <ul>{L.items.map((it) => <Item key={it.label} it={it} />)}</ul>
        {showAll}
        <button className="legend-more" onClick={() => setOpen(true)} aria-expanded="false" aria-label="Show the full legend" title="Show the full legend">+</button>
      </div>
    )
  }
  return (
    <div className={`legend ${open ? 'open' : 'shut'} ${strip ? 'strip' : ''}`} role="group" aria-label={`Legend: ${layer.label}`}>
      <button className="legend-title" onClick={() => setOpen((o) => !o)} aria-expanded={open} title={open ? 'Hide the legend' : 'Show the legend'}>
        {!open && grad && <i className="legend-sw" style={{ background: grad }} />}
        <span>{open ? layer.label : 'Legend'}</span>
        <span className="legend-chev" aria-hidden="true">{open ? '–' : '+'}</span>
      </button>
      {open && L.type === 'classes' && (
        <ul className={L.items.length > 6 ? 'cols' : ''}>
          {L.items.map((it) => <Item key={it.label} it={it} full />)}
        </ul>
      )}
      {open && showAll}
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
