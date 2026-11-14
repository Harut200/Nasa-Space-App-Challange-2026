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
  key: I(<><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 10h.1M11 10h.1M15 10h.1M7 14h10" /></>),
}
