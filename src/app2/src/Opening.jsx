import { fmtDate, fmtKm } from './data.js'

export default function Opening({ place, index, onWatch, onFeel, onChoose }) {
  const P = place?.primary
  const v = P?.values[index]
  const d = place?.dates[index]
  const first = P?.values[0], last = P?.values[P.values.length - 1]
  return (
    <div className="opening">
      <div className="opening-copy">
        <p className="eyebrow">NASA Space Apps 2026 · NISAR radar · seasons of water and earthquakes</p>
        <h1>Dancing<br /><em>with the SARs</em></h1>
        <p className="lede">
          {place && P.kind === 'water'
            ? <>Radar sees the Earth’s surface change through cloud and night. In ten passes, the open water of {place.name.split(',')[0]} grew from {fmtKm(first)} to {fmtKm(last)} km². Here is the season, performed. Other places show something sudden: the ground itself moving in an earthquake.</>
            : <>Radar sees the Earth’s surface change through cloud and night: floods across a season, ground moving in an earthquake. Watch how it changed.</>}
        </p>
        <div className="actions">
          <button className="btn primary big" onClick={onWatch}>Watch the flood <span aria-hidden>→</span></button>
          <button className="btn ghost big" onClick={onChoose}>Choose a place</button>
          {onFeel && <button className="btn ghost big" onClick={onFeel}>Feel the ground move <span aria-hidden>→</span></button>}
        </div>
      </div>
      {place && (
        <div className="opening-live" aria-hidden="true">
          <span className="mono">{fmtDate(d)}</span>
          <strong>{fmtKm(v)}<small> km²</small></strong>
          <span className="mono dim">open water · {place.name.split(',')[0]}</span>
        </div>
      )}
      <p className="opening-note">Preview images, about 180 m per pixel, not calibrated: patterns and timing are reliable, exact areas are not.</p>
    </div>
  )
}
