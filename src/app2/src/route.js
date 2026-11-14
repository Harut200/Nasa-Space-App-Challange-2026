import { useCallback, useEffect, useRef, useState } from 'react'

// #/<place>?mode=story|explore&scene=N&layer=<id>&date=YYYY-MM-DD[&cmp=1]     #/  (opening)     #/places  (gallery)
export function parse(hash = location.hash) {
  const h = hash.replace(/^#\/?/, '')
  const [path, q = ''] = h.split('?')
  const p = new URLSearchParams(q)
  const seg = decodeURIComponent(path || '')
  if (!seg) return { place: null, mode: 'open' }
  if (seg === 'places') return { place: null, mode: 'places' }
  const mode = p.get('mode') === 'explore' ? 'explore' : 'story'
  return { place: seg, mode, scene: Math.max(1, parseInt(p.get('scene') || '1', 10) || 1), layer: p.get('layer') || null, date: p.get('date') || null, cmp: p.get('cmp') === '1' }
}
export function build(r) {
  if (r.mode === 'open' || (!r.place && r.mode !== 'places')) return '#/'
  if (r.mode === 'places') return '#/places'
  const p = new URLSearchParams({ mode: r.mode })
  if (r.mode === 'story') p.set('scene', String(r.scene || 1))
  if (r.layer) p.set('layer', r.layer)
  if (r.date) p.set('date', r.date)
  if (r.mode === 'explore' && r.cmp) p.set('cmp', '1')
  return `#/${r.place}?${p}`
}

export function useRoute() {
  const [route, setRoute] = useState(parse)
  const seen = useRef(location.hash) // the hash this hook last knew; a patch computed for an older route must not overwrite a newer navigation
  useEffect(() => {
    const on = () => { seen.current = location.hash; setRoute(parse()) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  // push: new history entry (place or mode changes); otherwise replace (scrubbing, scenes)
  const go = useCallback((patch, push = true) => {
    setRoute((cur) => {
      if (location.hash !== seen.current && !push) return cur // the address moved on (back, a typed link): drop this late patch
      const next = { ...cur, ...patch }
      const url = build(next)
      if (url !== location.hash) { push ? history.pushState(null, '', url) : history.replaceState(null, '', url) }
      seen.current = location.hash
      return next
    })
  }, [])
  return [route, go]
}
