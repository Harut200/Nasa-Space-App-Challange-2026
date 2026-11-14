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
