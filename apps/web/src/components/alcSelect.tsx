import { useState } from 'react'
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { toQuery } from '../lib/constants'
import { useDebouncedSearch } from '../lib/hooks'
import type { Page } from '../types'

type AlcChoice = { id: string; alc_code: string; alc_name: string }

export const ALC_OPTION_PAGE_SIZE = 25
const label = (a: AlcChoice) => `${a.alc_code} · ${a.alc_name}`

// Searchable ALC filter backed by a server-side paginated list endpoint (e.g. ``/portal/alcs``,
// which the backend scopes to the caller's own ALCs). One page of options is loaded at a time
// and more are fetched on demand, so every in-scope ALC stays reachable without preloading
// them all. The selected ALC stays visible even when it is not in the loaded options.
export function AlcSelect({ endpoint, value, onChange, allLabel = 'All ALCs', className }: {
  endpoint: string; value: string; onChange: (id: string) => void; allLabel?: string; className?: string
}) {
  const [text, setText] = useState('')
  const search = useDebouncedSearch(text)
  const [picked, setPicked] = useState<AlcChoice | null>(null)
  const q = useInfiniteQuery({
    queryKey: ['alc-select', endpoint, search],
    queryFn: ({ pageParam }) => api.get<Page<AlcChoice>>(`${endpoint}?${toQuery({ page: pageParam, page_size: ALC_OPTION_PAGE_SIZE, search })}`),
    initialPageParam: 1,
    getNextPageParam: last => (last.page < last.pages ? last.page + 1 : undefined),
    placeholderData: keepPreviousData,
  })
  const options = q.data?.pages.flatMap(p => p.items) ?? []
  const total = q.data?.pages[0]?.total ?? 0
  const pinned = value && !options.some(o => o.id === value) ? (picked?.id === value ? picked : null) : null
  function choose(id: string) {
    const option = options.find(o => o.id === id)
    if (option) setPicked(option)
    onChange(id)
  }
  return <div className={className}>
    <input className="mb-2" aria-label="Search ALCs" placeholder="Search ALC code or name" value={text} onChange={e => setText(e.target.value)} />
    <select aria-label="ALC" value={value} onChange={e => choose(e.target.value)}>
      <option value="">{allLabel}</option>
      {value && !options.some(o => o.id === value) && <option value={value}>{pinned ? label(pinned) : 'Selected ALC'}</option>}
      {options.map(o => <option key={o.id} value={o.id}>{label(o)}</option>)}
    </select>
    <p className="mt-1 text-xs text-slate-500" aria-live="polite">
      {q.isLoading ? 'Loading ALCs…'
        : q.isError ? 'ALCs could not be loaded.'
        : !options.length ? (search ? 'No ALCs match this search.' : 'No ALCs available.')
        : <>{options.length} of {total} ALCs{q.hasNextPage && <> · <button type="button" className="font-semibold text-teal hover:underline" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? 'Loading…' : 'Show more'}</button></>}</>}
    </p>
  </div>
}
