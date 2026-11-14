import { useEffect, useMemo, useRef, useState } from 'react'
import MapView from './MapView.jsx'
import { Chart, Strip } from './Charts.jsx'

const DATA = import.meta.env.BASE_URL + 'data/'
const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'
const COLORS = ['#ff6b6b', '#5aa9ff', '#5ad17a', '#ffb347', '#c792ff', '#f0e442', '#5fd4c4']
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
  if (legend.type === 'text') return <p className="legend-text">{legend.text}</p>
  if (legend.type === 'classes') {
    return (
      <ul className="legend-classes">
        {(legend.items || NONE).map((it) => (
          <li key={it.label}><i style={{ background: it.color }} />{it.label}</li>
        ))}
      </ul>
    )
  }
  // A short unit ("dB") goes next to the numbers; a long one gets its own line.
  const long = legend.unit && legend.unit.length > 6
  const u = legend.unit && !long ? ` ${legend.unit}` : ''
  return (
    <div className="legend-ramp">
      <div className="legend-bar" style={{ background: `linear-gradient(to right, ${(legend.colors || ['#000', '#fff']).join(',')})` }} />
      <div className="legend-ends"><span>{legend.min}{u}</span><span>{legend.max}{u}</span></div>
      {legend.labels && <div className="legend-ends legend-labels">{legend.labels.map((l) => <span key={l}>{l}</span>)}</div>}
      {long && <p className="legend-unit">{legend.unit}</p>}
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

  if (error) return <p role="alert" className="splash">Could not load the data: {error}. Reload the page to try again.</p>
  if (!manifests) return <p className="splash">Loading…</p>
  if (!m) return <p className="splash">No study areas published yet.</p>

  const pickArea = (id) => { setAreaId(id); setDateIndex(0); setOtherIndex(0); setPlaying(false) }
  const now = frame(m.id, layer, date)
  const other = frame(m.id, layer, m.dates[otherIndex])
  const ts = series[m.id]
  const pol = layer.id === 'hv' ? 'hv' : 'hh'

  const step = (d) => { setPlaying(false); setDateIndex((i) => Math.min(n - 1, Math.max(0, i + d))) }
  const preview = m.source === 'preview'
  const facts = (ts?.facts || NONE).map((f) => { const i = ts.dates.indexOf(date); return { ...f, v: f.values[i], lo: f.lo?.[i], hi: f.hi?.[i] } }).filter((f) => f.v != null)
  const km = (v) => Math.round(v).toLocaleString('en')
  const quality = m?.quality?.[date]

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Dancing with the SARs</h1>
          <p className="pitch">Press play on a season of radar</p>
        </div>
        <nav aria-label="Study areas" className="areas">
          {manifests.map((x) => <button key={x.id} className="chip" aria-pressed={x.id === m.id} onClick={() => pickArea(x.id)}>{x.name}</button>)}
        </nav>
        <button className="btn ghost about-btn" onClick={() => about.current.showModal()}>About</button>
      </header>

      <main className="layout">
        <div className="stage">
          <div className="maps">
            <MapView bounds={m.bounds} url={now.url} spots={m.spots} colors={COLORS} onMap={setA} />
            {compare && <MapView bounds={m.bounds} url={other.url} onMap={setB} style={{ clipPath: `inset(0 0 0 ${split}%)` }} />}
            {compare && <div className="divider" style={{ left: `${split}%` }} aria-hidden="true" />}
            <div className="map-tags" aria-hidden="true">
              <span className="map-tag">{compare ? 'Left: ' : ''}{layer.dates ? words(now.shown) : 'Whole season'}</span>
              {compare && <span className="map-tag">Right: {layer.dates ? words(other.shown) : 'Whole season'}</span>}
            </div>
          </div>
          {preview && (
            <p className="map-notice"><strong>Preview images</strong>, about 180 m per pixel, uncalibrated: patterns and timing are reliable, absolute values are not.</p>
          )}

          <section aria-label="Timeline" className="timeline">
            <div className="timeline-head">
              <button className="btn primary play" onClick={() => setPlaying(!playing)} disabled={n < 2} aria-label={playing ? 'Pause' : 'Play'}>
                <span aria-hidden="true">{playing ? '❚❚' : '▶'}</span> {playing ? 'Pause' : 'Play'}
              </button>
              <button className="btn icon" onClick={() => step(-1)} disabled={dateIndex === 0} aria-label="Previous pass">‹</button>
              <button className="btn icon" onClick={() => step(1)} disabled={dateIndex === n - 1} aria-label="Next pass">›</button>
              <p className="timeline-date" aria-live="polite">
                <strong>{words(date)}</strong> <span>pass {dateIndex + 1} of {n}</span>
              </p>
            </div>
            <Strip dates={m.dates} gaps={m.gaps} quality={m.quality} index={dateIndex} onPick={(i) => { setPlaying(false); setDateIndex(i) }} />
            {quality && <p className={`quality-note ${quality.quality}`} role="status"><strong>{quality.quality === 'fail' ? 'Faulty pass' : 'Caution'}{quality.channels?.length ? ` (${quality.channels.join(', ')})` : ''}:</strong> {quality.quality_note}{quality.quality === 'fail' ? ' Left out of change detection.' : ''}</p>}
            {now.shown && now.shown !== date && <p role="status" className="status">No “{layer.label}” image for {words(date)}. Showing the nearest one, {words(now.shown)}.</p>}
            {!layer.dates && (
              <p role="status" className="status">“{layer.label}” sums up the whole season, so it stays the same as you move the timeline{ts?.series?.length ? '; the chart still follows the date' : ''}.</p>
            )}
          </section>
        </div>

        <aside className="panel">
          <section aria-label="Layer" className="card">
            <h2 className="eyebrow">Layer</h2>
            <div className="seg">
              {m.layers.map((l) => <button key={l.id} className="chip" aria-pressed={l.id === layer.id} onClick={() => setLayerId(l.id)}>{l.label}</button>)}
            </div>
            <Legend legend={layer.legend} />
          </section>

          {(ts?.series?.length > 0 || facts.length > 0) && (
            <section aria-label="Backscatter over time" className="card">
              {facts.length > 0 && (
                <dl className="facts">
                  {facts.map((f) => <div key={f.label}><dt>{f.label}</dt><dd>{f.lo != null ? '≈ ' : ''}{km(f.v)} <span>{f.unit}{f.lo != null && ` (range ${km(f.lo)}–${km(f.hi)})`}</span></dd></div>)}
                </dl>
              )}
              {ts?.series?.length > 0 && (
                <Chart ts={ts} pol={pol} gaps={m.gaps} index={Math.max(0, ts.dates.indexOf(date))} colors={COLORS} spots={m.spots}
                  onPick={(j) => { setPlaying(false); setDateIndex(Math.max(0, m.dates.indexOf(ts.dates[j]))) }} />
              )}
            </section>
          )}

          <section aria-label="Compare two dates" className="card">
            <label className="check"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare two dates</label>
            {compare && (
              <div className="compare">
                {!layer.dates && <p className="status">This layer has one image for the whole season, so both sides look the same. Pick a dated layer to compare.</p>}
                <p className="muted">Left: {words(date)} (the timeline)</p>
                <label className="field">Right
                  <select value={otherIndex} onChange={(e) => setOtherIndex(+e.target.value)}>
                    {m.dates.map((d, i) => <option key={d} value={i}>{words(d)}</option>)}
                  </select>
                </label>
                <label className="field">Divider
                  <input type="range" min="0" max="100" value={split} onChange={(e) => setSplit(+e.target.value)} />
                </label>
              </div>
            )}
          </section>

          <section aria-label="Story" className="card story">
            <h2>{m.name}</h2>
            {STORY.some(([k]) => m.story?.[k])
              ? STORY.map(([k, title]) => m.story[k] && <div key={k}><h3>{title}</h3><p>{m.story[k]}</p></div>)
              : <p className="muted">The story for this place is not written yet. Press Play and watch the image change.</p>}
          </section>
        </aside>
      </main>

      <footer className="foot">
        <p>{m.credits}</p>
        <p>Basemap: OpenFreeMap, OpenMapTiles, OpenStreetMap contributors. <a href={REPO}>Source code</a></p>
      </footer>

      <dialog ref={about} className="about">
        <h2>About</h2>
        <p>This app shows how the Earth's surface changed between June and October 2026, seen by the NISAR radar satellite. Pick a place, press Play, and watch water, forest and fields change from one pass to the next. Radar sees through cloud and at night, so no pass is lost to weather.</p>
        <p>Between 27 July and 10 August the instrument was off, so there are no passes then. The timeline shows that gap.</p>
        {preview && <p><strong>Preview images.</strong> The pictures for {m.name} are uncalibrated previews at about 180 m per pixel. Patterns and timing are reliable; absolute brightness values are not.</p>}
        <p>Data caveat: these are PROVISIONAL NISAR products. Calibration is not final.</p>
        <form method="dialog"><button className="btn primary">Close</button></form>
      </dialog>
    </div>
  )
}
