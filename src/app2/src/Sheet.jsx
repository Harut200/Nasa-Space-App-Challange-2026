import { useEffect, useRef } from 'react'
import { BASE, tidy, fmtDate, fmtDateLong, fmtKm, fmtVal, goodViews, isEvent, isNoisy, isQuake } from './data.js'
import { Icon } from './ui.jsx'

// "How sure are we?": always reachable, never in the way.
export default function Sheet({ open, onClose, place, pass }) {
  const ref = useRef(null)
  useEffect(() => { if (open) ref.current?.focus() }, [open])
  if (!open) return null
  if (isEvent(place)) return <EventSheet onClose={onClose} place={place} sheetRef={ref} />
  const P = place?.primary
  const qs = place ? Object.entries(place.quality) : []
  const i = place && pass ? place.dates.indexOf(pass) : (place?.dates.length ?? 1) - 1
  return (
    <div className="sheet-back" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label="How sure are we?" onClick={(e) => e.stopPropagation()} ref={ref} tabIndex={-1}>
        <div className="row"><h2>How sure are we?</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close}</button></div>
        <section>
          {place?.calibrated ? (
            <>
              <h3>Calibrated data, provisional calibration</h3>
              <p>Calibrated NISAR data, provisional calibration. Every map and number here comes from calibrated NISAR GCOV data at 80 m: gamma-naught backscatter, in decibels. Because the calibration is still provisional, treat the numbers as a careful measurement of this season, not a finished product.</p>
            </>
          ) : (
            <>
              <h3>These are preview images</h3>
              <p>Every map and number comes from NISAR’s free preview images: about 180 m per pixel, and not calibrated. The values are display brightness, not backscatter in decibels. Patterns and timing are reliable; exact areas are not. Calibrated 20 m data is the next step.</p>
            </>
          )}
        </section>
        {place && P && P.kind !== 'hh' && P.values[i] != null && (
          <section>
            <h3>{place.name}: the range</h3>
            <p>{P.label} on {fmtDateLong(place.dates[i])}: <b>{fmtVal(P, P.values[i])}</b>{P.lo ? <>, plausible range <b>{fmtKm(P.lo[i])}–{fmtKm(P.hi[i])} km²</b></> : null}. {P.lo ? 'The band on the timeline shows this range for every pass; treat the line as the best estimate, not the truth.' : 'This area has no range estimate; treat it as approximate.'}</p>
          </section>
        )}
        {place && (
          <section>
            <h3>Pass by pass</h3>
            {qs.length === 0 ? <p>No pass of this place was flagged by the automatic checks.</p> : (
              <ul className="qlist">
                {qs.map(([d, q]) => (
                  <li key={d} className={`q-${q.quality}`}><b>{fmtDate(d)} · {q.quality === 'fail' ? 'flagged, left out of change detection' : 'caution'}{q.channels?.length ? ` (${q.channels.join(', ')})` : ''}</b><span>{tidy(q.quality_note)}</span></li>
                ))}
              </ul>
            )}
            {place.gaps.length > 0 && <p className="fine">No radar passes exist from {fmtDate(place.gaps[0][0])} to {fmtDate(place.gaps[0][1])} (instrument outage); the timeline shows it as a rest.</p>}
          </section>
        )}
        <section>
          <h3>Credits</h3>
          <p>{place?.credits || 'NISAR GCOV preview images: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC.'}</p>
          <p className="fine">Earth imagery: NASA GIBS, Blue Marble Shaded Relief with Bathymetry (wide views), and Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020; CC BY-NC-SA 4.0) for close views, both when online; the radar layers work without them. Story figures are sourced in docs/research/story-sources.md in the project repository.</p>
        </section>
        <section>
          <h3>Team</h3>
          <p className="sheet-team"><img src={`${BASE}brand/lockup.svg`} height="26" alt="Constellation" /><span>Team Constellation · NASA Space Apps 2026</span></p>
        </section>
        <section>
          <h3>Use of AI</h3>
          <p>The team did the research, the data analysis and the planning, and designed the detection approach: which radar measurements to use, the formulas, the control tests and the experiments, and what counts as a result. Harutyun Kesablyan and Abel Babayan led detection, data analysis and science; Arameh Haghverdian led the design of the app's back end and front end. AI tools (Claude) were used as an assistant under the team's direction: to help with research, write code, run analyses and draft text. The team reviewed the code, the results and the text.</p>
        </section>
      </aside>
    </div>
  )
}

