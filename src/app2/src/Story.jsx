import { useRef, useState } from 'react'
import { fmtDate, isEvent } from './data.js'
import { EventStrip } from './EventParts.jsx'
import { Icon, Num } from './ui.jsx'

function Stat({ s }) {
  const num = typeof s.big === 'number'
  return (
    <div className="stat">
      <div className="statbig">
        {s.sign && <span className="sign">{s.sign}</span>}
        {num ? (s.dec ? s.big.toFixed(s.dec) : <Num value={s.big} />) : s.big}
        {s.unit && <small>{s.unit}</small>}
      </div>
      <div className="stat-label">{s.label}</div>
      {s.sub && <div className="stat-sub mono">{s.sub}</div>}
    </div>
  )
}

export default function Story({ place, scenes, n, index, playing, setPlaying, onScene, onIndex, onSheet, onExplore, missing }) {
  const sc = scenes[n - 1]
  const [open, setOpen] = useState(null)
  const touch = useRef(null)
  const stat = sc.stat(index)
  const expanded = open === sc.key
  const last = n === scenes.length
  const ev = isEvent(place)
  const q = ev ? null : place.quality[place.dates[index]]
  return (
    <section className="panel story" aria-label="Story" aria-live="polite"
      onTouchStart={(e) => { touch.current = e.touches[0].clientX }}
      onTouchEnd={(e) => { const dx = e.changedTouches[0].clientX - (touch.current ?? 0); if (Math.abs(dx) > 70) onScene(n + (dx < 0 ? 1 : -1)) }}>
      <div className="story-in" key={sc.key}>
        <div className="kicker mono"><span>{sc.kicker}</span><span>{n} / {scenes.length}</span></div>
        <h2>{sc.title}</h2>
        <Stat s={stat} />
        <p className="body">{expanded ? sc.full : sc.text}</p>
        {sc.full && sc.full !== sc.text && <button className="linkish" onClick={() => setOpen(expanded ? null : sc.key)} aria-expanded={expanded}>{expanded ? 'Show less' : 'Read the full passage'}</button>}
        {ev && sc.strip && <EventStrip event={place.event} />}
        {sc.note && <p className="hint mono">{sc.note}</p>}
        {ev && sc.hint && <p className="hint mono">{sc.hint}</p>}
        {!ev && sc.compare && <p className="hint mono">Drag the handle on the map: first pass on the left, {fmtDate(place.dates[index])} on the right.</p>}
        {!ev && sc.focus && <p className="hint mono">Zoomed to {place.spots.find((s) => s.id === sc.focus)?.label}, the spot where radar brightness changed most.</p>}
        {missing && <p className="hint mono">No image for this pass in this layer.</p>}
      </div>
      {!ev && <div className="beats-wrap">
        <div className="beats" role="group" aria-label="Radar passes">
          <button className="play sm" onClick={() => { if (!playing && index >= place.dates.length - 1) onIndex(0); setPlaying(!playing) }} aria-label={playing ? 'Pause' : 'Play the passes'}>{playing ? Icon.pause : Icon.play}</button>
          {place.dates.map((d, i) => (
            <button key={d} className={`beat ${i === index ? 'on' : ''} ${place.quality[d] ? 'q' : ''}`} onClick={() => onIndex(i)} aria-label={`Pass ${i + 1}, ${fmtDate(d)}`} aria-current={i === index} title={fmtDate(d)}><i /></button>
          ))}
          <span className="mono beat-date">{fmtDate(place.dates[index])}</span>
        </div>
        {q && <p className={`pill q-${q.quality}`} role="status">{q.quality === 'fail' ? 'Flagged pass' : 'Caution'}: {q.quality_note.split(';')[0]}</p>}
      </div>}
      <div className="story-nav">
        <div className="dots" role="tablist" aria-label="Scenes">
          {scenes.map((s, i) => <button key={s.key} role="tab" aria-selected={i + 1 === n} aria-label={`Scene ${i + 1}: ${s.title}`} className={i + 1 === n ? 'on' : ''} onClick={() => onScene(i + 1)} />)}
        </div>
        <div className="pair">
          {sc.sheet && <button className="btn ghost" onClick={onSheet}>Open the caveats</button>}
          {last ? <button className="btn primary" onClick={onExplore}>Explore <span aria-hidden>→</span></button> : (
            <>
              <button className="btn ghost sq" onClick={() => onScene(n - 1)} disabled={n === 1} aria-label="Previous scene">{Icon.prev}</button>
              <button className="btn primary" onClick={() => onScene(n + 1)} aria-label="Next scene">Next <span aria-hidden>→</span></button>
            </>
          )}
        </div>
      </div>
    </section>
  )
}
