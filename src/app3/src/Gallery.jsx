import PlaceGallery from './parts/PlaceGallery.jsx'
import { Icon } from './ui.jsx'
import { capWord, classesIn, numWord, isEvent } from './data.js'

// The company: every place as a card; picking one flies there.
export default function Gallery({ places, ids, current, onPick, onClose, filter, total }) {
  const rows = ids.filter((id) => places[id]).map((id) => ({ id, manifest: places[id], timeseries: places[id].ts }))
  const loaded = rows.map((r) => r.manifest)
  const cls = classesIn(loaded), kinds = cls.length
  const evs = cls.filter((c) => loaded.some((p) => isEvent(p) && classesIn([p])[0].id === c.id)).map((c) => c.label.toLowerCase())
  const list = (a) => (a.length > 1 ? `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}` : a[0])
  const seasonKinds = cls.filter((c) => !evs.includes(c.label.toLowerCase())).map((c) => c.label.toLowerCase())
  return (
    <div className="gallery" role="region" aria-label="All places">
      <div className="gallery-in">
        <div className="row gal-head">
          <div>
            <p className="eyebrow">The company</p>
            <h2>{(total ?? ids.length) <= 25 ? capWord(total ?? ids.length) : (total ?? ids.length)} places{kinds > 1 ? `, ${numWord(kinds)} kinds of change` : ''}</h2>
            <p className="gal-sub">{seasonKinds.length ? `${list(seasonKinds)[0].toUpperCase()}${list(seasonKinds).slice(1)}, each a real radar frame with its open-water curve` : ''}{seasonKinds.length && evs.length ? ', and ' : ''}{evs.length ? `${list(evs)}, each a before and after pair showing how far the ground moved or changed` : ''}. Pick a place to fly there.</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close places">{Icon.close}</button>
        </div>
        {filter}
        {rows.length < ids.length && <p className="mono dim" role="status">Loading places… {rows.length} of {ids.length}</p>}
        <PlaceGallery places={rows} currentId={current} onPick={onPick} />
      </div>
    </div>
  )
}
