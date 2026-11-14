import { classList, dataNotice, fmtDate, fmtKm, joinList, numWord } from './data.js'

// Said only of what the data shows: grew, shrank or held steady.
const verb = (a, z) => (a == null || z == null ? null : Math.abs(z - a) < Math.max(0.5, a * 0.005) ? 'held steady' : z > a ? `grew from ${fmtKm(a)} to ${fmtKm(z)} km²` : `shrank from ${fmtKm(a)} to ${fmtKm(z)} km²`)

export default function Opening({ place, summary, total, index, onWatch, onFeel, onChoose }) {
  const P = place?.primary
  const v = P?.values[index]
  const d = place?.dates[index]
  const first = P?.values[0], last = P?.values[P.values.length - 1]
  const what = place && P.kind === 'water' ? verb(first, last) : null
  const nm = place?.name.split(',')[0]
  // counts and kinds are those of the collection (summary.json), never typed in
  const classes = summary?.places ? classList(summary.places.map((p) => ({ id: p.id, category: p.category })), summary) : []
  const n = summary?.places?.length || total || 0
  const kinds = classes.map((c) => c.label.toLowerCase())
  return (
    <div className="opening">
      <div className="opening-copy">
        <p className="eyebrow">NASA Space Apps 2026 · NISAR radar{n ? ` · ${n} places, ${classes.length > 1 ? `${numWord(classes.length)} kinds of change` : 'one kind of change'}` : ''}</p>
        <h1>Dancing<br /><em>with the SARs</em></h1>
        <p className="lede">
          {what
            ? <>Radar sees the Earth’s surface change through cloud and night. In {numWord(place.dates.length)} passes, the open water of {nm} {what}. Here is the season, performed.</>
            : <>Radar sees the Earth’s surface change through cloud and night{kinds.length ? `: ${joinList(kinds)}` : ''}. Watch how it changed.</>}
        </p>
        <div className="actions">
          <button className="btn primary big" onClick={onWatch}>Watch the flood <span aria-hidden>→</span></button>
          <button className="btn ghost big" onClick={onChoose}>Choose a place</button>
          {onFeel && <button className="btn ghost big" onClick={onFeel}>Feel the ground move <span aria-hidden>→</span></button>}
        </div>
      </div>
      {place && P.kind === 'water' && (
        <div className="opening-live" aria-hidden="true">
          <span className="mono live-date">{fmtDate(d)}</span>
          <strong>{fmtKm(v)}<small> km²</small></strong>
          <span className="mono dim">open water · {nm}</span>
        </div>
      )}
      {place && <p className="opening-note">{dataNotice(place)}</p>}
    </div>
  )
}
