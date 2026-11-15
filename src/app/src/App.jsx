import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Chart, Strip } from './Charts.jsx'
import TimelineChart from './TimelineChart.jsx'
import { PLANETS } from './planets.js'
import { REGIONS } from './regions.js'
import { frame } from './data.js'

const DATA = import.meta.env.BASE_URL + 'data/'
const REPO = 'https://github.com/Harut200/Nasa-Space-App-Challange-2026'
const COLORS = ['#3b9eff', '#2dd4bf', '#e8f1f5', '#e9a23b', '#c9798f', '#9b8cff'] // chart series: blue and teal lead; red stays reserved for severe
const STORY = [['see', 'What you see'], ['physics', 'What is happening'], ['lband', 'Why radar shows it'], ['people', 'Why it matters']]
const NONE = []
const GEOCODE = 'https://geocoding-api.open-meteo.com/v1/search?count=1&name=' // free, no key
// maplibre-gl is ~1 MB: load it lazily so the shell paints first; both imports start now
const globeImport = import('./Globe.jsx')
const mapImport = import('./MapView.jsx')
const Globe = lazy(() => globeImport)
const MapView = lazy(() => mapImport)
const HASH = new URLSearchParams(location.hash.slice(1)) // link state at page load

const fmt = (o) => { const f = new Intl.DateTimeFormat('en-GB', { ...o, timeZone: 'UTC' }); return (d) => { const t = new Date(d); return isNaN(t) ? 'Invalid Date' : f.format(t) } }
const short = fmt({ day: 'numeric', month: 'short' })
const coord = ([lng, lat]) => `${Math.abs(lat).toFixed(2)}° ${lat < 0 ? 'S' : 'N'}  ${Math.abs(lng).toFixed(2)}° ${lng < 0 ? 'W' : 'E'}`
const worst = (r) => (r.events.some((e) => e.severity === 'high') ? 'high' : r.events.length ? 'low' : null)
const GLOBE_REGIONS = REGIONS.map((r) => ({ ...r, severity: worst(r) }))

