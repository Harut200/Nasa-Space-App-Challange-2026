import { Component, StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { currentTheme } from './meta.js'

document.documentElement.dataset.theme = currentTheme()

// A render error must never leave a blank page: show a message and a way back instead.
class Boundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="splash" style={{ textAlign: 'center', gap: 12 }}>
        <p>Something went wrong while drawing the app.</p>
        <p><a href={location.pathname}>Back to the globe</a></p>
      </div>
    )
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Boundary><App /></Boundary>
  </StrictMode>,
)
