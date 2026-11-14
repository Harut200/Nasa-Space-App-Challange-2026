import { useEffect, useMemo, useRef, useState } from 'react'
import MapView from './MapView.jsx'
import { Chart, Strip } from './Charts.jsx'

const DATA = import.meta.env.BASE_URL + 'data/'
const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'
const COLORS = ['#e41a1c', '#377eb8', '#4daf4a', '#ff7f00', '#984ea3']
const STORY = [['see', 'What you see'], ['physics', 'What is happening'], ['lband', 'Why radar shows it'], ['people', 'Why it matters']]
const NONE = []
const HASH = new URLSearchParams(location.hash.slice(1)) // link state at page load

const getJson = (path) => fetch(DATA + path).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${path}: ${r.status}`))))
const words = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
const nearest = (dates, d) => dates.reduce((a, b) => (Math.abs(Date.parse(b) - Date.parse(d)) < Math.abs(Date.parse(a) - Date.parse(d)) ? b : a))

// The date a layer really shows for a wanted date, and the image for it.
function frame(area, layer, date) {
  if (!layer.dates) return { url: `${DATA}${area}/${layer.id}.webp`, shown: null }
  const shown = layer.dates.includes(date) ? date : nearest(layer.dates, date)
  return { url: `${DATA}${area}/${layer.id}/${shown}.webp`, shown }
}

function Legend({ legend }) {
  if (!legend?.type) return null
  if (legend.type === 'text') return <p>{legend.text}</p>
  return (
    <div>
      <span>{legend.min} {legend.unit} </span>
      <span className="legend-bar" style={{ display: 'inline-block', background: `linear-gradient(to right, ${legend.colors.join(',')})` }} />
      <span> {legend.max} {legend.unit}</span>
      {legend.labels && <div>{legend.labels.join(' ↔ ')}</div>}
    </div>
  )
}

export default function App() {
  const [manifests, setManifests] = useState(null)
  const [error, setError] = useState(null)
  const [areaId, setAreaId] = useState(HASH.get('area'))
  const [dateIndex, setDateIndex] = useState(0)
  const [layerId, setLayerId] = useState(HASH.get('layer'))
  const [playing, setPlaying] = useState(false)
  const [compare, setCompare] = useState(false)
  const [otherIndex, setOtherIndex] = useState(0)
  const [split, setSplit] = useState(50)
  const [series, setSeries] = useState({})
  const [maps, setMaps] = useState({})
  const about = useRef(null)

  useEffect(() => {
    getJson('index.json')
      .then((idx) => Promise.all(idx.areas.map((a) => getJson(`${a}/manifest.json`))))
      .then((list) => {
        setManifests(list)
        const m = list.find((x) => x.id === HASH.get('area')) || list[0]
        if (m) { setAreaId(m.id); setDateIndex(Math.max(0, m.dates.indexOf(HASH.get('date')))) }
      })
      .catch((e) => setError(e.message))
  }, [])

  const m = manifests?.find((x) => x.id === areaId)
  const layer = m?.layers.find((l) => l.id === layerId) || m?.layers[0]
  const date = m?.dates[dateIndex]
  const n = m?.dates.length || 0

  // Chart data is optional: a missing or broken file just hides the chart.
  useEffect(() => {
    if (m && !(m.id in series)) getJson(`${m.id}/timeseries.json`).catch(() => null).then((ts) => setSeries((s) => ({ ...s, [m.id]: ts })))
  }, [m, series])

  useEffect(() => {
    if (!playing || n < 2) return
    const id = setInterval(() => setDateIndex((i) => (i + 1) % n), 800)
    return () => clearInterval(id)
  }, [playing, n])

  // Preload every frame of the layer so play and scrubbing do not flicker.
  useEffect(() => {
    layer?.dates?.forEach((d) => { new Image().src = `${DATA}${areaId}/${layer.id}/${d}.webp` })
  }, [areaId, layer])

  useEffect(() => {
    if (m) history.replaceState(null, '', '#' + new URLSearchParams({ area: m.id, date, layer: layer.id }))
  }, [m, date, layer])

  // Compare: keep the two cameras identical.
  useEffect(() => {
    const { a, b } = maps
    if (!a || !b) return
    const follow = (from, to) => () => {
      if (to._syncing) return
      from._syncing = true
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() })
      from._syncing = false
    }
    const ab = follow(a, b), ba = follow(b, a)
    a.on('move', ab); b.on('move', ba); ab()
    return () => { a.off('move', ab); b.off('move', ba) }
  }, [maps])

  const setA = useMemo(() => (x) => setMaps((s) => ({ ...s, a: x })), [])
  const setB = useMemo(() => (x) => setMaps((s) => ({ ...s, b: x })), [])

  if (error) return <p role="alert">Could not load the data: {error}. Reload the page to try again.</p>
  if (!manifests) return <p>Loading…</p>
  if (!m) return <p>No study areas published yet.</p>

  const pickArea = (id) => { setAreaId(id); setDateIndex(0); setOtherIndex(0); setPlaying(false) }
  const now = frame(m.id, layer, date)
  const other = frame(m.id, layer, m.dates[otherIndex])
  const ts = series[m.id]
  const pol = layer.id === 'hv' ? 'hv' : 'hh'

  return (
    <>
      <header>
        <h1>Dancing with the SARs</h1>
        <nav aria-label="Study areas">
          {manifests.map((x) => <button key={x.id} aria-pressed={x.id === m.id} onClick={() => pickArea(x.id)}>{x.name}</button>)}
        </nav>
        <button onClick={() => about.current.showModal()}>About</button>
      </header>

      <main>
        <div className="maps">
          <MapView bounds={m.bounds} url={now.url} spots={m.spots} colors={COLORS} onMap={setA} />
          {compare && <MapView bounds={m.bounds} url={other.url} onMap={setB} style={{ clipPath: `inset(0 0 0 ${split}%)` }} />}
        </div>

        <section aria-label="Layer">
          {m.layers.map((l) => <button key={l.id} aria-pressed={l.id === layer.id} onClick={() => setLayerId(l.id)}>{l.label}</button>)}
          <Legend legend={layer.legend} />
          {now.shown && now.shown !== date && <p role="status">No {layer.label} image for {words(date)}. Showing {words(now.shown)}.</p>}
          {!layer.dates && <p role="status">This layer covers the whole period. It does not change with the date.</p>}
        </section>

        <section aria-label="Timeline">
          <Strip dates={m.dates} gaps={m.gaps} index={dateIndex} onPick={setDateIndex} />
          <button onClick={() => setPlaying(!playing)} disabled={n < 2}>{playing ? 'Pause' : 'Play'}</button>
          <input type="range" aria-label="Date" min="0" max={n - 1} value={dateIndex}
            onChange={(e) => { setPlaying(false); setDateIndex(+e.target.value) }} />
          <strong> {words(date)}</strong> <span>(pass {dateIndex + 1} of {n})</span>
        </section>

        <section aria-label="Compare two dates">
          <label><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare two dates</label>
          {compare && (
            <>
              <span> Left: {words(date)}. Right: </span>
              <select aria-label="Right date" value={otherIndex} onChange={(e) => setOtherIndex(+e.target.value)}>
                {m.dates.map((d, i) => <option key={d} value={i}>{words(d)}</option>)}
              </select>
              <input type="range" aria-label="Divider position" min="0" max="100" value={split} onChange={(e) => setSplit(+e.target.value)} />
            </>
          )}
        </section>

        {ts?.series?.length > 0 && (
          <section aria-label="Backscatter over time">
            <Chart ts={ts} pol={pol} gaps={m.gaps} index={Math.max(0, ts.dates.indexOf(date))} colors={COLORS} spots={m.spots}
              onPick={(j) => setDateIndex(Math.max(0, m.dates.indexOf(ts.dates[j])))} />
          </section>
        )}
        {(ts?.facts || NONE).map((f) => {
          const v = f.values[ts.dates.indexOf(date)]
          return v != null && <p key={f.label}>{f.label}: <strong>{v.toLocaleString('en')} {f.unit}</strong></p>
        })}

        <section aria-label="Story">
          <h2>{m.name}</h2>
          {STORY.map(([k, title]) => m.story[k] && <div key={k}><h3>{title}</h3><p>{m.story[k]}</p></div>)}
        </section>
      </main>

      <footer>
        <p>{m.credits}</p>
        <p>Basemap: OpenFreeMap, OpenMapTiles, OpenStreetMap contributors. <a href={REPO}>Source code</a></p>
      </footer>

      <dialog ref={about}>
        <h2>About</h2>
        <p>This app shows how the Earth's surface changed between June and October 2026, seen by the NISAR radar satellite. Pick a place, press Play, and watch water, forest and fields change from one pass to the next. Radar sees through cloud and at night, so no pass is lost to weather.</p>
        <p>Data caveat: these are PROVISIONAL NISAR products. Calibration is not final.</p>
        <form method="dialog"><button>Close</button></form>
      </dialog>
    </>
  )
}
