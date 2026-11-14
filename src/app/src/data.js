// How the app turns a manifest (contract C, plan/00-contracts.md) into image addresses.
// Pure functions, so tests can run them without a browser.
export const nearest = (dates, d) => dates.reduce((a, b) => (Math.abs(Date.parse(b) - Date.parse(d)) < Math.abs(Date.parse(a) - Date.parse(d)) ? b : a))

// The date a layer really shows for a wanted date, and the image for it. `base` ends in "data/".
export function frame(base, area, layer, date) {
  if (!layer.dates) return { url: `${base}${area}/${layer.id}.webp`, shown: null }
  const shown = layer.dates.includes(date) ? date : nearest(layer.dates, date)
  return { url: `${base}${area}/${layer.id}/${shown}.webp`, shown }
}
