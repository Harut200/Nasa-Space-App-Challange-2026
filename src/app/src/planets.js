// Planet registry. Non-Earth imagery = OpenPlanetaryMap web-mercator XYZ tiles (CORS-open, verified with curl).
// `lo` = whole planet as one bundled 512 px image (the four zoom-1 tiles stitched): paints at once, with no network.
const OPM = 'https://cartocdn-gusc.global.ssl.fastly.net/opmbuilder/api/v1/map/named'
const opm = (n) => `${OPM}/opm-${n}-basemap-v0-1/all/{z}/{x}/{y}.png`
const lo = (n) => `${location.origin}${import.meta.env.BASE_URL}planets/${n}.jpg` // MapLibre's worker needs an absolute URL

export const PLANETS = [
  { id: 'earth', name: 'Earth', tint: '#4f8fd6',
    imagery: { far: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg',
      near: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg' },
    credit: 'Imagery: NASA GIBS Blue Marble · Sentinel-2 cloudless by EOX (modified Copernicus Sentinel data 2020) · names © OpenStreetMap, OpenFreeMap' },
  { id: 'moon', name: 'Moon', tint: '#bdbdbd', tiles: opm('moon'), lo: lo('moon'), maxzoom: 7,
    credit: 'Moon: LRO/LOLA imagery via OpenPlanetaryMap (NASA, public domain)' },
  { id: 'mars', name: 'Mars', tint: '#ff6a3d', tiles: opm('mars'), lo: lo('mars'), maxzoom: 7,
    credit: 'Mars: MGS/MOLA imagery via OpenPlanetaryMap (NASA, public domain)' },
  { id: 'mercury', name: 'Mercury', tint: '#a89c8c', tiles: opm('mercury'), lo: lo('mercury'), maxzoom: 7,
    credit: 'Mercury: MESSENGER imagery via OpenPlanetaryMap (NASA, public domain)' },
]
