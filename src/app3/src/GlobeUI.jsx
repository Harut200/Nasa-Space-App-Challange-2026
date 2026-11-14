import { useMemo } from 'react'
import { SEASON_COLOR, eventType, headline, isEvent, typeInfo } from './data.js'
import { KindIcon, SearchPanel } from './Search.jsx'
import { Icon } from './ui.jsx'

// Everything on top of the full globe: search and filter chips, the dot legend, a hint, and the card for a tapped dot.
export default function GlobeUI({ list, q, setQ, kind, setKind, selected, onSelect, onOpen, onRandom, onNear, near, inputRef, onEscape, phone, panelOpen, setPanelOpen }) {
  const types = useMemo(() => [...new Set(list.filter(isEvent).map(eventType))].sort((a, b) => (a === 'earthquake' ? -1 : b === 'earthquake' ? 1 : a.localeCompare(b))), [list])
  const sel = list.find((p) => p.id === selected)
  return (
    <div className="globe-ui">
      <section className={`globe-panel panel ${phone && !panelOpen ? 'collapsed' : ''}`} aria-label="Find a place">
        {phone && (
          <button className="gp-toggle" onClick={() => setPanelOpen(!panelOpen)} aria-expanded={panelOpen}>{Icon.search}<span>{panelOpen ? 'Hide the list' : 'Search and list'}</span><span className="mono n">{list.length}</span></button>
        )}
        {(!phone || panelOpen) && (
          <>
            <SearchPanel places={list} q={q} setQ={setQ} kind={kind} setKind={setKind} onPick={(p) => onOpen(p.id)} inputRef={inputRef} onEscape={onEscape} compactList={phone} />
            <div className="gp-actions">
              <button className="chip" onClick={onRandom}>{Icon.dice}<span>Random</span></button>
              <button className="chip" onClick={onNear}>{Icon.locate}<span>Nearest to me</span></button>
            </div>
            {near && <p className="near" role="status">{near}</p>}
          </>
        )}
      </section>
      <div className="globe-legend glass-note" aria-label="Dot legend">
        <p className="mono lg-h">Dots</p>
        <span><i style={{ background: SEASON_COLOR.big }} />Season, over 1,000 km² open water at peak</span>
        <span><i style={{ background: SEASON_COLOR.some }} />Season, some open water</span>
        <span><i style={{ background: SEASON_COLOR.land }} />Season, no open water mapped</span>
        {types.map((t) => <span key={t}><KindIcon shape={typeInfo(t).shape} color={typeInfo(t).color} size={14} />{typeInfo(t).label}: before and after</span>)}
        <span className="faint">Bigger dot: more water, or a stronger event. Coral ring: where you are.</span>
      </div>
      {!phone && <p className="globe-hint mono">Drag to turn · scroll to zoom · click a dot to fly in · / search · Esc back</p>}
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
