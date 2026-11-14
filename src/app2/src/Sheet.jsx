import { useEffect, useRef } from 'react'
import { fmtDate, fmtDateLong, fmtKm } from './data.js'
import { Icon } from './ui.jsx'

// "How sure are we?": always reachable, never in the way.
export default function Sheet({ open, onClose, place, pass }) {
  const ref = useRef(null)
  useEffect(() => { if (open) ref.current?.focus() }, [open])
  if (!open) return null
  const P = place?.primary
  const qs = place ? Object.entries(place.quality) : []
  const i = place && pass ? place.dates.indexOf(pass) : (place?.dates.length ?? 1) - 1
  return (
    <div className="sheet-back" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label="How sure are we?" onClick={(e) => e.stopPropagation()} ref={ref} tabIndex={-1}>
        <div className="row"><h2>How sure are we?</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close}</button></div>
        <section>
          <h3>These are preview images</h3>
          <p>Every map and number comes from NISAR’s free preview images: about 180 m per pixel, and not calibrated. The values are display brightness, not backscatter in decibels. Patterns and timing are reliable; exact areas are not. Calibrated 20 m data is the next step.</p>
        </section>
        {place && P && P.kind !== 'hh' && (
          <section>
            <h3>{place.name}: the range</h3>
            <p>{P.label} on {fmtDateLong(place.dates[i])}: <b>{fmtKm(P.values[i])} km²</b>{P.lo ? <>, plausible range <b>{fmtKm(P.lo[i])}–{fmtKm(P.hi[i])} km²</b></> : null}. {P.lo ? 'The band on the timeline shows this range for every pass; treat the line as the best estimate, not the truth.' : 'This area has no range estimate; treat it as approximate.'}</p>
          </section>
        )}
        {place && (
          <section>
            <h3>Pass by pass</h3>
            {qs.length === 0 ? <p>No pass of this place was flagged by the automatic checks.</p> : (
              <ul className="qlist">
                {qs.map(([d, q]) => (
                  <li key={d} className={`q-${q.quality}`}><b>{fmtDate(d)} · {q.quality === 'fail' ? 'flagged, left out of change detection' : 'caution'}{q.channels?.length ? ` (${q.channels.join(', ')})` : ''}</b><span>{q.quality_note}</span></li>
                ))}
              </ul>
            )}
            {place.gaps.length > 0 && <p className="fine">No radar passes exist from {fmtDate(place.gaps[0][0])} to {fmtDate(place.gaps[0][1])} (instrument outage); the timeline shows it as a rest.</p>}
          </section>
        )}
        <section>
          <h3>Credits</h3>
          <p>{place?.credits || 'NISAR GCOV preview images: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC.'}</p>
          <p className="fine">Earth imagery: NASA GIBS, Blue Marble Shaded Relief with Bathymetry (when online; the radar layers work without it). Story figures are sourced in docs/research/story-sources.md in the project repository.</p>
        </section>
        <section>
          <h3>Use of AI</h3>
          <p>AI tools (Claude) were used for research, planning documents, code, analysis and first drafts of the story text. The team chose the challenge approach and the places, reviewed the results and edited the text.</p>
        </section>
      </aside>
    </div>
  )
}
