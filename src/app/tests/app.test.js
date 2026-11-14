import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createElement as h } from 'react'
import { renderToStaticMarkup as html } from 'react-dom/server'
import { createServer } from 'vite'

const root = path.resolve(import.meta.dirname, '..')
const pub = path.join(root, 'public/data')
const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
after(() => vite.close())
const { frame, nearest } = await vite.ssrLoadModule('/src/data.js')
const meta = await vite.ssrLoadModule('/src/meta.js')
const { Fact } = await vite.ssrLoadModule('/src/Panels.jsx')
const { Strip, Chart } = await vite.ssrLoadModule('/src/Charts.jsx')
const count = (s, re) => (s.match(re) || []).length
const noop = () => {}

test('frame: contract C paths and nearest date', () => {
  const dated = { id: 'hh', dates: ['2026-06-20', '2026-07-02', '2026-07-14'] }
  assert.deepEqual(frame('data/', 'a', dated, '2026-07-02'), { url: 'data/a/hh/2026-07-02.webp', shown: '2026-07-02' })
  assert.deepEqual(frame('data/', 'a', { id: 'rgb', dates: null }, '2026-07-02'), { url: 'data/a/rgb.webp', shown: null })
  assert.equal(nearest(dated.dates, '2026-07-10'), '2026-07-14')
})

test('link format: #/<place>?layer=&date=', () => {
  assert.deepEqual(meta.parseHash('#/tonle-sap?layer=water&date=2026-10-17'), { id: 'tonle-sap', layer: 'water', date: '2026-10-17', about: false, compare: false, split: 50 })
  assert.deepEqual(meta.parseHash(''), { id: null, layer: null, date: null, about: false, compare: false, split: 50 })
  assert.equal(meta.parseHash('#/sevan?about').about, true)
})

test('layers: the four clearest come first, nothing is dropped', () => {
  const layers = ['hv', 'sig', 'hh', 'classes', 'change', 'when', 'water'].map((id) => ({ id, label: id }))
  const { main, more } = meta.splitLayers(layers)
  assert.deepEqual(main.map((l) => l.id), ['water', 'when', 'change', 'hh'])
  assert.equal(main.length + more.length, layers.length)
})

test('nearest place and peak water come from data', () => {
  const places = [{ id: 'a', lngLat: [104, 12] }, { id: 'b', lngLat: [45, 40] }]
  assert.equal(meta.nearestPlace(places, [44, 41]).id, 'b')
  const peak = meta.peakWater({ dates: ['d1', 'd2'], facts: [{ label: 'Open water', unit: 'km²', values: [1, 5] }] })
  assert.deepEqual(peak, { v: 5, date: 'd2', unit: 'km²' })
  assert.equal(meta.peakWater({ dates: [], facts: [] }), null)
})

test('Strip and Chart render with gaps, nulls and one date', () => {
  const s = html(h(Strip, { dates: ['2026-06-20', '2026-07-02', '2026-08-10'], gaps: [['2026-07-10', '2026-08-01']], index: 1, onPick: noop }))
  assert.ok(s.includes('Outage') && !s.includes('NaN'))
  const ts = { dates: ['2026-06-20', '2026-07-02', '2026-07-14'], series: [{ spot: 'lake', hh: [-18, null, -20] }] }
  const c = html(h(Chart, { ts, pol: 'hh', gaps: [], index: 0, onPick: noop, colors: ['#f00'], spots: [{ id: 'lake', label: 'Open lake' }] }))
  assert.equal(count(c, /<circle/g), 2)
  assert.ok(c.includes('Open lake') && !c.includes('NaN'))
})

test('published data is complete and has no demo place', () => {
  const idx = JSON.parse(fs.readFileSync(path.join(pub, 'index.json'), 'utf8'))
  assert.equal(idx.areas[0], 'tonle-sap')
  assert.ok(!idx.areas.includes('demo') && !fs.existsSync(path.join(pub, 'demo')))
  for (const a of idx.areas) {
    const m = JSON.parse(fs.readFileSync(path.join(pub, a, 'manifest.json'), 'utf8'))
    assert.ok(Array.isArray(m.bounds) && m.bounds.length === 4)
    for (const l of m.layers) for (const d of m.dates) {
      assert.ok(fs.existsSync(path.join(root, 'public', frame('data/', a, l, d).url)), `${a} ${l.id} ${d}`)
    }
  }
})

