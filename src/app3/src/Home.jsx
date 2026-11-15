const B = `${import.meta.env.BASE_URL}brand/`

// The landing: team name, title, and the partner logos with the challenge line at the bottom left. The top bar and the globe are the way in.
export default function Home({ onGlobe }) {
  return (
    <>
      <div className="opening">
        <div className="opening-copy">
          <p className="eyebrow">Team Constellation</p>
          <h1>Dancing<br /><em>with the SARs</em></h1>
          <div className="actions"><button className="btn primary big" onClick={onGlobe}>Jump in <span aria-hidden>→</span></button></div>
        </div>
      </div>
      <div className="landing-foot">
        <div className="landing-logos">
          <img src={`${B}nasa.svg`} height="34" alt="NASA" />
          <img src={`${B}isro.svg`} height="34" alt="ISRO" />
          <img src={`${B}lockup.svg`} height="22" alt="Constellation" />
        </div>
        <p className="mono">NASA Space Apps Challenge 2026</p>
      </div>
    </>
  )
}
