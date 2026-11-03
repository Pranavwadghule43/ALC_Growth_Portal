import { Component, type ReactNode } from 'react'

export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <main className="mx-auto max-w-xl p-6"><div role="alert" className="panel p-6"><h1 className="text-xl font-bold text-navy">This page could not be displayed</h1><p className="mt-3 text-sm text-slate-600">Reload the portal to try again. If the problem continues, contact your administrator.</p><button className="btn-primary mt-4" onClick={() => window.location.reload()}>Reload portal</button></div></main> : this.props.children
  }
}
