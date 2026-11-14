import { fmtDate, fmtKm } from './data.js'

export default function Opening({ place, index, onWatch, onChoose }) {
  const P = place?.primary
  const v = P?.values[index]
  const d = place?.dates[index]
  const first = P?.values[0], last = P?.values[P.values.length - 1]
  return (
    <div className="opening">
      <div className="opening-copy">
        <p className="eyebrow">NASA Space Apps 2026 · NISAR radar · June to October</p>
        <h1>Dancing<br /><em>with the SARs</em></h1>
        <p className="lede">
          {place && P.kind === 'water'
            ? <>In ten radar passes, the open water of {place.name.split(',')[0]} grew from {fmtKm(first)} to {fmtKm(last)} km². The satellite saw it through cloud and night. Here is the season, performed.</>
            : <>Ten passes of a radar satellite, one season. Watch how the Earth’s surface changed.</>}
        </p>
        <div className="actions">
          <button className="btn primary big" onClick={onWatch}>Watch the flood <span aria-hidden>→</span></button>
          <button className="btn ghost big" onClick={onChoose}>Choose a place</button>
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
