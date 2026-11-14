import { Chart, Strip } from './Charts.jsx'
import { words, dayMonth, dayMon, km, shortLabel, splitLayers, DOT_COLOR, useTween, isEvent, eventTip, placeSummary, groupPlaces, landingText, classList, placeStyle, SHAPE, SHAPE_CLASSES, eventType, noisyView, fringeCycle, eventViews, viewMotion, viewControl, viewingNote, magnitude, utcStamp, fmtVal, withUnit } from './meta.js'

export const COLORS = ['#ff6b6b', '#5aa9ff', '#5ad17a', '#ffb347', '#c792ff', '#f0e442', '#5fd4c4']
const STORY = [['see', 'What you see'], ['physics', 'What is happening'], ['lband', 'Why radar shows it'], ['people', 'Why it matters']]
const NONE = []
const GREY = ['hh', 'hv', 'rgb'] // grey radar images start a little see-through so the terrain shows

export function Fact({ p }) {
  if (p.kind === 'event') {
    const { head, rest } = eventTip(p)
    return <><strong className="tip-name">{head}</strong>{rest && <span className="tip-num">{rest}</span>}</>
  }
  return (
    <>
      <strong className="tip-name">{p.name}</strong>
      {p.peak && p.peak.v >= 1
        ? <span className="tip-num"><b>{km(p.peak.v)} km²</b> open water at its peak ({dayMonth(p.peak.date)})</span>
        : <span className="tip-num">{p.note || 'No open water mapped here: a land-surface story'}</span>}
    </>
  )
}

function DotKey({ style }) {
  if (style === 'quake') return <i className="quake-key" style={{ borderColor: DOT_COLOR.quake, background: DOT_COLOR.quake }} />
  if (SHAPE_CLASSES.includes(style)) return <i className={`shape-key ${SHAPE[style]}`} style={{ background: DOT_COLOR[style] }} />
  return <i style={{ background: DOT_COLOR[style] }} />
}

export function Landing({ places, summary, tip, selected, onOpen, onRandom, onNear, near, onAbout }) {
  const tp = places.find((p) => p.id === tip?.id)
  const sel = places.find((p) => p.id === selected)
  const firstSeason = places.find((p) => p.kind !== 'event'), firstQuake = places.find((p) => p.kind === 'event' && eventType({ kind: 'event', event: p.event }) === 'earthquake')
  const styles = new Set(places.map(placeStyle))
  const cls = classList(places, summary)
  const label = (id) => cls.find((c) => c.id === id)?.label
  return (
    <div className="ui landing">
      <header className="top">
        <div className="glass brand"><span className="logo" aria-hidden="true" /><h1>Dancing with the SARs</h1></div>
        <button className="glass btn" onClick={onAbout}>About</button>
      </header>
      {tip && tp && <div className="glass tip" style={{ left: tip.x, top: tip.y }} role="tooltip"><Fact p={tp} /></div>}
      <section className="glass dock" aria-label="Start">
        {sel && <div className="sheet-tip" role="dialog" aria-label={sel.name}><Fact p={sel} /><button className="btn solid" onClick={() => onOpen(sel.id)}>Open this place</button></div>}
        <p className="lede">Press play on a season of radar. {landingText(places, summary)} Pick a dot on the globe.</p>
        <div className="legend-dots" aria-label="Dot legend">
          {styles.has('big') && <span><DotKey style="big" />Water: over 1,000 km² of open water at peak</span>}
          {styles.has('some') && <span><DotKey style="some" />Water: smaller</span>}
          {styles.has('land') && <span><DotKey style="land" />Land: no open water mapped</span>}
          {styles.has('quake') && <span><DotKey style="quake" />Earthquake: ground moved</span>}
          {['fire', 'volcano', 'landslide', 'other'].filter((k) => styles.has(k)).map((k) => <span key={k}><DotKey style={k} />{k === 'other' ? 'Other' : label(k)}</span>)}
          <span className="faint">Bigger dot: more water, or a stronger earthquake</span>
        </div>
        <div className="row">
          {firstSeason && <button className="btn solid" onClick={() => onOpen(firstSeason.id)}>Start with {firstSeason.name.split(',')[0]}</button>}
          {firstQuake && <button className="btn" onClick={() => onOpen(firstQuake.id)}>See an earthquake</button>}
          <button className="btn" onClick={onRandom}>Random place</button>
          <button className="btn" onClick={onNear}>Nearest to me</button>
        </div>
        {near && <p className="near" role="status">{near}</p>}
      </section>
    </div>
  )
}

