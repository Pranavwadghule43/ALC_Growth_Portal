// Minimal component-test harness (jsdom + React's own ``act``; no extra testing library).
// Test files using it start with ``// @vitest-environment jsdom`` and mock ``../lib/api``.
import type { ReactNode } from 'react'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

export async function render(ui: ReactNode, path = '/') {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } } })
  const root = createRoot(container)
  await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter></QueryClientProvider>) })
  await settle()
  return { container, unmount: () => act(() => { root.unmount(); container.remove() }) }
}

// Run due timers and resolved promises (React Query notifies through setTimeout(0)).
export async function settle(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
}

function setNative(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value)
}

// Type ``text`` one character at a time (each keystroke is its own change event).
export async function typeText(input: HTMLInputElement, text: string, { from = input.value } = {}) {
  for (let i = 1; i <= text.length; i += 1) await setInput(input, from + text.slice(0, i))
}

export async function setInput(input: HTMLInputElement, value: string) {
  await act(async () => { setNative(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}

export async function selectValue(select: HTMLSelectElement, value: string) {
  await act(async () => { setNative(select, value); select.dispatchEvent(new Event('change', { bubbles: true })) })
}

export async function click(el: Element) {
  await act(async () => { (el as HTMLElement).click() })
}

export function button(container: Element, name: string) {
  const found = [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === name)
  if (!found) throw new Error(`No button "${name}"`)
  return found
}

export function inputByPlaceholder(container: Element, placeholder: string) {
  const found = container.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`)
  if (!found) throw new Error(`No input "${placeholder}"`)
  return found
}

// ---- API fake -------------------------------------------------------------------------------
export type Handler = (path: string, params: URLSearchParams) => unknown

// Records every GET and answers it from ``handler``. A handler may return a Promise to hold a
// response back (e.g. to reproduce out-of-order responses) or throw to simulate an API error.
export function fakeApi(get: ReturnType<typeof vi.fn>, handler: Handler) {
  const calls: string[] = []
  get.mockImplementation((url: string) => {
    calls.push(url)
    const [path, query = ''] = url.split('?')
    try { return Promise.resolve(handler(path!, new URLSearchParams(query))) } catch (error) { return Promise.reject(error) }
  })
  return {
    calls,
    to: (path: string) => calls.filter(c => c.split('?')[0] === path).map(c => new URLSearchParams(c.split('?')[1] ?? '')),
  }
}

export function page<T>(items: T[], params: URLSearchParams, extra: Record<string, unknown> = {}) {
  const size = Number(params.get('page_size') ?? 25)
  const current = Number(params.get('page') ?? 1)
  return { items: items.slice((current - 1) * size, current * size), page: current, page_size: size, total: items.length, pages: Math.ceil(items.length / size), ...extra }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
