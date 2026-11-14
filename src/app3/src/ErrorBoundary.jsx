import { Component } from 'react'

// A single failure must never produce a blank page.
export default class ErrorBoundary extends Component {
  state = { err: null }
  static getDerivedStateFromError(err) { return { err } }
  componentDidCatch(err) { console.error(err) }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="fatal" role="alert">
        <h1>Something went off beat.</h1>
        <p>The app hit an unexpected error. Your data is fine.</p>
        <button className="btn primary" onClick={() => { location.hash = '#/'; location.reload() }}>Start again</button>
      </div>
    )
  }
}
