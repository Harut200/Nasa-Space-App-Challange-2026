import { useMemo, useState } from 'react'
import { classesIn, headline } from './data.js'
import { KindIcon, SearchPanel } from './Search.jsx'
import { Icon } from './ui.jsx'

// On top of the full globe: a search field (chips and the list appear when it is focused or used), a compact legend of the classes present, and the card for a tapped dot.
export default function GlobeUI({ list, q, setQ, kind, setKind, selected, onSelect, onOpen, onRandom, onNear, near, inputRef, onEscape, phone }) {
  const classes = useMemo(() => classesIn(list), [list])
  const sel = list.find((p) => p.id === selected)
  const [focus, setFocus] = useState(false)
  const expanded = focus || !!q || kind !== 'all'
  return (
    <div className="globe-ui">
      <section className={`globe-panel ${expanded ? 'expanded' : ''}`} aria-label="Find a place"
        onFocusCapture={() => setFocus(true)} onBlurCapture={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setFocus(false) }}>
        <SearchPanel places={list} q={q} setQ={setQ} kind={kind} setKind={setKind} onPick={(p) => onOpen(p.id)} inputRef={inputRef} onEscape={onEscape} compactList={phone} listOpen={expanded} />
        {expanded && (
          <div className="gp-actions">
            <button className="chip quiet" onClick={onRandom}>{Icon.dice}<span>Random</span></button>
            <button className="chip quiet" onClick={onNear}>{Icon.locate}<span>Nearest to me</span></button>
          </div>
        )}
        {near && <p className="near" role="status">{near}</p>}
      </section>
      <div className="globe-legend" aria-label="Dot legend">
        {classes.map((c) => <span key={c.id}><KindIcon shape={c.shape} color={c.color} size={13} />{c.label}</span>)}
      </div>
      {sel && (
        <div className="globe-sel panel" role="dialog" aria-label={sel.name}>
          <strong>{sel.name}</strong>
          <span>{headline(sel).long}</span>
          <div className="row"><button className="btn primary" onClick={() => onOpen(sel.id)}>Open this place <span aria-hidden>→</span></button><button className="icon-btn" onClick={() => onSelect(null)} aria-label="Dismiss">{Icon.close}</button></div>
        </div>
      )}
    </div>
  )
}
