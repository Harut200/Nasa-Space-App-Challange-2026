// Planet registry. Non-Earth imagery = OpenPlanetaryMap web-mercator XYZ tiles (CORS-open, verified with curl).
// ponytail: no bundled offline textures; tiles need network. Add equirect->mercator canvas warp if offline is needed.
const OPM = 'https://cartocdn-gusc.global.ssl.fastly.net/opmbuilder/api/v1/map/named'
const opm = (n) => `${OPM}/opm-${n}-basemap-v0-1/all/{z}/{x}/{y}.png`

export const PLANETS = [
  { id: 'earth', name: 'Earth', tint: '#7aa7c7', credit: '© OpenFreeMap © OpenMapTiles Data from OpenStreetMap' },
  { id: 'moon', name: 'Moon', tint: '#bdbdbd', tiles: opm('moon'), maxzoom: 7,
    credit: 'Moon: LRO/LOLA imagery via OpenPlanetaryMap (NASA, public domain)' },
  { id: 'mars', name: 'Mars', tint: '#ff6a3d', tiles: opm('mars'), maxzoom: 7,
    credit: 'Mars: MGS/MOLA imagery via OpenPlanetaryMap (NASA, public domain)' },
  { id: 'mercury', name: 'Mercury', tint: '#a89c8c', tiles: opm('mercury'), maxzoom: 7,
    credit: 'Mercury: MESSENGER imagery via OpenPlanetaryMap (NASA, public domain)' },
]
