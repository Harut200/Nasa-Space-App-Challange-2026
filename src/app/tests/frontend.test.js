import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createElement as h } from 'react'
import { renderToStaticMarkup as html } from 'react-dom/server'
import { createServer } from 'vite'
import { frame, nearest } from '../src/data.js'

const root = path.resolve(import.meta.dirname, '..')
const pub = path.join(root, 'public/data')
const B = 'data/'
const dated = { id: 'hh', dates: ['2026-06-20', '2026-07-02', '2026-07-14'] }
const change = { id: 'change', dates: ['2026-07-02', '2026-07-14'] }
const rgb = { id: 'rgb', dates: null }

test('frame: contract C paths', () => {
  assert.deepEqual(frame(B, 'a', dated, '2026-07-02'), { url: 'data/a/hh/2026-07-02.webp', shown: '2026-07-02' })
  assert.deepEqual(frame(B, 'a', change, '2026-06-20'), { url: 'data/a/change/2026-07-02.webp', shown: '2026-07-02' })
  assert.deepEqual(frame(B, 'a', rgb, '2026-07-02'), { url: 'data/a/rgb.webp', shown: null })
})

test('nearest picks closest date', () => {
  assert.equal(nearest(dated.dates, '2026-07-10'), '2026-07-14')
  assert.equal(nearest(dated.dates, '2026-06-25'), '2026-06-20')
})

const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
after(() => vite.close())
const { Strip, Chart } = await vite.ssrLoadModule('/src/Charts.jsx')
const count = (s, re) => (s.match(re) || []).length
const colors = ['#f00', '#0f0']
const spots = [{ id: 'lake', label: 'Open lake' }]
const noop = () => {}

test('Strip renders gap, many dates and a single date', () => {
  const d = ['2026-06-20', '2026-07-02', '2026-08-10']
  const s = html(h(Strip, { dates: d, gaps: [['2026-07-10', '2026-08-01']], index: 1, onPick: noop }))
  assert.equal(count(s, /<title>/g), 3)
  assert.ok(s.includes('no data (instrument)'))
  assert.ok(!s.includes('NaN'))
  const one = html(h(Strip, { dates: ['2026-06-20'], gaps: [], index: 0, onPick: noop }))
  assert.ok(!one.includes('NaN') && count(one, /<title>/g) === 1)
})

test('Chart: nulls break the line, gap, unknown spot, single date', () => {
  const ts = {
    dates: ['2026-06-20', '2026-07-02', '2026-07-14', '2026-08-10', '2026-08-22'],
    series: [
      { spot: 'lake', hh: [-18, null, -20, -21, null], hv: [-27, -28, -29, null, -30] },
      { spot: 'ghost', hh: [-10, -11, -12, -13, -14], hv: [] },
    ],
  }
  const s = html(h(Chart, { ts, pol: 'hh', gaps: [['2026-07-27', '2026-08-10']], index: 0, onPick: noop, colors, spots }))
  assert.equal(count(s, /<circle/g), 3 + 5)
  assert.equal(count(s, /<polyline/g), 3 + 1) // lake: runs [a] [c,d] [] -> 3 elements (one empty), ghost: 1
  assert.ok(s.includes('Open lake') && s.includes('ghost'))
  assert.ok(!s.includes('NaN') && !s.includes('undefined'))
  const one = html(h(Chart, { ts: { dates: ['2026-06-20'], series: [{ spot: 'lake', hh: [-18] }] }, pol: 'hh', gaps: [], index: 0, onPick: noop, colors, spots }))
  assert.equal(count(one, /<circle/g), 1)
  assert.ok(!one.includes('NaN'))
})

test('TimelineChart: water curve, range band, nulls and no data', async () => {
  const { default: TimelineChart } = await vite.ssrLoadModule('/src/TimelineChart.jsx')
  const dates = ['2026-06-20', '2026-07-02', '2026-08-10']
  const facts = [{ label: 'Open water', unit: 'km²', values: [10, null, 30], lo: [8, null, 25], hi: [12, null, 36] }, { label: 'Flooded vegetation', unit: 'km²', values: [0, 2, 4] }]
  const s = html(h(TimelineChart, { dates, facts, gaps: [['2026-07-10', '2026-08-01']], index: 2, onIndex: noop }))
  assert.equal(count(s, /<circle/g), 2) // the null pass has no dot
  assert.ok(s.includes('pt-tl-band') && s.includes('pt-tl-fv') && s.includes('No passes'))
  assert.ok(!s.includes('NaN') && !s.includes('undefined'))
  assert.ok(!html(h(TimelineChart, { dates: [], facts: [], gaps: [], index: 0 })).includes('NaN'))
})

test('published data is clean and complete', () => {
  const idx = JSON.parse(fs.readFileSync(path.join(pub, 'index.json'), 'utf8'))
  assert.ok(Array.isArray(idx.areas))
  assert.ok(!JSON.stringify(idx).includes('demo'))
  assert.ok(!fs.existsSync(path.join(pub, 'demo')))
  for (const a of idx.areas) {
    const m = JSON.parse(fs.readFileSync(path.join(pub, a, 'manifest.json'), 'utf8'))
    for (const l of m.layers) for (const d of m.dates) {
      const { url } = frame(B, a, l, d)
      assert.ok(fs.existsSync(path.join(root, 'public', url)), url)
    }
  }
})

test('no demo references in app source', () => {
  const files = [path.join(root, 'index.html')]
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : files.push(path.join(d, e.name))))
  walk(path.join(root, 'src'))
  const re = /data\/demo|'demo'|"demo"|Demo \(synthetic\)/
  assert.deepEqual(files.filter((f) => re.test(fs.readFileSync(f, 'utf8'))), [])
})
