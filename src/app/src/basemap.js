// Two looks for the map under our radar data.
//   natural: NASA Blue Marble (NASA GIBS) with Sentinel-2 cloudless (EOX) fading in as you zoom. Real imagery, no API key.
//   dark:    plain dark globe with bundled coastlines plus OpenFreeMap country lines (the earlier look).
// Neither request carries any identifier of ours.
const GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf'
const FONT = ['Noto Sans Regular']

export const GIBS_ATTR = 'Imagery: NASA GIBS, Blue Marble (NASA Earth Observatory)'
export const EOX_ATTR = 'Sentinel-2 cloudless by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)'
const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg'
const EOX = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg'
export const IMAGERY_SOURCES = ['bm', 's2']

function graticule() {
  const f = []
  for (let lng = -180; lng < 180; lng += 30) f.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: Array.from({ length: 37 }, (_, i) => [lng, -90 + i * 5]) } })
  for (let lat = -60; lat <= 60; lat += 30) f.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: Array.from({ length: 73 }, (_, i) => [-180 + i * 5, lat]) } })
  return { type: 'FeatureCollection', features: f }
}
const label = (id, filter, size, minzoom = 0) => ({
  id, type: 'symbol', source: 'ofm', 'source-layer': 'place', minzoom, filter,
  layout: { 'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']], 'text-font': FONT, 'text-size': size, 'text-transform': 'uppercase', 'text-letter-spacing': 0.12 },
  paint: { 'text-color': '#8b8b8b', 'text-halo-color': '#000', 'text-halo-width': 1 },
})

export function styleFor(mode) {
  if (mode === 'natural') {
    return {
      version: 8, projection: { type: 'globe' },
      sky: { 'sky-color': '#02050a', 'horizon-color': '#4f8fd6', 'fog-color': '#02050a', 'sky-horizon-blend': 0.7, 'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.8, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 5, 0.5, 8, 0] },
      sources: {
        bm: { type: 'raster', tiles: [GIBS], tileSize: 256, maxzoom: 8, attribution: GIBS_ATTR },
        s2: { type: 'raster', tiles: [EOX], tileSize: 256, maxzoom: 13, attribution: EOX_ATTR },
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': '#0a1018' } },
        { id: 'bm', type: 'raster', source: 'bm', paint: { 'raster-fade-duration': 300 } },
        { id: 's2', type: 'raster', source: 's2', minzoom: 4, paint: { 'raster-fade-duration': 300, 'raster-opacity': ['interpolate', ['linear'], ['zoom'], 4, 0, 6, 1] } },
      ],
    }
  }
  return {
    version: 8, glyphs: GLYPHS, projection: { type: 'globe' },
    sky: { 'sky-color': '#000', 'horizon-color': '#1b2a3a', 'fog-color': '#000', 'sky-horizon-blend': 0.6, 'horizon-fog-blend': 0.5, 'fog-ground-blend': 0.9, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.9, 5, 0.4, 7, 0] },
    sources: {
      ofm: { type: 'vector', url: 'https://tiles.openfreemap.org/planet', attribution: '© OpenStreetMap contributors, OpenFreeMap' },
      grat: { type: 'geojson', data: graticule() },
      land: { type: 'geojson', data: new URL(import.meta.env.BASE_URL + 'world-land.json', location.href).href }, // bundled coastline: continents show without the online tiles
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#0c141c' } },
      { id: 'grat', type: 'line', source: 'grat', paint: { 'line-color': '#1d2c3a', 'line-width': 0.6 } },
      { id: 'land0', type: 'fill', source: 'land', paint: { 'fill-color': '#27302f' } },
      { id: 'land0-line', type: 'line', source: 'land', paint: { 'line-color': '#55646a', 'line-width': 0.7 } },
      { id: 'landcover', source: 'ofm', 'source-layer': 'landcover', type: 'fill', paint: { 'fill-color': '#232a2e' } },
      { id: 'water', source: 'ofm', 'source-layer': 'water', type: 'fill', paint: { 'fill-color': '#05090d' } },
      { id: 'coast', source: 'ofm', 'source-layer': 'water', type: 'line', paint: { 'line-color': '#4a5560', 'line-width': 0.6, 'line-opacity': 0.7 } },
      { id: 'border', source: 'ofm', 'source-layer': 'boundary', type: 'line', filter: ['==', ['get', 'admin_level'], 2], paint: { 'line-color': '#5a5a5a', 'line-width': 0.5, 'line-dasharray': [2, 2] } },
      label('country', ['==', ['get', 'class'], 'country'], 11),
      label('city', ['in', ['get', 'class'], ['literal', ['city']]], 10, 4),
    ],
  }
}
