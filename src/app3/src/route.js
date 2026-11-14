import { useCallback, useEffect, useState } from 'react'

// #/                                              home (the opening)
// #/globe[?q=<search>]                            the full globe
// #/places[?q=<search>]                           the gallery
// #/<place>?mode=story|explore&scene=N&layer=<id>&date=YYYY-MM-DD[&cmp=1][&from=home|globe|places][&globe=1]
//     globe=1 keeps the place underneath and shows the expanded globe on top of it (the mini globe, grown).
export function parse(hash = location.hash) {
  const h = hash.replace(/^#\/?/, '')
  const [path, q = ''] = h.split('?')
  const p = new URLSearchParams(q)
  let seg = path || ''
  try { seg = decodeURIComponent(seg) } catch { /* keep the raw text */ }
  const search = p.get('q') || ''
  if (!seg) return { place: null, mode: 'open', q: search }
  if (seg === 'globe') return { place: null, mode: 'globe', q: search }
  if (seg === 'places') return { place: null, mode: 'places', q: search }
  const mode = p.get('mode') === 'explore' ? 'explore' : 'story'
  const from = ['home', 'globe', 'places'].includes(p.get('from')) ? p.get('from') : 'home'
  return { place: seg, mode, scene: Math.max(1, parseInt(p.get('scene') || '1', 10) || 1), layer: p.get('layer') || null, date: p.get('date') || null, cmp: p.get('cmp') === '1', from, globe: p.get('globe') === '1', q: search }
}
export function build(r) {
  const qs = r.q ? `?${new URLSearchParams({ q: r.q })}` : ''
  if (r.mode === 'globe') return `#/globe${qs}`
  if (r.mode === 'places') return `#/places${qs}`
  if (r.mode === 'open' || !r.place) return '#/'
  const p = new URLSearchParams({ mode: r.mode })
  if (r.mode === 'story') p.set('scene', String(r.scene || 1))
  if (r.layer) p.set('layer', r.layer)
  if (r.date) p.set('date', r.date)
  if (r.mode === 'explore' && r.cmp) p.set('cmp', '1')
  if (r.from && r.from !== 'home') p.set('from', r.from)
  if (r.globe) p.set('globe', '1')
  return `#/${r.place}?${p}`
}

export function useRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const on = () => setRoute(parse())
    window.addEventListener('hashchange', on)
    window.addEventListener('popstate', on)
    return () => { window.removeEventListener('hashchange', on); window.removeEventListener('popstate', on) }
  }, [])
  // push: new history entry (view, place or mode changes); otherwise replace (scrubbing, scenes, typing)
  const go = useCallback((patch, push = true, state = null) => {
    const next = { ...parse(), ...patch }
    const url = build(next)
    if (url !== location.hash) {
      if (push) history.pushState({ ...state, app: (history.state?.app || 0) + 1 }, '', url)
      else history.replaceState(history.state, '', url)
    }
    setRoute(next)
  }, [])
  return [route, go]
}
