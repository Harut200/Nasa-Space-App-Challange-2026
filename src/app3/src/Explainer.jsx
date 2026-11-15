import { tidy } from './data.js'
import { Stat } from './Story.jsx'
import EvidenceChart from './EvidenceChart.jsx'
import { Icon, useStepMotion } from './ui.jsx'

// "How we found it": a short stepper, one step per piece of evidence; each step drives the map (layer, compare, date) from App.
export default function Explainer({ place, steps, n, index, onStep, onClose, onIndex, emptyPass }) {
  const s = steps[n - 1]
  const last = n === steps.length
  const { leaving, mv } = useStepMotion(s.key, { n, title: s.title, text: s.text, tone: s.tone }, place.id)
  return (
    <section className="panel story how" aria-label="How we found it" aria-live="polite">
      <div className="how-head">
        <div className="kicker mono"><span>How we found it</span><span>{n} / {steps.length}</span></div>
        <button className="icon-btn" onClick={onClose} aria-label="Close: back to the place" title="Close ( E )">{Icon.close}</button>
      </div>
      <div className={`story-in how-in ${mv.fast ? 'fast' : ''}`} style={{ '--dir': mv.dir }}>
        {leaving && (
          <div className="swap leaving" key={`leave-${leaving.n}`} aria-hidden="true">
            <h2>{leaving.title}</h2>
            <p className={`body ${leaving.tone === 'caution' ? 'caution' : ''}`}>{tidy(leaving.text)}</p>
          </div>
        )}
        <div className="swap head" key={`h-${s.key}`}><h2>{s.title}</h2></div>
        {s.stat && <Stat key={`s-${s.key}`} s={s.stat} />}
        <div className="swap rest" key={`r-${s.key}`}>
          <p className={`body ${s.tone === 'caution' ? 'caution' : ''}`}>{tidy(s.text)}</p>
          {s.chart && <EvidenceChart place={place} index={index} onScrub={onIndex} compact />}
          {emptyPass && <p className="hint mono">No open water on this pass.</p>}
        </div>
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
