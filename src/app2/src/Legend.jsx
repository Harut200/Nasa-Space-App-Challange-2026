// Legend for the layer on show, from manifest.layers[].legend. Compact, bottom-left of the stage.
export default function Legend({ layer }) {
  if (!layer) return null
  const L = layer.legend || {}
  const grad = L.colors ? `linear-gradient(90deg, ${L.colors.map((c) => (c === '#00000000' ? '#2a2f3a' : c)).join(',')})` : null
  return (
    <div className="legend" role="group" aria-label={`Legend: ${layer.label}`}>
      <div className="legend-title">{layer.label}</div>
      {L.type === 'classes' && (
        <ul className={L.items.length > 6 ? 'cols' : ''}>
          {L.items.map((it) => <li key={it.label}><i style={{ background: it.color }} />{it.label}</li>)}
        </ul>
      )}
      {(L.type === 'ramp' || L.type === 'diverging') && (
        <>
          <div className="ramp" style={{ background: grad }} />
          <div className="ramp-labels">
            {L.type === 'diverging' && L.labels ? <><span>{L.labels[0]}</span><span>{L.labels[1]}</span></> : <><span>{L.min}</span><span>{L.unit}</span><span>{L.max}</span></>}
          </div>
        </>
      )}
      {L.type === 'text' && <p>{L.text}</p>}
    </div>
  )
}
