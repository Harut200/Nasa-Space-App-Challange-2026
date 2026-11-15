import { useRef } from 'react'
import { Chips, SearchInput, useSearchNav } from './Search.jsx'

// Search field and kind chips on top of the picture gallery: the cards below are filtered live.
export default function GalleryFilter({ places, shown, q, setQ, kind, setKind, onPick }) {
  const input = useRef(null)
  const nav = useSearchNav(shown, (p) => onPick(p), [q, kind], () => { if (q) setQ(''); else input.current?.blur() })
  const move = (e) => { if (e.key === 'ArrowDown') { const c = document.querySelector('.gallery .pt-card'); if (c) { e.preventDefault(); c.focus() } } else nav.onKeyDown(e) }
  return (
    <div className="gal-filter">
      <SearchInput q={q} setQ={setQ} inputRef={input} nav={{ ...nav, onKeyDown: move }} placeholder="Search places, countries, kinds" />
      <Chips places={places} kind={kind} setKind={setKind} />
      {shown.length === 0 && places.length > 0 && <p className="gal-empty" role="status">No place matches {q ? <>“{q}”</> : 'this filter'}. Try a country, “lake”, “earthquake” or “calibrated”. <button className="linkish" onClick={() => { setQ(''); setKind('all') }}>Clear</button></p>}
    </div>
  )
}
