import { useCallback, useEffect, useState } from 'react'

// Server-side search fields wait this long after the last keystroke before querying.
export const SEARCH_DEBOUNCE_MS = 350

// ``value`` once it has stopped changing for ``delay`` ms. Values for which ``immediate``
// returns true are applied at once (in the same render), e.g. a cleared search box.
export function useDebouncedValue<T>(value: T, delay = SEARCH_DEBOUNCE_MS, immediate?: (value: T) => boolean): T {
  const [debounced, setDebounced] = useState(value)
  // Adjusting state during render (not in an effect) so the immediate value is used by this
  // very render and no request is ever made for the stale value.
  if (immediate?.(value) && !Object.is(value, debounced)) setDebounced(value)
  useEffect(() => {
    if (Object.is(value, debounced)) return
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay, debounced])
  return debounced
}

// Trimmed search text to send to the server: debounced while typing, cleared immediately.
export function useDebouncedSearch(text: string, delay = SEARCH_DEBOUNCE_MS) {
  return useDebouncedValue(text.trim(), delay, value => value === '')
}

// Page number that returns to 1 whenever ``key`` (the committed search and major filters)
// changes. The reset happens in the same render as the change, so a narrower search never
// asks for a page past its last one and no request is made for the old page number.
export function usePageFor(key: string): [number, (next: number | ((page: number) => number)) => void] {
  const [state, setState] = useState({ key, page: 1 })
  const page = state.key === key ? state.page : 1
  const setPage = useCallback((next: number | ((page: number) => number)) => setState(current => {
    const from = current.key === key ? current.page : 1
    return { key, page: typeof next === 'function' ? next(from) : next }
  }), [key])
  return [page, setPage]
}
