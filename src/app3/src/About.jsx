import { useEffect, useRef } from 'react'
import { Icon } from './ui.jsx'

export const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'

export default function About({ open, onClose }) {
  const ref = useRef(null)
  useEffect(() => { if (open) ref.current?.focus() }, [open])
  if (!open) return null
  return (
    <div className="sheet-back" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label="About" onClick={(e) => e.stopPropagation()} ref={ref} tabIndex={-1}>
        <div className="row"><h2>About</h2><button className="icon-btn" onClick={onClose} aria-label="Close">{Icon.close}</button></div>
        <section>
          <p className="about-lockup"><img src={`${import.meta.env.BASE_URL}brand/lockup.svg`} height="32" width="152" alt="Constellation" /></p>
          <p className="fine">Team Constellation · NASA Space Apps 2026</p>
          <p>Dancing with the SARs shows how the surface of the Earth changed in 2026, seen by the NISAR L-band radar. Radar sees through cloud and at night, so no pass is lost to weather.</p>
          <p>There are two kinds of places. <b>Seasons of water</b> have about ten dated passes: watch lakes and floodplains fill and empty, open water in blue and water under vegetation in green. <b>Events</b>, such as earthquakes, have one pass before and one after, and show how far the ground moved along the radar’s line of sight.</p>
        </section>
        <section>
          <h3>Keys</h3>
          <p className="fine">← → scenes or passes · [ ] or Shift + ← → previous and next place · R random place · G globe · / search · P all places · M story or explore · I place info · ? how sure are we · Esc closes the top panel.</p>
        </section>
        <section>
          <h3>Credits</h3>
          <p>Radar data: NASA/JPL NISAR mission, distributed by the Alaska Satellite Facility (ASF DAAC). Earthquake locations, magnitudes and mechanisms: USGS Earthquake Hazards Program. Ground-motion maps: NISAR L2 GUNW interferograms, provisional.</p>
          <p className="fine">Earth imagery: NASA GIBS, Blue Marble Shaded Relief with Bathymetry, and Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020; CC BY-NC-SA 4.0). <a href={REPO}>Source code</a>.</p>
        </section>
        <section>
          <h3>Use of AI</h3>
          <p>AI tools (Claude) were used for research, planning documents, code, analysis and first drafts of the story text. The team chose the challenge approach and the places, reviewed the results and edited the text.</p>
        </section>
      </aside>
    </div>
  )
}
