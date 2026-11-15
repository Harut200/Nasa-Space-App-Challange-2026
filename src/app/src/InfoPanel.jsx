import { useEffect, useRef } from 'react'
import { coordText, dataNotice, dmyDate, eventType, fmtDate, fmtDateLong, fmtKm, headline, isEvent, kindLabel, magOf, tidy, typeInfo } from './data.js'
import { Icon } from './ui.jsx'

const Row = ({ k, children }) => children ? <div className="info-row"><dt className="mono">{k}</dt><dd>{children}</dd></div> : null

// A compact panel about the place on show: kind, where, when, the data and how far to trust it, the headline numbers.
export default function InfoPanel({ place, onClose, onSheet }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.focus() }, [place?.id])
  if (!place) return null
  const ev = isEvent(place)
  const P = place.primary
  const e = place.event
  const km = (b) => { const w = Math.abs(b[2] - b[0]) * 111 * Math.cos(((b[1] + b[3]) / 2) * Math.PI / 180), h = Math.abs(b[3] - b[1]) * 111; return `${fmtKm(w)} × ${fmtKm(h)} km` }
  const h = headline(place)
  const views = (e?.views || []).filter((v) => v.before && v.after)
  const last = place.dates.length - 1
  const water = place.water
  return (
    <aside className="panel info" aria-label={`About ${place.name}`} ref={ref} tabIndex={-1}>
      <div className="row"><h3>{place.name}</h3><button className="icon-btn" onClick={onClose} aria-label="Close the place info">{Icon.close}</button></div>
      <p className="kind-line mono">{kindLabel(place)}{ev && e.magnitude != null && magOf(e) != null ? ` · M ${magOf(e).toFixed(1)}` : ''}</p>
      <dl>
        <Row k="Where">{coordText(place.bounds)} · footprint {km(place.bounds)}</Row>
        {ev ? (
          <>
            <Row k="When">{e.time ? `${typeInfo(eventType(place)).label} on ${dmyDate(e.time)}${e.depth_km != null && Number.isFinite(Number(e.depth_km)) ? `, depth ${Math.round(e.depth_km)} km` : ''}` : null}</Row>
            <Row k="Location">{e.place}</Row>
            <Row k="Before / after">{views.length ? views.map((v) => <span key={v.id} className="info-line">{v.label}, track {v.track}: {fmtDate(String(v.before).slice(0, 10))} → {fmtDate(String(v.after).slice(0, 10))}</span>) : 'one radar pair across the event'}</Row>
            <Row k="Numbers">{(e.views || []).filter((v) => Number.isFinite(v.p2p_cm)).map((v) => <span key={v.id} className="info-line">{v.label}, track {v.track}: ≈ {fmtKm(v.p2p_cm)} cm along the line of sight{Number.isFinite(v.control_p2p_cm) ? ` (same track before: ≈ ${fmtKm(v.control_p2p_cm)} cm)` : ''}</span>)}</Row>
          </>
        ) : (
          <>
            <Row k="Passes">{place.dates.length} radar passes, {fmtDateLong(place.dates[0])} → {fmtDateLong(place.dates[last])}{place.gaps[0] ? `; instrument off ${fmtDate(place.gaps[0][0])} to ${fmtDate(place.gaps[0][1])}` : ''}</Row>
            <Row k="Headline">{h.long}</Row>
            {P.lo && P.hi && water && <Row k="Range at the last pass">{fmtKm(P.lo[last])}–{fmtKm(P.hi[last])} km² (plausible range for {P.label.toLowerCase()})</Row>}
            {place.flood && Math.max(...place.flood.values.filter((v) => v != null), 0) >= 1 && <Row k="Flooded vegetation">up to {fmtKm(Math.max(...place.flood.values.filter((v) => v != null)))} km² possible water under vegetation</Row>}
            {P.kind === 'pct' && <Row k="Coherence">{fmtKm(P.values.find((v) => v != null))} % → {fmtKm(P.values[last])} % ({P.label.toLowerCase()}), first pair to last pair</Row>}
          </>
        )}
        <Row k="Data">{ev ? 'NISAR L2 GUNW interferograms, provisional.' : place.calibrated ? 'Calibrated NISAR GCOV, gamma-naught at 80 m.' : 'NISAR free preview images, about 180 m per pixel.'}</Row>
        <Row k="Calibration">{ev ? 'Provisional; line of sight only, not validated against GNSS.' : dataNotice(place)}</Row>
        <Row k="Credit">{tidy(place.credits)}</Row>
      </dl>
      <button className="linkish" onClick={onSheet}>How sure are we? Open the caveats</button>
    </aside>
  )
}
