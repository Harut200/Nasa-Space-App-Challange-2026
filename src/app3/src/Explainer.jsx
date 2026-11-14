import { tidy } from './data.js'
import { Stat } from './Story.jsx'
import EvidenceChart from './EvidenceChart.jsx'
import { Icon } from './ui.jsx'

// "How we found it": a short stepper, one step per piece of evidence; each step drives the map (layer, compare, date) from App.
export default function Explainer({ place, steps, n, index, onStep, onClose, onIndex }) {
  const s = steps[n - 1]
  const last = n === steps.length
  return (
    <section className="panel story how" aria-label="How we found it" aria-live="polite">
      <div className="how-head">
        <div className="kicker mono"><span>How we found it</span><span>{n} / {steps.length}</span></div>
        <button className="icon-btn" onClick={onClose} aria-label="Close: back to the place" title="Close ( E )">{Icon.close}</button>
      </div>
      <div className="story-in how-in" key={s.key}>
        <h2>{s.title}</h2>
        {s.stat && <Stat s={s.stat} />}
        <p className={`body ${s.tone === 'caution' ? 'caution' : ''}`}>{tidy(s.text)}</p>
        {s.chart && <EvidenceChart place={place} index={index} onScrub={onIndex} compact />}
      </div>
      <div className="story-nav">
        <div className="dots" role="tablist" aria-label="Steps" style={{ '--n': n - 1 }}>
          <i className="dot-ind" aria-hidden="true" />
          {steps.map((x, i) => <button key={x.key} role="tab" aria-selected={i + 1 === n} aria-label={`Step ${i + 1}: ${x.title}`} className={i + 1 === n ? 'on' : ''} onClick={() => onStep(i + 1)} />)}
        </div>
        <div className="pair">
          <button className="btn ghost sq" onClick={() => onStep(n - 1)} disabled={n === 1} aria-label="Previous step">{Icon.prev}</button>
          {last ? <button className="btn primary" onClick={onClose}>Done</button> : <button className="btn primary" onClick={() => onStep(n + 1)} aria-label="Next step">Next <span aria-hidden>→</span></button>}
        </div>
      </div>
    </section>
  )
}
