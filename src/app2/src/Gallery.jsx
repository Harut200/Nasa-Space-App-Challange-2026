import PlaceGallery from './parts/PlaceGallery.jsx'
import { Icon } from './ui.jsx'

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty', 'twenty-one', 'twenty-two', 'twenty-three', 'twenty-four', 'twenty-five']

// The company: every place as a card; picking one flies there.
export default function Gallery({ places, ids, current, onPick, onClose }) {
  const rows = ids.filter((id) => places[id]).map((id) => ({ id, manifest: places[id], timeseries: places[id].ts }))
  return (
    <div className="gallery" role="region" aria-label="All places">
      <div className="gallery-in">
        <div className="row gal-head">
          <div>
            <p className="eyebrow">The company</p>
            <h2>{WORDS[ids.length] ? WORDS[ids.length][0].toUpperCase() + WORDS[ids.length].slice(1) : ids.length} places, two kinds of change</h2>
            <p className="gal-sub">Seasons of water, each a real radar frame with its open-water curve, and earthquakes, each a before and after pair showing how far the ground moved. Pick a place to fly there.</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close places">{Icon.close}</button>
        </div>
        {rows.length < ids.length && <p className="mono dim" role="status">Loading places… {rows.length} of {ids.length}</p>}
        <PlaceGallery places={rows} currentId={current} onPick={onPick} />
      </div>
    </div>
  )
}
