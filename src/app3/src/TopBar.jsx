import { useState } from 'react'
import { SearchBar } from './Search.jsx'
import { KindIcon } from './Search.jsx'
import { Icon } from './ui.jsx'

export function Switcher({ place, index, count, onPrev, onNext, onRandom, onName }) {
  return (
    <div className="switcher" role="group" aria-label="Change place">
      <button className="icon-btn" onClick={onPrev} aria-label="Previous place" title="Previous place ( [ )">{Icon.prev}</button>
      <button className="sw-name" onClick={onName} title="Pick or search a place ( / )" aria-label={`${place.name}, place ${index + 1} of ${count}. Open the list of places.`}>
        <KindIcon place={place} size={15} />
        <span className="sw-text">{place.name}</span>
        <small className="mono">{index + 1}/{count}</small>
      </button>
      <button className="icon-btn" onClick={onNext} aria-label="Next place" title="Next place ( ] )">{Icon.next}</button>
      <button className="chip sw-rand" onClick={onRandom} title="A random place ( R )">{Icon.dice}<span>Random</span></button>
    </div>
  )
}

const BACK_LABEL = { home: 'Home', globe: 'Globe', places: 'Places' }

// One top bar for every view: home, Back, the place switcher, search, the globe and the caveats.
export default function TopBar({ view, phone, place, placeIndex, count, mode, from, places, q, setQ, kind, setKind, inputRef, onHome, onBack, onPick, onPrev, onNext, onRandom, onName, onMode, onPlaces, onGlobe, onInfo, onSheet, onAbout, onSearchSheet, infoOpen, globeOpen, backText: backOverride }) {
  const [menu, setMenu] = useState(false)
  const inPlace = view === 'place'
  const backText = backOverride || (inPlace ? BACK_LABEL[from] || 'Home' : 'Home')
  const showBack = view !== 'open'
  const brand = (
    <button className="brand" onClick={onHome} aria-label="Dancing with the SARs: home" title="Home">
      <span className="brand-mark" aria-hidden="true" />
      {!phone && <span className="brand-text">Dancing <em>with the SARs</em></span>}
    </button>
  )
  const back = showBack && (
    <button className="chip back" onClick={onBack} aria-label={`Back to ${backText.toLowerCase()}`} title="Back (Esc)">{Icon.prev}<span>{phone ? '' : backText}</span></button>
  )
  const seg = inPlace && (
    <div className="seg" role="tablist" aria-label="View">
      <button role="tab" aria-selected={mode === 'story'} className={mode === 'story' ? 'on' : ''} onClick={() => onMode('story')}>Story</button>
      <button role="tab" aria-selected={mode === 'explore'} className={mode === 'explore' ? 'on' : ''} onClick={() => onMode('explore')}>Explore</button>
    </div>
  )
  const search = view !== 'globe' && (phone
    ? <button className="icon-btn" onClick={onSearchSheet} aria-label="Search places" title="Search ( / )">{Icon.search}</button>
    : <SearchBar places={places} q={q} setQ={setQ} kind={kind} setKind={setKind} inputRef={inputRef} onPick={onPick} />)
  const labelled = !inPlace && !phone
  const btn = (icon, text, on, title, extra = {}) => (
    labelled
      ? <button className={`chip ${extra.active ? 'on' : ''}`} onClick={on} title={title}>{icon}<span>{text}</span></button>
      : <button className={`icon-btn ${extra.active ? 'on' : ''}`} onClick={on} aria-label={text} title={title} aria-pressed={extra.active}>{icon}</button>
  )
  return (
    <>
      <header className={`topbar ${inPlace ? 'in-place' : ''}`}>
        {brand}
        {back}
        {inPlace && !phone && place && <Switcher place={place} index={placeIndex} count={count} onPrev={onPrev} onNext={onNext} onRandom={onRandom} onName={onName} />}
        {inPlace && !phone && seg}
        {inPlace && phone && seg}
        <div className="spacer" />
        {search}
        {view !== 'places' && btn(Icon.grid, 'Places', onPlaces, 'All places ( P )')}
        {view !== 'globe' && !globeOpen && btn(Icon.globe, 'Globe', onGlobe, 'The globe ( G )')}
        {inPlace && !phone && place && btn(Icon.list, 'Place info', onInfo, 'About this place ( I )', { active: infoOpen })}
        {!phone && (inPlace ? <button className="chip sure" onClick={onSheet} title="How sure are we? ( ? )">{Icon.info}<span>How sure are we?</span></button> : btn(Icon.info, 'How sure are we?', onSheet, 'How sure are we? ( ? )'))}
        {!inPlace && !phone && btn(Icon.about, 'About', onAbout, 'About and credits')}
        {phone && (
          <button className="icon-btn" onClick={() => setMenu((x) => !x)} aria-label="More" aria-expanded={menu} title="More">{Icon.more}</button>
        )}
      </header>
      {phone && menu && (
        <div className="menu-pop panel-like" role="menu" onClick={() => setMenu(false)}>
          {view !== 'places' && <button role="menuitem" onClick={onPlaces}>{Icon.grid}<span>Places</span></button>}
          {inPlace && place && <button role="menuitem" onClick={onInfo}>{Icon.list}<span>About this place</span></button>}
          <button role="menuitem" onClick={onSheet}>{Icon.info}<span>How sure are we?</span></button>
          <button role="menuitem" onClick={onAbout}>{Icon.about}<span>About and credits</span></button>
        </div>
      )}
      {inPlace && phone && place && (
        <div className="switcher-row"><Switcher place={place} index={placeIndex} count={count} onPrev={onPrev} onNext={onNext} onRandom={onRandom} onName={onName} /></div>
      )}
    </>
  )
}