const minus = (v) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v).replace(/^-(?=\d)/, '\u2212') : '')
function Legend({ legend }) {
  if (!legend?.type) return null
  if (legend.type === 'text') return <p className="legend-text">{legend.text}</p>
  if (legend.type === 'classes') {
    return <ul className="legend-classes">{(legend.items || NONE).map((it) => <li key={it.label}><i style={{ background: it.color }} />{it.label}</li>)}</ul>
  }
  const long = legend.unit && legend.unit.length > 6
  const u = legend.unit && !long ? ` ${legend.unit}` : ''
  return (
    <div className="legend-ramp">
      <div className="legend-bar" style={{ background: `linear-gradient(to right, ${(legend.colors || ['#000', '#fff']).join(',')})` }} />
      <div className="legend-ends"><span>{minus(legend.min)}{u}</span><span>{minus(legend.max)}{u}</span></div>
      {legend.labels && <div className="legend-ends legend-labels">{legend.labels.map((l) => <span key={l}>{l}</span>)}</div>}
      {long && <p className="legend-unit">{legend.unit}</p>}
    </div>
  )
}

// Event headline. An earthquake: magnitude (strongest), how far the ground moved (second), the line-of-sight and control clauses, then where and when.
// Volcano, landslide and others: the manifest's own summary sentence, then where and when. A view whose control is as large as its signal is greyed out.
function EventHeadline({ e, onStory, reveal, onReveal, revealOn }) {
  const quake = (e.type || 'earthquake') === 'earthquake'
  const views = eventViews(e).filter((v) => viewMotion(v)), many = views.length > 1
  const tag = (v) => `${v.label[0].toUpperCase() + v.label.slice(1)}, track ${v.track}`
  const good = views.filter((v) => !noisyView(v)), ctl = good.filter((v) => viewControl(v))
  const mag = magnitude(e)
  const when = [e.time && utcStamp(e.time), Number.isFinite(e.depth_km) && `depth ${e.depth_km} km`].filter(Boolean).join(' · ')
  return (
    <div className="glass headline ev-head" aria-label="Headline number">
      {quake ? <p className="hl-num"><span>{mag ? `M ${mag}` : 'Earthquake'}</span></p> : <p className="hl-label hl-big">{e.summary || e.title}</p>}
      {quake && views.map((v) => (noisyView(v)
        ? <p className="hl-motion noisy" key={v.id}>{many && <small>{tag(v)}</small>}too noisy to use</p>
        : <p className="hl-motion" key={v.id}>{many && <small>{tag(v)}</small>}<b>{viewMotion(v)}</b> ground motion</p>))}
      {quake && good.length > 0 && (
        <p className="hl-clause">along the radar's line of sight{ctl.length > 0 && ` · same track before the earthquake: ${ctl.map((v) => `${many ? `track ${v.track} ` : ''}${viewControl(v)}`).join('; ')}`}</p>
      )}
      <p className="hl-label">{e.place && <span className="hl-where">{e.place}</span>}<span className="nw">{when}</span>{e.time_note && <span className="hl-where hl-note">Date: {e.time_note}</span>}</p>
      <div className="hl-actions">
        <button className="chip" onClick={onStory}>Story</button>
        {reveal && <button className="chip" aria-pressed={revealOn} onClick={onReveal}>Reveal</button>}
        {e.source_url && <a className="chip" href={e.source_url} target="_blank" rel="noreferrer">Source</a>}
      </div>
    </div>
  )
}

