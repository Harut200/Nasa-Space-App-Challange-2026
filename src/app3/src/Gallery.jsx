import PlaceGallery from './parts/PlaceGallery.jsx'
import { Icon } from './ui.jsx'
import { capWord, isEvent } from './data.js'

// The company: every place as a card; picking one flies there.
export default function Gallery({ places, ids, current, onPick, onClose, filter, total }) {
  const rows = ids.filter((id) => places[id]).map((id) => ({ id, manifest: places[id], timeseries: places[id].ts }))
  const loaded = rows.map((r) => r.manifest)
  const kinds = new Set(loaded.map((p) => (isEvent(p) ? 'e' : 's'))).size
  return (
    <div className="gallery" role="region" aria-label="All places">
      <div className="gallery-in">
        <div className="row gal-head">
          <div>
            <p className="eyebrow">The company</p>
            <h2>{(total ?? ids.length) <= 25 ? capWord(total ?? ids.length) : (total ?? ids.length)} places{kinds === 2 ? ', two kinds of change' : ''}</h2>
            <p className="gal-sub">{kinds === 2 ? 'Seasons of water, each a real radar frame with its open-water curve, and earthquakes, each a before and after pair showing how far the ground moved.' : 'Each place is a real radar frame.'} Pick a place to fly there.</p>
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
