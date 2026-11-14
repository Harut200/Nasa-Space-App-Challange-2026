import { useEffect, useRef, useState } from 'react'
import { fmtKm } from './data.js'

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// Smoothly counts toward a new value (instant with reduced motion).
export function useCountUp(target, ms = 650) {
  const [v, setV] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target == null || reducedMotion()) { setV(target); from.current = target; return }
    const a = from.current ?? target, t0 = performance.now()
    let raf
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3)
      const cur = a + (target - a) * e
      from.current = cur; setV(cur)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

export const Num = ({ value }) => { const v = useCountUp(value); return <>{fmtKm(v)}</> }

// A small, dismissible hint for the keyboard; remembered for the visit when storage allows.
export function KeyHint({ text }) {
  const [gone, setGone] = useState(() => { try { return sessionStorage.getItem('keyhint') === '1' } catch { return false } })
  if (gone) return null
  return (
    <div className="keys-hint">
      <span>{text}</span>
      <button onClick={() => { setGone(true); try { sessionStorage.setItem('keyhint', '1') } catch { /* private window */ } }} aria-label="Hide the keyboard hint">×</button>
    </div>
  )
}

const I = (d, p = {}) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p}>{d}</svg>
export const Icon = {
  play: I(<path d="M7 4.5v15l12-7.5z" fill="currentColor" />),
  pause: I(<><rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor" /><rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor" /></>),
  prev: I(<path d="M15 5l-7 7 7 7" />),
  next: I(<path d="M9 5l7 7-7 7" />),
  info: I(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.6v.1" /></>),
  grid: I(<><rect x="4" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" /></>),
  split: I(<><rect x="3.5" y="5" width="17" height="14" rx="2" /><path d="M12 5v14" /></>),
  pin: I(<><path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" /></>),
  close: I(<path d="M6 6l12 12M18 6L6 18" />),
  search: I(<><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></>),
  globe: I(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" /></>),
  dice: I(<><rect x="4" y="4" width="16" height="16" rx="3.5" /><path d="M8.5 8.5h.1M15.5 8.5h.1M12 12h.1M8.5 15.5h.1M15.5 15.5h.1" strokeWidth="2.6" /></>),
  home: I(<><path d="M4 11l8-7 8 7" /><path d="M6 10v9h12v-9" /></>),
  more: I(<path d="M6 12h.1M12 12h.1M18 12h.1" strokeWidth="3" />),
  locate: I(<><circle cx="12" cy="12" r="3.5" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></>),
  list: I(<><rect x="4" y="4" width="16" height="16" rx="2.5" /><path d="M8 9h8M8 12.5h8M8 16h5" /></>),
  about: I(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.6 2.6 0 1 1 3.6 2.4c-.8.4-1.1.9-1.1 1.8M12 16.6v.1" /></>),
  layers: I(<><path d="M12 3.5 3.5 8 12 12.5 20.5 8z" /><path d="M3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16" opacity=".6" /></>),
  key: I(<><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 10h.1M11 10h.1M15 10h.1M7 14h10" /></>),
}
