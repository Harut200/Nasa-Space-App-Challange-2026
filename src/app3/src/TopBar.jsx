import { useState } from 'react'
import { SearchBar, KindIcon } from './Search.jsx'
import { Icon } from './ui.jsx'

export function Switcher({ place, index, count, prevName, nextName, onPrev, onNext, onRandom, onName, classLabel, onClearClass }) {
  return (
    <div className="switcher" role="group" aria-label="Change place">
      <button className="icon-btn quiet" onClick={onPrev} aria-label={`Previous place${prevName ? `: ${prevName}` : ''}`} title={prevName ? `Previous: ${prevName}  ( [ )` : 'Previous place ( [ )'}>{Icon.prev}</button>
      <button className="sw-name" onClick={onName} title={`${place.name} · place ${index + 1} of ${count} · open the list ( / )`} aria-label={`${place.name}, place ${index + 1} of ${count}. Open the list of places.`}>
        <KindIcon place={place} size={14} />
        <span className="sw-text">{place.name}</span>
      </button>
      <button className="icon-btn quiet" onClick={onNext} aria-label={`Next place${nextName ? `: ${nextName}` : ''}`} title={nextName ? `Next: ${nextName}  ( ] )` : 'Next place ( ] )'}>{Icon.next}</button>
      <button className="icon-btn quiet sw-rand" onClick={onRandom} aria-label="Random place" title="A random place ( R )">{Icon.dice}</button>
      {classLabel && <span className="class-tag mono" title="Arrows and Random stay in this class">{classLabel}<button onClick={onClearClass} aria-label={`Clear the ${classLabel} filter`}>×</button></span>}
    </div>
  )
}

const BACK_LABEL = { home: 'Home', globe: 'Globe', places: 'Places' }

// One quiet top bar for every view.
export default function TopBar({ view, phone, place, placeIndex, count, mode, from, places, q, setQ, kind, setKind, inputRef, onHome, onBack, onPick, onPrev, onNext, onRandom, onName, onMode, onPlaces, onGlobe, onInfo, onSheet, onSearchSheet, infoOpen, globeOpen, backText: backOverride, prevName, nextName, classLabel, onClearClass }) {
  const [menu, setMenu] = useState(false)
  const inPlace = view === 'place'
  const backText = backOverride || (inPlace ? BACK_LABEL[from] || 'Home' : 'Home')
  const brand = (
    <button className="brand" onClick={onHome} aria-label="Dancing with the SARs: home" title="Home">
      <img className="brand-logo" src={`${import.meta.env.BASE_URL}brand/mark.svg`} width="28" height="28" alt="Constellation" />
      {!phone && !inPlace && <span className="brand-text">Dancing <em>with the SARs</em></span>}
    </button>
  )
  const back = view !== 'open' && (
    <button className="chip back quiet" onClick={onBack} aria-label={`Back to ${backText.toLowerCase()}`} title="Back ( Esc )">{Icon.prev}<span>{phone ? '' : backText}</span></button>
  )
  const seg = inPlace && (
    <div className="seg" role="tablist" aria-label="View">
      <button role="tab" aria-selected={mode === 'story'} className={mode === 'story' ? 'on' : ''} onClick={() => onMode('story')}>Story</button>
      <button role="tab" aria-selected={mode === 'explore'} className={mode === 'explore' ? 'on' : ''} onClick={() => onMode('explore')}>Explore</button>
    </div>
  )
  const search = view !== 'globe' && (phone
    ? <button className="icon-btn quiet" onClick={onSearchSheet} aria-label="Search places" title="Search ( / )">{Icon.search}</button>
    : <SearchBar places={places} q={q} setQ={setQ} kind={kind} setKind={setKind} inputRef={inputRef} onPick={onPick} />)
  const ib = (icon, text, on, title, active) => (
    <button className={`icon-btn quiet ${active ? 'on' : ''}`} onClick={on} aria-label={text} title={title}>{icon}</button>
  )
  return (
    <>
      <header className={`topbar ${inPlace ? 'in-place' : ''}`}>
        {brand}
        {back}
        {inPlace && !phone && place && <Switcher place={place} index={placeIndex} count={count} prevName={prevName} nextName={nextName} onPrev={onPrev} onNext={onNext} onRandom={onRandom} onName={onName} classLabel={classLabel} onClearClass={onClearClass} />}
        {inPlace && seg}
        <div className="spacer" />
        {search}
        {!phone && view !== 'places' && ib(Icon.grid, 'Places', onPlaces, 'All places ( P )')}
        {!phone && !inPlace && view !== 'globe' && !globeOpen && ib(Icon.globe, 'Globe', onGlobe, 'The globe ( G )')}
        {!phone && (inPlace && place ? ib(Icon.info, 'About this place', onInfo, 'About this place ( I )', infoOpen) : ib(Icon.info, 'How sure are we?', onSheet, 'How sure are we? ( ? )'))}
        {phone && <button className="icon-btn quiet" onClick={() => setMenu((x) => !x)} aria-label="More" aria-expanded={menu} title="More">{Icon.more}</button>}
      </header>
      {phone && menu && (
        <div className="menu-pop panel-like" role="menu" onClick={() => setMenu(false)}>
          {view !== 'places' && <button role="menuitem" onClick={onPlaces}>{Icon.grid}<span>Places</span></button>}
          {view !== 'globe' && !globeOpen && <button role="menuitem" onClick={onGlobe}>{Icon.globe}<span>Globe</span></button>}
          {inPlace && place && <button role="menuitem" onClick={onInfo}>{Icon.list}<span>About this place</span></button>}
          <button role="menuitem" onClick={onSheet}>{Icon.info}<span>How sure are we?</span></button>
        </div>
      )}
      {inPlace && phone && place && (
        <div className="switcher-row"><Switcher place={place} index={placeIndex} count={count} prevName={prevName} nextName={nextName} onPrev={onPrev} onNext={onNext} onRandom={onRandom} onName={onName} classLabel={classLabel} onClearClass={onClearClass} /></div>
      )}
    </>
  )
}