const MIDDLE = { earthquake: ['⚡', 'earthquake'], volcano: ['', 'eruption'], landslide: ['', 'collapse'] }
function EventBar({ e, layers }) {
  const views = eventViews(e), many = views.length > 1
  const type = e.type || 'earthquake', quake = type === 'earthquake'
  const [icon, word] = MIDDLE[type] || ['', 'event']
  const cycle = fringeCycle(layers)
  const noisy = views.filter(noisyView)
  return (
    <section aria-label="Before and after" className="glass timeline evbar">
      {views.map((v) => (
        <p className={`ev-pass${noisyView(v) ? ' noisy' : ''}`} key={v.id}>
          {many && <span className="ev-view">{v.label[0].toUpperCase() + v.label.slice(1)}, track {v.track}</span>}
          <span className="ev-dates"><b>{dayMon(v.before)}</b><i aria-hidden="true">→</i><b className="ev-quake">{icon && <span aria-hidden="true">{icon} </span>}{e.time ? dayMon(e.time) : word}</b><i aria-hidden="true">→</i><b>{dayMon(v.after)}</b></span>
          <span className="ev-cap">before · {word} · after</span>
        </p>
      ))}
      <p className="status">{quake
        ? `Two radar passes, one before and one after.${cycle ? ` Each full colour cycle in the fringes is ${cycle} cm of ground motion toward or away from the satellite.` : ''}`
        : 'Two radar passes, one before and one after. Where the surface changed completely, the radar no longer recognises it.'}</p>
      {noisy.length > 0 && <p className="notice">{noisy.map((v) => `Track ${v.track}`).join(', ')}: the same track before the earthquake moved nearly as much as the earthquake pair, so {noisy.length > 1 ? 'those views are' : 'that view is'} too noisy to use.</p>}
      <p className="notice">{quake ? viewingNote(e) : 'Provisional data: this shows where the surface changed, not what it is made of or how thick it is.'}</p>
    </section>
  )
}

