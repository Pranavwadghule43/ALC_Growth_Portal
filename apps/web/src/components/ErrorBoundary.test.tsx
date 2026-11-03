// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from '../test/harness'
import ErrorBoundary from './ErrorBoundary'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); document.body.innerHTML = '' })
describe('ErrorBoundary', () => {
  it('shows a recovery action when a page throws without exposing the exception', async () => {
    vi.useFakeTimers()
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    function Broken(): never { throw new Error('private request detail') }
    const { container } = await render(<ErrorBoundary><Broken /></ErrorBoundary>)
    expect(errors).toHaveBeenCalled()
    expect(container.textContent).toContain('This page could not be displayed')
    expect(container.querySelector('button')?.textContent).toBe('Reload portal')
    expect(container.textContent).not.toContain('private request detail')
  })
})
