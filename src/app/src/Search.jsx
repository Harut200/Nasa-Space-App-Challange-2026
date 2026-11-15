import { useEffect, useMemo, useRef, useState } from 'react'
import { classInfo, classOf, classesIn, headline, kindLabel, matches, placeColor } from './data.js'
import { Icon } from './ui.jsx'

// A little icon in the colour and shape of the place's dot on the globe.
export function KindIcon({ place, shape: sh, color, size = 16 }) {
  const t = place ? classInfo(classOf(place)) : { shape: sh }
  const c = color || (place ? placeColor(place) : '#fff')
  const s = size
  let shape
  if (t.shape === 'ring') shape = <><circle cx="12" cy="12" r="5.2" fill={c} stroke="#fff" strokeWidth="1.6" /><circle cx="12" cy="12" r="9.6" fill="none" stroke={c} strokeWidth="1.8" /></>
  else if (t.shape === 'triangle') shape = <path d="M12 3.5 21 20H3z" fill={c} stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
  else if (t.shape === 'flame') shape = <path d="M12 2.5c.6 3.4 5.8 6 5.8 11.2a5.8 5.8 0 0 1-11.6 0c0-2.2 1-3.6 2.2-4.6.2 1.4.9 2.2 1.8 2.6C9.4 8.2 10.6 5.4 12 2.5z" fill={c} stroke="#fff" strokeWidth="1.5" strokeLinejoin="round" />
  else if (t.shape === 'square') shape = <rect x="4.5" y="4.5" width="15" height="15" rx="2.5" fill={c} stroke="#fff" strokeWidth="1.6" />
  else if (t.shape === 'diamond') shape = <path d="M12 2.5 21.5 12 12 21.5 2.5 12z" fill={c} stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
  else shape = <circle cx="12" cy="12" r="7" fill={c} stroke="#fff" strokeWidth="1.6" />
  return <svg className="kind-icon" width={s} height={s} viewBox="0 0 24 24" aria-hidden="true">{shape}</svg>
}

// All, then one chip per class present in the data (labels, order and counts from the data).
export function chipsFor(places) {
  return [{ key: 'all', label: 'All', n: places.length }, ...classesIn(places).map((c) => ({ key: c.id, label: c.label, n: c.count, shape: c.shape, color: c.color, desc: c.desc }))]
}
export const filterPlaces = (places, q, kind) => {
  const r = places.filter((p) => matches(p, q, kind))
  if (!String(q || '').trim() || kind !== 'all') return r
  const order = classesIn(places).map((c) => c.id) // searching groups the results by class, in display order
  return [...r].sort((a, b) => order.indexOf(classOf(a)) - order.indexOf(classOf(b)))
}

export function Chips({ places, kind, setKind }) {
  const chips = useMemo(() => chipsFor(places), [places])
  return (
    <div className="chips" role="group" aria-label="Filter places by kind">
      {chips.map((c) => (
        <button key={c.key} type="button" className={`fchip ${kind === c.key ? 'on' : ''}`} aria-pressed={kind === c.key} onClick={() => setKind(c.key)}>
          {c.shape && <KindIcon shape={c.shape} color={c.color} size={12} />}{c.label} <span className="mono n">{c.n}</span>
        </button>
      ))}
      {chips.find((c) => c.key === kind)?.desc && <p className="class-desc" role="status">{chips.find((c) => c.key === kind).desc}</p>}
    </div>
  )
}