export function PlaceUI({ m, ts, v, patch, places, summary, layer, date, preview, otherDates, note, onBack, onPlace, onRandom, onAbout }) {
  const n = m.dates.length
  const ev = isEvent(m)
  const { main, more } = splitLayers(m.layers, ev, m.defaultLayer ? [m.defaultLayer] : [])
  const di = ts?.dates.indexOf(date)
  const facts = (ts?.facts || NONE).map((f) => ({ ...f, v: f.values[di], lo: f.lo?.[di], hi: f.hi?.[di] })).filter((f) => f.v != null)
  const head = facts[0]
  const flood = facts.find((f) => f.label === 'Flooded vegetation')
  const shownV = useTween(head?.v)
  const none = m.headline === 'none' // a place whose story is not water: no open-water number, the manifest's own note instead
  const noWater = none || (head?.label === 'Open water' && !(places.find((p) => p.id === m.id)?.peak?.v >= 1)) // same rule as the globe tooltip
  const pct = !none && facts.find((f) => f.unit === '%') // a land place with a % story (Ararat): that number leads
  const quality = m.quality?.[date]
  const idx = places.findIndex((p) => p.id === m.id)
  const hop = (d) => onPlace(places[(idx + d + places.length) % places.length].id)
  const rng = (f) => (f.lo != null ? `range ${fmtVal(f.lo, f.unit)}–${fmtVal(f.hi, f.unit)}` : null)
  const pick = (i) => patch({ dateIndex: i, playing: false, touched: true })
  const step = (d) => pick(Math.min(n - 1, Math.max(0, v.dateIndex + d)))
  const chip = (l) => <button key={l.id} className="chip" title={l.label} aria-pressed={l.id === layer.id} onClick={() => patch({ layerId: l.id, touched: true, ...(ev ? {} : { opacity: GREY.includes(l.id) ? 0.8 : 0.9 }), ...(l.dates || ev ? {} : { compare: false }) })}>{shortLabel(l)}</button>

  return (
    <div className={`ui place${ev ? ` ev ev${Math.min(2, eventViews(m.event).length)}` : ''}`}>
      <header className="top">
        <div className="glass group">
          <button className="btn plain" onClick={onBack} aria-label="Back to the globe"><span aria-hidden="true">‹</span><span className="wide-only"> Globe</span></button>
          <h1 className="vtitle">{m.name}</h1>
        </div>
        <div className="glass group right">
          <button className="btn plain icon" onClick={() => hop(-1)} aria-label="Previous place">‹</button>
          <button className="btn plain" aria-expanded={v.list} onClick={() => patch({ list: !v.list })}>Places <span aria-hidden="true">▾</span></button>
          <button className="btn plain icon" onClick={() => hop(1)} aria-label="Next place">›</button>
          <button className="btn plain" onClick={onRandom}>Random</button>
          <button className="btn plain" onClick={onAbout}>About</button>
        </div>
        {v.list && (
          <>
            <button className="scrim" tabIndex={-1} aria-label="Close the list" onClick={() => patch({ list: false })} />
            <ul className="glass drawer" role="menu">
              {groupPlaces(places, summary).map((g) => [
                <li key={g.key} role="presentation" className="grp">{g.title}</li>,
                ...g.items.map((p) => <li key={p.id}><button role="menuitem" aria-current={p.id === m.id} onClick={() => { patch({ list: false }); onPlace(p.id) }}>
                  <span>{p.name}</span><small>{placeSummary(p)}</small></button></li>),
              ])}
            </ul>
          </>
        )}
      </header>

      {!v.sheet && ev && <EventHeadline e={m.event} onStory={() => patch({ sheet: 'story' })} reveal={ev} revealOn={!!v.compare} onReveal={() => patch({ compare: !v.compare })} />}
      {!v.sheet && !ev && (
        <div className="glass headline" aria-label="Headline number">
          {noWater && pct ? (
            <>
              <p className="hl-num"><span>{fmtVal(pct.v, pct.unit)}</span><small>{pct.unit === '%' ? '' : ' '}{pct.unit}</small></p>
              <p className="hl-label">{pct.label.toLowerCase()} · {words(date)}</p>
            </>
          ) : none || (head && noWater) ? (
            <>
              <p className="hl-label hl-big">{(none && m.headlineNote) || 'No open water mapped here: a land-surface story'}</p>
              <p className="hl-sub">{words(date)}</p>
            </>
          ) : head ? (
            <>
              <p className="hl-num">{head.lo != null && <small>≈ </small>}<span>{fmtVal(shownV, head.unit)}</span><small>{head.unit === '%' ? '' : ' '}{head.unit}</small></p>
              <p className="hl-label">{head.label.toLowerCase()} · {words(date)}</p>
              {(rng(head) || flood) && <p className="hl-sub">{[rng(head), flood && `${withUnit(flood.v, flood.unit)} flooded vegetation`].filter(Boolean).join(' · ')}</p>}
            </>
          ) : <p className="hl-label">{words(date)}</p>}
          <div className="hl-actions">
            <button className="chip" onClick={() => patch({ sheet: 'story' })}>Story</button>
            {ts?.series?.length > 0 && <button className="chip" onClick={() => patch({ sheet: 'curves' })}>Curves</button>}
            <button className="chip" onClick={() => patch({ sheet: 'compare', compare: !!layer.dates })}>Compare</button>
          </div>
        </div>
      )}

      <aside className={`glass layers${v.layersOpen ? ' open' : ''}`} aria-label="Layer">
        <button className="layers-head" aria-expanded={v.layersOpen} onClick={() => patch({ layersOpen: !v.layersOpen })}>
          <span className="eyebrow">Layer</span><strong>{shortLabel(layer)}</strong><span aria-hidden="true" className="caret">▾</span>
        </button>
        {v.layersOpen && (
          <div className="layers-body">
            <div className="seg">{main.map(chip)}</div>
            {more.length > 0 && (
              <details className="more" open={more.some((l) => l.id === layer.id) || undefined}>
                <summary>More layers ({more.length})</summary>
                <div className="seg">{more.map(chip)}</div>
              </details>
            )}
            {layer.label !== shortLabel(layer) && <p className="layer-full">{layer.label}</p>}
            <Legend legend={layer.legend} />
            <label className="opacity">{ev ? 'Layer opacity' : 'Radar opacity'}
              <input type="range" min="0.15" max="1" step="0.05" value={v.opacity} onChange={(e) => patch({ opacity: +e.target.value })} aria-label="Radar layer opacity" />
            </label>
          </div>
        )}
      </aside>

      {v.sheet && (
        <aside className="glass side-sheet" aria-label="Details">
          <div className="sheet-head">
            <div className="tabs" role="tablist">
              {(ev ? [['story', 'Story']] : [['story', 'Story'], ['curves', 'Curves'], ['compare', 'Compare']]).map(([k, t]) => <button key={k} role="tab" aria-selected={v.sheet === k} onClick={() => patch({ sheet: k, compare: k === 'compare' ? !!layer.dates : v.compare })}>{t}</button>)}
            </div>
            <button className="btn plain icon" onClick={() => patch({ sheet: null })} aria-label="Close details">×</button>
          </div>
          <div className="sheet-body">
            {v.sheet === 'story' && (
              <div className="story">
                <h2>{m.name}</h2>
                {STORY.some(([k]) => m.story?.[k])
                  ? STORY.map(([k, title]) => m.story[k] && <div key={k}><h3>{title}</h3><p>{m.story[k]}</p></div>)
                  : <p className="muted">The story for this place is not written yet. Press Play and watch the image change.</p>}
                <p className="credit">{m.credits}</p>
              </div>
            )}
            {v.sheet === 'curves' && (
              <div>
                {facts.length > 0 && (
                  <dl className="facts">
                    {facts.map((f) => <div key={f.label}><dt>{f.label}</dt><dd>{f.lo != null ? '≈ ' : ''}{fmtVal(f.v, f.unit)}{f.unit === '%' ? '' : ' '}<span>{f.unit}{f.lo != null && ` (range ${fmtVal(f.lo, f.unit)}–${fmtVal(f.hi, f.unit)})`}</span></dd></div>)}
                  </dl>
                )}
                {ts?.series?.length > 0
                  ? <Chart ts={ts} pol={layer.id === 'hv' ? 'hv' : 'hh'} gaps={m.gaps} index={Math.max(0, ts.dates.indexOf(date))} colors={COLORS} spots={m.spots} onPick={(j) => pick(Math.max(0, m.dates.indexOf(ts.dates[j])))} />
                  : <p className="muted">No curves for this place.</p>}
              </div>
            )}
            {v.sheet === 'compare' && (
              <div className="compare">
                <label className="check"><input type="checkbox" disabled={!layer.dates} checked={!!(v.compare && layer.dates)} onChange={(e) => patch({ compare: e.target.checked })} /> Compare two dates</label>
                {!layer.dates && <p className="status">Compare is off: this layer is one image for the whole season, so both sides would look the same. Pick a dated layer (for example Water and flooded vegetation) to compare.</p>}
                <p className="muted">Left: {words(date)} (the timeline)</p>
                <label className="field">Right
                  <select value={v.otherIndex} onChange={(e) => patch({ otherIndex: +e.target.value })}>
                    {m.dates.map((d, i) => <option key={d} value={i}>{words(d)}</option>)}
                  </select>
                </label>
                {v.compare && layer.dates && <p className="muted">Right: {otherDates}. Drag the round handle on the map to move the divider.</p>}
              </div>
            )}
          </div>
        </aside>
      )}

      {ev ? <EventBar e={m.event} layers={m.layers} /> : (
      <section aria-label="Timeline" className="glass timeline">
        <div className="timeline-head">
          <button className="btn solid play" onClick={() => patch({ playing: !v.playing, touched: true, ...(!v.playing && v.dateIndex >= n - 1 ? { dateIndex: 0 } : {}) })} disabled={n < 2} aria-label={v.playing ? 'Pause' : 'Play'}>
            <span aria-hidden="true">{v.playing ? '❚❚' : '▶'}</span> {v.playing ? 'Pause' : 'Play'}
          </button>
          <button className="btn icon" onClick={() => step(-1)} disabled={v.dateIndex === 0} aria-label="Previous pass">‹</button>
          <button className="btn icon" onClick={() => step(1)} disabled={v.dateIndex === n - 1} aria-label="Next pass">›</button>
          <p className="timeline-date" aria-live="polite"><strong>{dayMonth(date)} 2026</strong> <span>pass {v.dateIndex + 1} of {n}</span></p>
        </div>
        <Strip dates={m.dates} gaps={m.gaps} quality={m.quality} index={v.dateIndex} onPick={pick} />
        {quality && <p className={`quality-note ${quality.quality}`} role="status"><strong>{quality.quality === 'fail' ? 'Faulty pass' : 'Caution'}{quality.channels?.length ? ` (${quality.channels.join(', ')})` : ''}:</strong> {quality.quality_note}{quality.quality === 'fail' ? ' Left out of change detection.' : ''}</p>}
        {note && <p role="status" className="status">{note}</p>}
        {!layer.dates && <p role="status" className="status">“{shortLabel(layer)}” sums up the whole season, so the picture stays the same as you move the timeline{ts?.series?.length ? '; the number still follows the date' : ''}.</p>}
        {preview && <p className="notice"><strong>Preview images</strong>, about 180 m per pixel, uncalibrated: patterns and timing are reliable, absolute values are not.</p>}
      </section>
      )}
    </div>
  )
}
