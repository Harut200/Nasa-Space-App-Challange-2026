import { useMemo } from 'react'
import Opening from './Opening.jsx'
import { eventType, headline, isEvent, kindLabel, typeInfo } from './data.js'
import { KindIcon } from './Search.jsx'
import { Icon } from './ui.jsx'
import { thumbUrl } from './parts/PlaceGallery.jsx'

const FEATURED = ['tonle-sap', 'tonle-sap-cal', 'sevan', 'ararat']
const LABEL = { 'tonle-sap': 'The flood, 10 passes', 'tonle-sap-cal': 'Calibrated 80 m data', sevan: 'A lake through the season', ararat: 'Farming season, coherence' }
// "Clearest" earthquakes: the ones with a measured ground motion, biggest first.
const motion = (p) => Math.max(0, ...(p.event?.views || []).map((v) => (Number.isFinite(v.p2p_cm) ? v.p2p_cm : 0)))

// B's opening as it was, with two more buttons beside its three, a one-line legend in the counter card, and a slim row of featured places.
export default function Home({ place, index, places, ids, onWatch, onFeel, onChoose, onGlobe, onRandom, onPick }) {
  const featured = useMemo(() => {
    const seasons = FEATURED.filter((id) => places[id]).map((id) => ({ p: places[id], note: LABEL[id] }))
    const ev = ids.map((id) => places[id]).filter((p) => p && isEvent(p))
    const quakes = ev.filter((p) => eventType(p) === 'earthquake').sort((a, b) => motion(b) - motion(a)).slice(0, 2)
    const others = ev.filter((p) => eventType(p) !== 'earthquake').slice(0, 1)
    return [...seasons, ...[...quakes, ...others].map((p) => ({ p, note: `${typeInfo(eventType(p)).label}, before and after` }))]
  }, [places, ids])
  const water = place?.layers.find((l) => l.id === 'water')
  const items = water?.legend?.type === 'classes' ? water.legend.items.slice(0, 2) : []
  const legend = items.length ? <div className="opening-legend" aria-label="Legend">{items.map((it) => <span key={it.label}><i style={{ background: it.color }} />{it.label}</span>)}</div> : null
  const extra = (
    <>
      <button className="btn ghost big" onClick={onGlobe}>{Icon.globe}<span>Explore the globe</span></button>
      <button className="btn ghost big" onClick={onRandom}>{Icon.dice}<span>Random place</span></button>
    </>
  )
  return (
    <>
      <Opening place={place} index={index} onWatch={onWatch} onFeel={onFeel} onChoose={onChoose} extra={extra} legend={legend} />
      <section className="featured" aria-label="Featured places">
        <h2 className="mono feat-h">Start somewhere</h2>
        <ul>
          {featured.map(({ p, note }) => {
            const h = headline(p)
            return (
              <li key={p.id}>
                <button className="feat-card" onClick={() => onPick(p.id)} aria-label={`${p.name}: ${h.short} ${h.tag}`}>
                  <span className="feat-img"><img src={thumbUrl({ ...p, id: p.id }, import.meta.env.BASE_URL + 'data/')} alt="" width="50" height="50" loading="lazy" decoding="async" onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} /></span>
                  <span className="feat-body">
                    <span className="feat-name"><KindIcon place={p} size={13} /> {p.name}</span>
                    <span className="feat-num">{h.short}</span>
                    <span className="feat-note mono">{h.tag} · {note || kindLabel(p)}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      </section>
    </>
  )
}
