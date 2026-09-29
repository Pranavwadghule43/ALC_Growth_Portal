// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { click, render, settle, setInput } from '../test/harness'
import { SEARCH_DEBOUNCE_MS, useDebouncedSearch, usePageFor } from './hooks'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

// Records every value the debounced search takes, render by render.
function SearchProbe({ seen }: { seen: string[] }) {
  const [text, setText] = useState('')
  const search = useDebouncedSearch(text)
  if (seen[seen.length - 1] !== search) seen.push(search)
  return <input value={text} onChange={e => setText(e.target.value)} />
}

describe('useDebouncedSearch', () => {
  it('commits only the final value after the typing pauses', async () => {
    const seen: string[] = []
    const { container } = await render(<SearchProbe seen={seen} />)
    const input = container.querySelector('input')!
    for (const value of ['p', 'pu', 'pun']) {
      await setInput(input, value)
      await settle(100) // typing faster than the debounce interval
    }
    await setInput(input, 'pune')
    expect(seen).toEqual([''])
    await settle(SEARCH_DEBOUNCE_MS - 1)
    expect(seen).toEqual([''])
    await settle(1)
    expect(seen).toEqual(['', 'pune'])
  })

  it('trims, and applies a cleared search immediately', async () => {
    const seen: string[] = []
    const { container } = await render(<SearchProbe seen={seen} />)
    const input = container.querySelector('input')!
    await setInput(input, '  pune  ')
    await settle(SEARCH_DEBOUNCE_MS)
    expect(seen).toEqual(['', 'pune'])
    await setInput(input, '')
    expect(seen).toEqual(['', 'pune', '']) // no wait for the timer
    await setInput(input, '   ')
    expect(seen).toEqual(['', 'pune', ''])
  })
})

function PageProbe({ log }: { log: string[] }) {
  const [key, setKey] = useState('a')
  const [page, setPage] = usePageFor(key)
  log.push(`${key}:${page}`)
  return <>
    <button onClick={() => setPage(p => p + 1)}>next</button>
    <button onClick={() => setPage(5)}>five</button>
    <button onClick={() => setKey(k => `${k}x`)}>key</button>
  </>
}

describe('usePageFor', () => {
  it('keeps the page for the same key and resets to 1 in the same render when the key changes', async () => {
    const log: string[] = []
    const { container } = await render(<PageProbe log={log} />)
    const [next, five, key] = [...container.querySelectorAll('button')]
    // Clicks go through act() (the harness helper), so every state update is flushed.
    await click(next!); await settle()
    await click(five!); await settle()
    await click(next!); await settle()
    expect(log[log.length - 1]).toBe('a:6')
    log.length = 0
    await click(key!); await settle()
    // Never rendered with the new key and the old page (which would have requested it).
    expect(log.every(entry => entry === 'ax:1')).toBe(true)
    await click(next!); await settle()
    expect(log[log.length - 1]).toBe('ax:2')
  })
})
