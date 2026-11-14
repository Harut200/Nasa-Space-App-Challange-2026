import { useEffect, useRef } from 'react'
import { THEMES, applyTheme, REPO, BRAND } from './meta.js'

export default function About({ open, onClose, manifest, theme, onTheme, hasEvents }) {
  const ref = useRef(null)
  useEffect(() => {
    const d = ref.current
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  const pick = (t) => { onTheme(t); applyTheme(t) }
  return (
    <dialog ref={ref} className="about" onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
      <img className="about-lockup" src={`${BRAND}lockup.svg`} width="134" height="40" alt="Constellation" />
      <p className="muted about-team">Team Constellation · NASA Space Apps 2026</p>
      <h2>About</h2>
      <p>This app shows how the Earth's surface changed between June and October 2026, seen by the NISAR radar satellite. Pick a place, press play, and watch water, forest and fields change from one pass to the next. Radar sees through cloud and at night, so no pass is lost to weather.</p>
      <p>Between 27 July and 10 August the instrument was off, so there are no passes then. The timeline shows that gap.</p>
      {manifest?.source === 'preview' && <p><strong>Preview images.</strong> The pictures are uncalibrated previews at about 180 m per pixel. Patterns and timing are reliable; absolute brightness values are not.</p>}
      {hasEvents && <p>Some places are one-off events instead of a season: one radar pass before and one after. For an earthquake the map shows how far the ground moved along the radar's line of sight; for a volcano, a landslide or a fire, where the surface changed so much that the radar no longer recognises it.</p>}
      <p>Data caveat: these are PROVISIONAL NISAR products. Calibration is not final.</p>
      <h3>Credits</h3>
      <p>Radar data: NASA/JPL NISAR mission, distributed by the Alaska Satellite Facility (ASF). Earth imagery: NASA GIBS, Blue Marble (NASA Earth Observatory). Close-up imagery: Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020), released under CC BY-NC-SA 4.0 (non-commercial use, which suits this challenge entry; commercial use needs a licence from EOX). Dark-look basemap: OpenFreeMap, OpenMapTiles, OpenStreetMap contributors; coastlines: Natural Earth (public domain). <a href={REPO}>Source code</a> (MIT licence).</p>
      {hasEvents && <p>Event locations, magnitudes and mechanisms: USGS Earthquake Hazards Program. Ground-motion maps: NISAR L2 GUNW interferograms (NASA/JPL-Caltech and ISRO, via ASF DAAC), provisional.</p>}
      {manifest?.credits && <p className="muted">{manifest.name}: {manifest.credits}</p>}
      <h3>Use of AI</h3>
      <p>AI tools (Claude) were used for research, planning documents, code, analysis and first drafts of the story text. The team chose the challenge approach and the places, reviewed the results and edited the text.</p>
      <h3>Look of the app</h3>
      <div className="seg" role="group" aria-label="Theme">
        {THEMES.map(([k, name]) => <button key={k} aria-pressed={theme === k} onClick={() => pick(k)}>{name}</button>)}
      </div>
      <form method="dialog"><button className="btn solid">Close</button></form>
    </dialog>
  )
}
