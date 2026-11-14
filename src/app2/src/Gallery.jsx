import { useMemo } from 'react'
import PlaceGallery from './parts/PlaceGallery.jsx'
import { Icon } from './ui.jsx'
import { capWord, categoryOf, classList, isEvent, joinList, numWord } from './data.js'

// One entry per place, from summary.json when it is there (instant), from the loaded manifest otherwise.
export function entriesFor(ids, places, summary) {
  const sum = new Map((summary?.places || []).map((s) => [s.id, s]))
  return ids.map((id) => {
    const s = sum.get(id), m = places[id]
    if (!s && !m) return null
    const event = m ? isEvent(m) : s.kind === 'event'
    return { id, s, m, event, category: s?.category || (m ? categoryOf(m) : 'other') }
  }).filter(Boolean)
}

// The company: every place as a card, in sections by class; picking one flies there.
export default function Gallery({ places, ids, summary, current, onPick, onClose }) {
  const entries = useMemo(() => entriesFor(ids, places, summary), [ids, places, summary])
  const classes = useMemo(() => classList(entries, summary), [entries, summary])
  const total = entries.length
  const evs = classes.filter((c) => entries.some((e) => e.category === c.id && e.event)).map((c) => c.label.toLowerCase())
  const fire = classes.filter((c) => entries.some((e) => e.category === c.id && !e.event && c.id === 'fire')).map((c) => c.label.toLowerCase())
  const wet = classes.filter((c) => entries.some((e) => e.category === c.id && !e.event) && c.id !== 'fire').map((c) => c.label.toLowerCase())
  const bits = [
    wet.length ? `${joinList(wet)}: a season of radar passes at each place` : '',
    fire.length ? `${joinList(fire)}: a season of passes where the radar echo changed` : '',
    evs.length ? `${joinList(evs)}: each a single event, seen in before and after radar pairs` : '',
  ].filter(Boolean)
  const sub = bits.length ? `${bits.join('; ')}.` : 'Each place is a real radar frame.'
  return (
    <div className="gallery" role="region" aria-label="All places">
      <div className="gallery-in">
        <div className="row gal-head">
          <div>
            <p className="eyebrow">The company</p>
            <h2>{total <= 25 ? capWord(total) : total} places{classes.length > 1 ? `, ${numWord(classes.length)} kinds of change` : ''}</h2>
            <p className="gal-sub">{sub} Pick a place to fly there.</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close places">{Icon.close}</button>
        </div>
        {total < ids.length && <p className="mono dim" role="status">Loading places… {total} of {ids.length}</p>}
        <PlaceGallery classes={classes} entries={entries} currentId={current} onPick={onPick} />
      </div>
    </div>
  )
}