function EventSheet({ onClose, place, sheetRef }) {
  const e = place.event
  const views = e.views || []
  if (!isQuake(place)) return <ChangeSheet onClose={onClose} place={place} sheetRef={sheetRef} />
  const good = goodViews(e), noisy = views.filter(isNoisy)
  const ctl = views.filter((v) => v.control_p2p_cm != null && !isNoisy(v))
  const looks = new Set(views.map((v) => v.label)).size
  return (
    <div className="sheet-back" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label="How sure are we?" onClick={(ev) => ev.stopPropagation()} ref={sheetRef} tabIndex={-1}>
        <div className="row"><h2>How sure are we?</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close}</button></div>
        <section>
          <h3>Provisional data</h3>
          <p>These ground-motion maps come from NISAR L2 GUNW interferograms, a provisional product.</p>
        </section>
        <section>
          <h3>Line of sight only</h3>
          <p>A radar measures movement along its line of sight, toward or away from the satellite, not up, down or sideways. {looks > 1 ? `Here we have two viewing directions (${views.map((v) => `${v.label}, track ${v.track}`).join('; ')}).` : `Here we have one viewing direction (${views[0] ? `${views[0].label}, track ${views[0].track}` : 'one track'}).`} The sign convention (which colour is toward the satellite) follows the interferogram definition we assumed; the product guide does not confirm it. The results are not validated against GNSS (GPS) measurements.</p>
          {good.length > 0 && <p>The “peak-to-peak” numbers ({good.map((v) => `${v.label}, track ${v.track}: ≈ ${v.p2p_cm} cm`).join('; ')}) are the span from the most to the least line-of-sight change near the epicentre, not a single displacement of the fault.</p>}
          {noisy.length > 0 && <p>{noisy.map((v) => `${v.label}, track ${v.track}`).join('; ')}: too noisy to use. The control pair from before the earthquake shows nearly as much motion as the earthquake pair, so we give no number for it.</p>}
        </section>
        {ctl.length > 0 && (
          <section>
            <h3>The control</h3>
            <p>Measured the same way on a pair from before the earthquake: {ctl.map((v) => `${v.label}, track ${v.track}: ≈ ${v.control_p2p_cm} cm`).join('; ')}. This is how much apparent motion the method shows when the earthquake is not in the pair. No control image is shown.</p>
          </section>
        )}
        <section>
          <h3>What we do not claim</h3>
          <p>This is a measurement of ground movement, not of damage or casualties. We do not infer which fault plane moved or how much it slipped.</p>
        </section>
        <section>
          <h3>Credits</h3>
          <p>Earthquake locations, magnitudes and mechanisms: USGS Earthquake Hazards Program. Ground-motion maps: NISAR L2 GUNW interferograms (NASA/JPL-Caltech and ISRO, via ASF DAAC), provisional.</p>
          <p className="fine">Earth imagery: NASA GIBS, Blue Marble Shaded Relief with Bathymetry (wide views), and Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020; CC BY-NC-SA 4.0) for close views, both when online; the radar layers work without them.</p>
        </section>
        <section>
          <h3>Team</h3>
          <p className="sheet-team"><img src={`${BASE}brand/lockup.svg`} height="26" alt="Constellation" /><span>Team Constellation · NASA Space Apps 2026</span></p>
        </section>
        <section>
          <h3>Use of AI</h3>
          <p>The team did the research, the data analysis and the planning, and designed the detection approach: which radar measurements to use, the formulas, the control tests and the experiments, and what counts as a result. Harutyun Kesablyan and Abel Babayan led detection, data analysis and science; Arameh Haghverdian led the design of the app's back end and front end. AI tools (Claude) were used as an assistant under the team's direction: to help with research, write code, run analyses and draft text. The team reviewed the code, the results and the text.</p>
        </section>
      </aside>
    </div>
  )
}

// Volcanoes and landslides: where the surface changed completely, nothing about what it is made of.
function ChangeSheet({ onClose, place, sheetRef }) {
  const e = place.event
  const views = e.views || []
  const layers = place.layers.filter((l) => l.id === 'coh_loss' || l.id === 'coherence')
  return (
    <div className="sheet-back" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label="How sure are we?" onClick={(ev) => ev.stopPropagation()} ref={sheetRef} tabIndex={-1}>
        <div className="row"><h2>How sure are we?</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close}</button></div>
        <section>
          <h3>Provisional data</h3>
          <p>Provisional data: this shows where the surface changed, not what it is made of or how thick it is.</p>
        </section>
        <section>
          <h3>What the map shows</h3>
          <p>{e.summary ? `${e.title || place.name}: ${e.summary}. ` : ''}{views.length ? `The change is measured between two radar passes (${views.map((v) => `${v.label}, track ${v.track}: ${fmtDate(String(v.before).slice(0, 10))} and ${fmtDate(String(v.after).slice(0, 10))}`).join('; ')}).` : ''}</p>
          {layers.length > 0 && <ul className="qlist">{layers.map((l) => <li key={l.id}><b>{l.label}</b>{l.legend?.type === 'classes' && l.legend.items?.[0] ? <span>{l.legend.items[0].label}</span> : null}</li>)}</ul>}
          {e.time_note && <p className="fine">Date note: {e.time_note}.</p>}
        </section>
        <section>
          <h3>Credits</h3>
          <p>{place.credits || 'NISAR data: NASA/JPL-Caltech and ISRO, distributed by ASF DAAC.'}</p>
          {e.source_url && <p className="fine">Event source: {e.source_url}</p>}
          <p className="fine">Earth imagery: NASA GIBS, Blue Marble Shaded Relief with Bathymetry (wide views), and Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020; CC BY-NC-SA 4.0) for close views, both when online; the radar layers work without them.</p>
        </section>
        <section>
          <h3>Team</h3>
          <p className="sheet-team"><img src={`${BASE}brand/lockup.svg`} height="26" alt="Constellation" /><span>Team Constellation · NASA Space Apps 2026</span></p>
        </section>
        <section>
          <h3>Use of AI</h3>
          <p>The team did the research, the data analysis and the planning, and designed the detection approach: which radar measurements to use, the formulas, the control tests and the experiments, and what counts as a result. Harutyun Kesablyan and Abel Babayan led detection, data analysis and science; Arameh Haghverdian led the design of the app's back end and front end. AI tools (Claude) were used as an assistant under the team's direction: to help with research, write code, run analyses and draft text. The team reviewed the code, the results and the text.</p>
        </section>
      </aside>
    </div>
  )
}