// Arrow keys move through the results, Enter opens the highlighted one. Returns props for the input.
export function useSearchNav(results, onPick, deps, onEscape) {
  const [active, setActive] = useState(0)
  useEffect(() => { setActive(0) }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(results.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter') { const p = results[Math.min(active, results.length - 1)]; if (p) { e.preventDefault(); onPick(p) } }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onEscape?.(e) }
  }
  return { active, setActive, onKeyDown }
}

export function ResultList({ results, active, setActive, onPick, q, onClear, id = 'results', grouped }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [active, results])
  if (!results.length) {
    return (
      <div className="results empty" role="status">
        <p>No place matches {q ? <>“{q}”</> : 'this filter'}.</p>
        <p className="dim">Try a country or region, “lake”, “earthquake” or “calibrated”.</p>
        {onClear && <button className="linkish" onClick={onClear}>Clear the search</button>}
      </div>
    )
  }
  return (
    <ul className="results" role="listbox" aria-label="Places" id={id} ref={ref}>
      {results.map((p, i) => {
        const h = headline(p)
        const head = grouped && (i === 0 || classOf(results[i - 1]) !== classOf(p))
        return [
          head && <li key={'h' + classOf(p)} role="presentation" className="r-group mono">{classInfo(classOf(p)).label}</li>,
          <li key={p.id} role="option" aria-selected={i === active} className={i === active ? 'on' : ''} data-id={p.id}
            onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(p)}>
            <KindIcon place={p} />
            <span className="r-name"><b title={p.name}>{p.name}</b><small className="mono" title={kindLabel(p)}>{kindLabel(p)}</small></span>
            <span className="r-num mono">{h.short}<small>{h.tag}</small></span>
          </li>,
        ]
      })}
    </ul>
  )
}

export function SearchInput({ q, setQ, inputRef, nav, placeholder = 'Search places', onFocus, onBlur, expanded, controls, autoFocus }) {
  return (
    <label className="search-field">
      <span className="s-ic" aria-hidden="true">{Icon.search}</span>
      <input ref={inputRef} type="search" value={q} placeholder={placeholder} autoComplete="off" spellCheck="false" autoFocus={autoFocus}
        aria-label="Search places by name, country, kind or event type" role="combobox" aria-expanded={!!expanded} aria-controls={controls} aria-autocomplete="list"
        onChange={(e) => setQ(e.target.value)} onKeyDown={nav.onKeyDown} onFocus={onFocus} onBlur={onBlur} />
      {q && <button type="button" className="s-clear" aria-label="Clear the search" onMouseDown={(e) => e.preventDefault()} onClick={() => { setQ(''); inputRef?.current?.focus() }}>{Icon.close}</button>}
    </label>
  )
}

// The search field of the top bar: a popover with chips and results under it.
export function SearchBar({ places, q, setQ, kind, setKind, onPick, inputRef, onEscape }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  const results = useMemo(() => filterPlaces(places, q, kind), [places, q, kind])
  const nav = useSearchNav(results, (p) => { setOpen(false); inputRef.current?.blur(); onPick(p) }, [q, kind], () => { if (q) setQ(''); else { setOpen(false); inputRef.current?.blur(); onEscape?.() } })
  return (
    <div className={`search-bar ${open ? 'open' : ''}`} ref={box}
      onBlur={(e) => { if (!box.current?.contains(e.relatedTarget)) setOpen(false) }}>
      <SearchInput q={q} setQ={setQ} inputRef={inputRef} nav={nav} onFocus={() => setOpen(true)} expanded={open} controls="bar-results" />
      {open && (
        <div className="search-pop panel-like" role="dialog" aria-label="Search results">
          <Chips places={places} kind={kind} setKind={setKind} />
          <ResultList grouped={!!q.trim()} results={results} {...nav} onPick={(p) => { setOpen(false); inputRef.current?.blur(); onPick(p) }} q={q} onClear={() => { setQ(''); setKind('all') }} id="bar-results" />
        </div>
      )}
    </div>
  )
}

// The docked search of the globe (and the sheet on a phone).
export function SearchPanel({ places, q, setQ, kind, setKind, onPick, inputRef, onEscape, autoFocus, compactList, listOpen = true }) {
  const results = useMemo(() => filterPlaces(places, q, kind), [places, q, kind])
  const nav = useSearchNav(results, onPick, [q, kind], () => { if (q) setQ(''); else onEscape?.() })
  return (
    <div className="search-panel">
      <SearchInput q={q} setQ={setQ} inputRef={inputRef} nav={nav} expanded controls="panel-results" autoFocus={autoFocus} />
      {listOpen && <Chips places={places} kind={kind} setKind={setKind} />}
      {listOpen && <div className={`panel-list ${compactList ? 'compact' : ''}`}>
        <p className="count mono" role="status">{results.length === places.length ? `${places.length} places` : `${results.length} of ${places.length} places`}</p>
        <ResultList grouped={!!q.trim()} results={results} {...nav} onPick={onPick} q={q} onClear={() => { setQ(''); setKind('all') }} id="panel-results" />
      </div>}
    </div>
  )
}