test('no invented events, planets or demo data in the app source', () => {
  assert.ok(!fs.existsSync(path.join(root, 'src/regions.js')) && !fs.existsSync(path.join(root, 'src/planets.js')))
  const re = /data\/demo|Demo \(synthetic\)|PLANETS|predicted event/
  const bad = fs.readdirSync(path.join(root, 'src')).filter((f) => /\.(jsx?|css)$/.test(f) && re.test(fs.readFileSync(path.join(root, 'src', f), 'utf8')))
  assert.deepEqual(bad, [])
})

// ---- earthquake event places ----
const readManifest = (a) => JSON.parse(fs.readFileSync(path.join(pub, a, 'manifest.json'), 'utf8'))
const idxAreas = JSON.parse(fs.readFileSync(path.join(pub, 'index.json'), 'utf8')).areas
const manifests = idxAreas.map(readManifest)
const events = manifests.filter(meta.isEvent)
const places = manifests.map((m) => meta.makePlace(m, null))
const BAD = /NaN|undefined|null|Infinity/

test('event places: data shape, layers on disk, helpers survive dates: []', () => {
  assert.ok(events.length >= 4)
  for (const m of events) {
    assert.deepEqual(m.dates, [])
    assert.equal(meta.defaultLayer(m), 'fringes')
    for (const l of m.layers) assert.ok(fs.existsSync(path.join(root, 'public', frame('data/', m.id, l, undefined).url)), `${m.id} ${l.id}`)
    assert.equal(meta.splitLayers(m.layers, true).main.length, m.layers.length)
    assert.ok(meta.eventViews(m.event).length >= 1)
  }
  assert.equal(meta.peakWater({ dates: [], series: [], facts: [] }), null)
})

test('event places: tooltip text, grouping, counts, summaries', () => {
  const tibet = places.find((p) => p.id === 'tibet-m70-2026')
  assert.equal(tibet.kind, 'event')
  const { head, rest } = meta.eventTip(tibet)
  assert.equal(`${head} · ${rest}`, "Tibet earthquake, M7.0 · 22 Oct 2026 · ground moved ≈ 127 cm along the radar's line of sight")
  const noNum = { ...tibet, event: { ...tibet.event, views: [{ ...tibet.event.views[0], p2p_cm: null }] } }
  assert.equal(meta.eventTip(noNum).rest, '22 Oct 2026')
  for (const p of places) {
    const t = html(h(Fact, { p })) + meta.placeSummary(p)
    assert.ok(!BAD.test(t), `${p.id}: ${t}`)
  }
  const g = meta.groupPlaces(places)
  assert.deepEqual(g.map((x) => x.title), ['A season of water', 'Earthquakes'])
  assert.equal(g[0].items.length + g[1].items.length, places.length)
  assert.ok(g[1].items.every((p) => p.kind === 'event'))
  assert.deepEqual(meta.groupPlaces(places.filter((p) => p.kind === 'event')).map((x) => x.key), ['event'])
  const { seasons, quakes } = meta.countPlaces(places)
  assert.equal(quakes, events.length)
  assert.equal(meta.landingText(places), `NISAR watched ${seasons} places through a season, and ${quakes} earthquakes between June and October 2026.`)
  assert.equal(meta.landingText(places.slice(0, 3)), 'NISAR watched 3 places through a season between June and October 2026.')
  assert.equal(meta.eventSummary(tibet), 'M7.0 · 22 Oct 2026')
})

test('event places: before/after and honesty text, no NaN', () => {
  for (const m of events) {
    const e = m.event
    const txt = [meta.viewingNote(e), meta.utcStamp(e.time), ...meta.eventViews(e).flatMap((v) => [meta.dayMon(v.before), meta.dayMon(v.after), meta.viewMotion(v), meta.viewControl(v)])].filter(Boolean).join(' ')
    assert.ok(!BAD.test(txt), txt)
  }
  const t = readManifest('tibet-m70-2026').event
  assert.equal(meta.dayMon(t.views[0].before), '19 Oct')
  assert.match(meta.viewingNote(t), /one viewing direction/)
  assert.match(meta.viewingNote({ views: [{ label: 'ascending' }, { label: 'descending' }] }), /two viewing directions/)
  assert.equal(meta.viewMotion({ p2p_cm: null }), null)
})

