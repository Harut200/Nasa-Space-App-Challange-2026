import { useMemo } from 'react'
import Opening from './Opening.jsx'
import { classesIn } from './data.js'

const one = (l) => (/ /.test(l) ? l : /oes$/.test(l) ? l.slice(0, -2) : l.replace(/s$/, ''))

// B's opening as it was, with one primary and two quiet actions and a single quiet line that opens place selection.
export default function Home({ place, index, list, onWatch, onFeel, onChoose }) {
  const summary = useMemo(() => classesIn(list), [list])
  const total = summary.reduce((s, c) => s + c.count, 0)
  return (
    <>
      <Opening place={place} index={index} onWatch={onWatch} onFeel={onFeel} onChoose={onChoose} bare />
      <p className="credit"><img src={`${import.meta.env.BASE_URL}brand/lockup.svg`} height="22" width="104" alt="Constellation" /><span className="mono">Team Constellation · NASA Space Apps 2026</span></p>
      {total > 0 && (
        <p className="landing-line">
          <button onClick={onChoose} aria-label="Choose a place">
            <b>{total} places</b>{summary.map((c) => <span key={c.id}> · {c.count} {c.count === 1 ? one(c.label.toLowerCase()) : c.label.toLowerCase()}</span>)} <span aria-hidden>→</span>
          </button>
        </p>
      )}
    </>
  )
}