const getJson = (path) => fetch(DATA + path).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${path}: ${r.status}`))))
const words = fmt({ day: 'numeric', month: 'long', year: 'numeric' })
const Legend = memo(function Legend({ legend }) {
  if (!legend?.type) return null
  if (legend.type === 'text') return <p className="legend">{legend.text}</p>
  if (legend.type === 'classes') return <ul className="legend classes">{legend.items.map((it) => <li key={it.label}><i style={{ background: it.color }} />{it.label}</li>)}</ul>
  return (
    <div className="legend">
      <span>{legend.min} {legend.unit}</span>
      <span className="legend-bar" style={{ background: `linear-gradient(to right, ${legend.colors.join(',')})` }} />
      <span>{legend.max} {legend.unit}</span>
      {legend.labels && <span className="legend-labels">{legend.labels.join(' ↔ ')}</span>}
    </div>
  )
})

// Re-render only when a breakpoint flips; height matters only in the narrow layout.
function useViewport() {
  const [wide, setWide] = useState(innerWidth >= 900)
  const [mid, setMid] = useState(innerWidth < 1200)
  const [vh, setVh] = useState(innerWidth >= 900 ? 0 : innerHeight)
  useEffect(() => {
    const f = () => { setWide(innerWidth >= 900); setMid(innerWidth < 1200); setVh(innerWidth >= 900 ? 0 : innerHeight) }
    addEventListener('resize', f)
    return () => removeEventListener('resize', f)
  }, [])
  return [wide, mid, vh]
}

const Event = memo(function Event({ e }) {
  return (
    <li className={`event ${e.severity}`}>
      <div className="event-head">
        <span className="label">{e.type}</span>
        <span className="chip">{e.severity === 'high' ? 'High' : 'Moderate'}</span>
      </div>
      <div className="event-prob">
        <span className="dot-num">{Math.round(e.probability * 100)}<small>%</small></span>
        <span className="label">{short(e.from)} – {short(e.to)}</span>
      </div>
      <div className="bar-track" role="img" aria-label={`Probability ${Math.round(e.probability * 100)} percent`}><i style={{ width: `${e.probability * 100}%` }} /></div>
      <p>{e.summary}</p>
    </li>
  )
})

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
  const [planetId, setPlanetId] = useState(PLANETS[0].id)
  const [regionId, setRegionId] = useState(HASH.get('region'))
  const [filters, setFilters] = useState(true) // detection overlays on the globe
  const [wide, mid, vh] = useViewport()
  const about = useRef(null)
  const where = useRef(null)
  const [custom, setCustom] = useState(null) // a place the user asked for; has no forecast
  const [findError, setFindError] = useState(null)
  const [busy, setBusy] = useState(false)

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

  const earth = planetId === PLANETS[0].id
  const planet = PLANETS.find((p) => p.id === planetId)
  // Detection overlays for the globe: each area's newest "water" map (detector output, contract C).
  const overlays = useMemo(() => (manifests || NONE).flatMap((x) => {
    const l = x.layers.find((y) => y.id === 'water' && y.dates?.length)
    return l ? [{ id: x.id, name: x.name, bounds: x.bounds, date: l.dates.at(-1), legend: l.legend, url: `${DATA}${x.id}/water/${l.dates.at(-1)}.webp` }] : []
  }), [manifests])
  // A region inside a detected area flies to that area, close enough to read the overlay. A detected area with no sample region gets its own pin.
  const globeRegions = useMemo(() => {
    const inside = (r, [w, s, e, n]) => r.lngLat[0] > w && r.lngLat[0] < e && r.lngLat[1] > s && r.lngLat[1] < n
    const base = custom ? [...GLOBE_REGIONS, custom] : GLOBE_REGIONS
    const extra = overlays.filter((o) => !base.some((r) => inside(r, o.bounds)))
      .map(({ id, name, bounds: [w, s, e, n] }) => ({ id, name, lngLat: [(w + e) / 2, (s + n) / 2], events: [], severity: null }))
    return [...base, ...extra].map((r) => {
      const o = overlays.find((x) => inside(r, x.bounds))
      if (!o) return r
      const [w, s, e, n] = o.bounds
      return { ...r, overlay: o, focus: [(w + e) / 2, (s + n) / 2], zoom: Math.log2((360 * 380) / (512 * (e - w))) } // area ~380 px wide
    })
  }, [custom, overlays])
  const region = earth ? globeRegions.find((r) => r.id === regionId) : null
  const open = !!region
  // Pixels of the hero that the overlay covers, so the globe centres on the free area.
  const { top, right, bottom, left } = wide
    ? { top: 135, right: open ? 460 : 24, bottom: open || mid ? 96 : 24, left: 24 } // wide + idle: corner controls clear the globe
    : { top: 140, right: 16, bottom: open ? Math.round(vh * 0.46) + 16 : 80, left: 16 }
  const padding = useMemo(() => ({ top, right, bottom, left }), [top, right, bottom, left])

  const now = m && frame(DATA, m.id, layer, date)
  const other = m && frame(DATA, m.id, layer, m.dates[otherIndex])
  const ts = m && series[m.id]
  const pol = layer?.id === 'hv' ? 'hv' : 'hh'
  const ti = ts?.dates ? ts.dates.indexOf(date) : -1
  const onSelect = useCallback((id) => earth && setRegionId(id), [earth])
  const clip = useMemo(() => ({ clipPath: `inset(0 0 0 ${split}%)` }), [split])
  const onPick = useCallback((j) => setDateIndex(Math.max(0, m.dates.indexOf(ts.dates[j]))), [m, ts])
  const pickArea = (id) => { setAreaId(id); setDateIndex(0); setOtherIndex(0); setPlaying(false) }
  const random = () => { const rest = globeRegions.filter((r) => r.id !== regionId); setRegionId(rest[Math.floor(Math.random() * rest.length)].id) }
  // Coordinates win over the city name; a city is a sample region if the name matches, else geocoded.
  const locate = async (e) => {
    e.preventDefault()
    const form = e.target, f = new FormData(form), city = f.get('city').trim(), lat = f.get('lat'), lon = f.get('lon')
    const done = (id) => { setRegionId(id); setFindError(null); form.reset(); where.current.close() }
    let place
    if (lat !== '' && lon !== '') place = { name: 'Custom point', lngLat: [+lon, +lat] }
    else if (city) {
      const known = REGIONS.find((r) => r.name.toLowerCase() === city.toLowerCase())
      if (known) return done(known.id)
      setBusy(true)
      const hit = await fetch(GEOCODE + encodeURIComponent(city)).then((r) => r.json()).then((j) => j.results?.[0]).catch(() => null)
      setBusy(false)
      if (!hit) return setFindError(`Could not find “${city}”. Check the spelling or use coordinates.`)
      place = { name: hit.name, lngLat: [hit.longitude, hit.latitude] }
    } else return setFindError('Enter a city name, or both latitude and longitude.')
    setCustom({ id: 'custom', events: [], severity: null, ...place })
    done('custom')
  }
  const pickPlanet = (id) => { setPlanetId(id); setRegionId(null) }

  return (
    <>
      <section className={`hero${open ? ' open' : ''}`} aria-label="Forecast globe">
        <div className="ghost" style={{ inset: `${top}px ${right}px ${bottom}px ${left}px` }} aria-hidden="true" />
        <Suspense fallback={null}><Globe planet={planetId} regions={earth ? globeRegions : NONE} selectedId={region?.id ?? null} onSelect={onSelect} padding={padding} overlays={filters ? overlays : NONE} /></Suspense>
        <div className="hero-ui">
          <header className="bar">
            <h1>Dancing with the <em>SARs</em></h1>
            <div className="row">
              {earth && <label className="switch"><input type="checkbox" checked={filters} onChange={(e) => setFilters(e.target.checked)} /><span>Detection</span></label>}
              <button className="pill" onClick={() => about.current.showModal()}>About</button>
            </div>
          </header>

          {earth ? (
            <nav className="tabs" aria-label="Choose a place">
              <button className="pill" onClick={random}>Random</button>
              <button className="pill" onClick={() => where.current.showModal()}>By location</button>
            </nav>
          ) : (
            <p className="soon">No forecast for {planet.name} — yet.</p>
          )}

          {region ? (
            <aside className="panel" aria-label={`Forecast for ${region.name}`}>
              <div className="panel-top">
                <span className="tag">Sample data</span>
                <button className="pill icon" onClick={() => setRegionId(null)} aria-label="Back to the whole globe">×</button>
              </div>
              <h2>{region.name}</h2>
              <p className="label">{coord(region.lngLat)}</p>
              <p className="label count">{region.events.length} predicted {region.events.length === 1 ? 'event' : 'events'}</p>
              {filters && region.overlay && <div className="detected"><p className="label">Detected by radar · {words(region.overlay.date)}</p><Legend legend={region.overlay.legend} /></div>}
              {!region.events.length && <p className="empty">No forecast for this place yet.</p>}
              <ul className="events">{region.events.map((e) => <Event key={e.type + e.from} e={e} />)}</ul>
            </aside>
          ) : null}

          <div className="foot">
            <div className="seg" role="group" aria-label="Planet">
              {PLANETS.map((p) => <button key={p.id} aria-pressed={p.id === planetId} onClick={() => pickPlanet(p.id)}>{p.name}</button>)}
            </div>
          </div>
        </div>
      </section>

      <main id="radar">
        <div className="head">
          <div>
            <p className="label">NISAR L-band · provisional</p>
            <h2 className="display">Radar record</h2>
          </div>
          {manifests && (
            <nav aria-label="Study areas" className="row">
              {manifests.map((x) => <button key={x.id} className="pill" aria-pressed={x.id === m?.id} onClick={() => pickArea(x.id)}>{x.name}</button>)}
            </nav>
          )}
        </div>

        {error && <p className="state" role="alert">Could not load the data: {error}. Reload the page to try again.</p>}
        {!error && !manifests && <p className="state" role="status"><span className="spin" aria-hidden="true" />Loading radar data…</p>}
        {manifests && !m && <p className="state">No study areas published yet.</p>}

        {m && (
          <div className="radar">
            <div className="col">
              <div className="maps">
                <Suspense fallback={null}><MapView bounds={m.bounds} url={now.url} spots={m.spots} colors={COLORS} onMap={setA} /></Suspense>
                {compare && <Suspense fallback={null}><MapView bounds={m.bounds} url={other.url} onMap={setB} style={clip} /></Suspense>}
                {compare && <span className="label side l">{words(date)}</span>}
                {compare && <span className="label side r">{words(m.dates[otherIndex])}</span>}
              </div>

              <section aria-label="Layer" className="card">
                <div className="row">
                  {m.layers.map((l) => <button key={l.id} className="pill" aria-pressed={l.id === layer.id} onClick={() => setLayerId(l.id)}>{l.label}</button>)}
                </div>
                <Legend legend={layer.legend} />
                {now.shown && now.shown !== date && <p className="note" role="status">No {layer.label} image for {words(date)}. Showing {words(now.shown)}.</p>}
                {!layer.dates && <p className="note" role="status">This layer covers the whole period. It does not change with the date.</p>}
              </section>

              <section aria-label="Timeline" className="card">
                <div className="date-row">
                  <button className="pill play" onClick={() => setPlaying(!playing)} disabled={n < 2}>{playing ? 'Pause' : 'Play'}</button>
                  <strong className="date">{words(date)}</strong>
                  <span className="label">Pass {dateIndex + 1} / {n}</span>
                </div>
                <Strip dates={m.dates} gaps={m.gaps} index={dateIndex} onPick={setDateIndex} />
                <input type="range" aria-label="Date" min="0" max={n - 1} value={dateIndex}
                  onChange={(e) => { setPlaying(false); setDateIndex(+e.target.value) }} />
              </section>

              <section aria-label="Compare two dates" className="card">
                <label className="switch"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /><span>Compare two dates</span></label>
                {compare && (
                  <div className="compare">
                    <label className="label">Right date
                      <select aria-label="Right date" value={otherIndex} onChange={(e) => setOtherIndex(+e.target.value)}>
                        {m.dates.map((d, i) => <option key={d} value={i}>{words(d)}</option>)}
                      </select>
                    </label>
                    <input type="range" aria-label="Divider position" min="0" max="100" value={split} onChange={(e) => setSplit(+e.target.value)} />
                  </div>
                )}
              </section>

              {ts?.facts?.[0]?.values?.some((x) => x != null) && (
                <section aria-label="Water area over time" className="card">
                  <figcaption>
                    <span className="label"><strong>Water area on each pass</strong> · drag to move through time</span>
                    <span className="key"><span style={{ color: 'var(--accent)' }}>● Open water</span><span style={{ color: 'var(--teal)' }}>● Flooded vegetation</span></span>
                  </figcaption>
                  <TimelineChart dates={ts.dates} facts={ts.facts} gaps={m.gaps} quality={m.quality} index={Math.max(0, ti)} onIndex={onPick} playing={playing} />
                </section>
              )}
              {ts?.series?.length > 0 && (
                <section aria-label="Backscatter over time" className="card">
                  <Chart ts={ts} pol={pol} gaps={m.gaps} index={Math.max(0, ti)} colors={COLORS} spots={m.spots} onPick={onPick} />
                </section>
              )}
              {(ts?.facts || NONE).some((f) => f.values[ti] != null) && (
                <div className="facts">
                  {ts.facts.map((f) => {
                    const v = f.values[ti]
                    return v != null && <p key={f.label}><span className="dot-num">{v.toLocaleString('en')}<small> {f.unit}</small></span><span className="label">{f.label}</span></p>
                  })}
                </div>
              )}
            </div>

            <section aria-label="Story" className="story">
              <p className="label">The story</p>
              <h2 className="display">{m.name}</h2>
              {STORY.some(([k]) => m.story[k]) ? STORY.map(([k, title]) => m.story[k] && <div key={k}><h3>{title}</h3><p>{m.story[k]}</p></div>)
                : <p className="empty">The story for this area arrives once change detection has run.</p>}
            </section>
          </div>
        )}
      </main>

      <footer>
        <p>{m?.credits}</p>
        <p>Globe imagery: NASA GIBS Blue Marble; Sentinel-2 cloudless by EOX. Basemap: OpenFreeMap, OpenMapTiles, OpenStreetMap contributors. <a href={REPO}>Source code</a></p>
      </footer>

      <dialog ref={about} onClick={(e) => e.target === about.current && about.current.close()}>
        <p className="label">About</p>
        <h2 className="display">Dancing with the SARs</h2>
        <p>This app shows how the Earth's surface changed between June and October 2026, seen by the NISAR radar satellite. Pick a place, press Play, and watch water, forest and fields change from one pass to the next. Radar sees through cloud and at night, so no pass is lost to weather.</p>
        <p>The forecast globe above shows sample data only. Data caveat: these are PROVISIONAL NISAR products. Calibration is not final.</p>
        <form method="dialog"><button className="pill">Close</button></form>
      </dialog>

      <dialog ref={where} onClick={(e) => e.target === where.current && where.current.close()}>
        <p className="label">By location</p>
        <form className="where" onSubmit={locate}>
          <label className="label">City name<input name="city" type="text" autoComplete="off" placeholder="Yerevan" /></label>
          <details>
            <summary className="label">Advanced</summary>
            <label className="label">Latitude<input name="lat" type="number" min="-90" max="90" step="any" placeholder="40.18" /></label>
            <label className="label">Longitude<input name="lon" type="number" min="-180" max="180" step="any" placeholder="44.51" /></label>
          </details>
          {findError && <p className="note" role="alert">{findError}</p>}
          <div className="row"><button className="pill" aria-pressed="true" disabled={busy}>{busy ? 'Finding…' : 'Go'}</button><button type="button" className="pill" onClick={() => where.current.close()}>Cancel</button></div>
        </form>
      </dialog>
    </>
  )
}
