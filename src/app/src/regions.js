// PLACEHOLDER DATA. Every region name, coordinate set and weather event below is
// INVENTED so the interface has something to show. It is not a forecast.
// Replace the whole list with the real one; the app reads nothing else from here.
// Event: { type, severity: 'high' | 'low', from, to (YYYY-MM-DD), probability: 0..1, summary }
export const REGIONS = [
  { id: 'yerevan', name: 'Yerevan', lngLat: [44.51, 40.18], events: [
    { type: 'Heavy snow', severity: 'high', from: '2026-11-22', to: '2026-11-25', probability: 0.72, summary: 'Snowfall above 20 cm expected over the city and the northern heights.' },
    { type: 'Cold snap', severity: 'low', from: '2026-11-27', to: '2026-12-01', probability: 0.48, summary: 'Night temperatures drop below minus 8 degrees.' },
  ] },
  { id: 'gyumri', name: 'Gyumri', lngLat: [43.85, 40.79], events: [
    { type: 'Storm', severity: 'high', from: '2026-11-19', to: '2026-11-20', probability: 0.81, summary: 'Strong wind gusts and a mix of rain and snow.' },
  ] },
  { id: 'vanadzor', name: 'Vanadzor', lngLat: [44.49, 40.81], events: [
    { type: 'Flood', severity: 'low', from: '2026-11-18', to: '2026-11-21', probability: 0.34, summary: 'Rivers in the valley may rise after steady rain.' },
  ] },
  { id: 'sevan', name: 'Lake Sevan', lngLat: [45.35, 40.37], events: [
    { type: 'Storm', severity: 'low', from: '2026-11-23', to: '2026-11-24', probability: 0.41, summary: 'Rough water and gusts along the northern shore.' },
  ] },
  { id: 'syunik', name: 'Syunik', lngLat: [46.15, 39.2], events: [
    { type: 'Heavy snow', severity: 'high', from: '2026-12-02', to: '2026-12-05', probability: 0.64, summary: 'Mountain passes may close under fresh snow.' },
  ] },
  { id: 'tonle-sap', name: 'Tonle Sap', lngLat: [104.0, 12.9], events: [
    { type: 'Flood', severity: 'high', from: '2026-11-16', to: '2026-11-30', probability: 0.88, summary: 'The lake is draining; low-lying fields stay waterlogged.' },
    { type: 'Storm', severity: 'low', from: '2026-11-20', to: '2026-11-21', probability: 0.29, summary: 'Isolated thunderstorms in the late afternoon.' },
  ] },
  { id: 'mekong', name: 'Mekong Delta', lngLat: [105.8, 10.0], events: [
    { type: 'Flood', severity: 'low', from: '2026-11-17', to: '2026-11-24', probability: 0.52, summary: 'High tides push salt water inland.' },
  ] },
  { id: 'sahel', name: 'Sahel', lngLat: [2.1, 13.5], events: [
    { type: 'Heatwave', severity: 'high', from: '2026-11-25', to: '2026-11-30', probability: 0.57, summary: 'Daytime highs near 42 degrees for several days.' },
    { type: 'Drought', severity: 'low', from: '2026-11-01', to: '2026-12-15', probability: 0.45, summary: 'Soil moisture stays below the seasonal average.' },
  ] },
]