test('event places: dot size grows with magnitude; link with a date param parses', () => {
  assert.ok(meta.quakeRadius(7.7) > meta.quakeRadius(6.8))
  assert.ok(Number.isFinite(meta.quakeRadius(undefined)))
  assert.equal(meta.parseHash('#/tibet-m70-2026?layer=fringes&date=2026-10-19').id, 'tibet-m70-2026')
})

// ---- polish pass ----
test('viewing note: same-direction passes are written plainly', () => {
  const same = meta.viewingNote({ views: [{ label: 'ascending' }, { label: 'ascending' }] })
  assert.equal(same, "Provisional data, two passes of the same viewing direction: this is motion along the radar's line of sight, not up/down or sideways.")
  assert.match(meta.viewingNote({ views: [{ label: 'ascending' }, { label: 'ascending' }, { label: 'ascending' }] }), /three passes of the same viewing direction/)
  assert.match(meta.viewingNote({ views: [{ label: 'ascending' }, { label: 'descending' }] }), /two viewing directions/)
  assert.doesNotMatch(same, /tracks, all/)
})

test('link: compare and split survive a reload, other date is optional', () => {
  const h = meta.parseHash('#/tonle-sap?layer=water&date=2026-10-17&compare=1&split=35&other=2026-07-02')
  assert.equal(h.compare, true); assert.equal(h.split, 35); assert.equal(h.other, '2026-07-02')
  assert.ok(!('other' in meta.parseHash('#/tonle-sap?compare')))
})

test('layer labels: known ids get a short name, unknown ids fall back to the manifest label, cut at a word', () => {
  assert.equal(meta.shortLabel({ id: 'water', label: 'x' }), 'Water and flooded vegetation')
  assert.equal(meta.shortLabel({ id: 'coh', label: 'Coherence' }), 'Coherence')
  const t = meta.shortLabel({ id: 'coh_change', label: 'Coherence change since the first pass of the season (unitless)' })
  assert.ok(t.endsWith('…') && t.length <= 31 && !/\s…$/.test(t), t)
  assert.equal(meta.shortLabel({ id: 'zz' }), 'zz')
})

test('numbers: percent, dB and nulls display cleanly', () => {
  assert.equal(meta.withUnit(63.44, '%'), '63.4%')
  assert.equal(meta.withUnit(-18.26, 'dB'), '−18.3 dB')
  assert.equal(meta.withUnit(1234.4, 'km²'), '1,234 km²')
  assert.equal(meta.fmtVal(null, '%'), '')
})

test('calibrated place (dB, negative values, axis not from 0) and a % fact with a null first value render without NaN', () => {
  const ts = { dates: ['2026-08-12', '2026-08-24', '2026-09-05'], unit: 'dB', series: [{ spot: 'lake', hh: [-21.4, -20.9, -22.1], hv: [-27, -26.5, null] }, { spot: 'rice', hh: [-12.2, -11.8, -9.5] }] }
  for (const pol of ['hh', 'hv', 'vv']) {
    const c = html(h(Chart, { ts, pol, gaps: [], index: 1, onPick: noop, colors: ['#f00', '#00f'], spots: [{ id: 'lake', label: 'Lake' }, { id: 'rice', label: 'Rice' }] }))
    assert.ok(!BAD.test(c.replace(/null/g, '')) && c.includes('−'), pol)
  }
  const one = { dates: ['2026-08-12'], series: [{ spot: 'lake', hh: [-20] }] }
  assert.ok(html(h(Chart, { ts: one, pol: 'hh', gaps: [], index: 0, onPick: noop, colors: ['#f00'], spots: [] })).includes('−20'))
  const noData = { dates: ['2026-08-12'], series: [{ spot: 'lake', hh: [null] }] }
  assert.equal(html(h(Chart, { ts: noData, pol: 'hh', gaps: [], index: 0, onPick: noop, colors: ['#f00'], spots: [] })), '')
  const peak = meta.peakWater({ dates: ['a', 'b'], facts: [{ label: 'Open water', unit: '%', values: [null, 12.5] }] })
  assert.deepEqual(peak, { v: 12.5, date: 'b', unit: '%' })
})
