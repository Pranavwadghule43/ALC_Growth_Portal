import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { ChevronDown, LogOut, Menu, Settings } from 'lucide-react'

// Smallest viewport at which a nav item sits directly in the header bar. Below it the item is
// listed under "More" ("Menu" on narrow screens); 'more' keeps it there at every width.
// Pure CSS breakpoints: at any width each item is either in the bar or in the More menu.
export type NavTier = 'lg' | 'xl' | '2xl' | 'more'
export type NavItem = readonly [label: string, to: string, icon: LucideIcon, tier: NavTier]

// Literal class strings so Tailwind's scanner keeps them.
const inBar: Record<Exclude<NavTier, 'more'>, string> = { lg: 'hidden lg:inline-flex', xl: 'hidden xl:inline-flex', '2xl': 'hidden 2xl:inline-flex' }
const inMore: Record<NavTier, string> = { lg: 'lg:hidden', xl: 'xl:hidden', '2xl': '2xl:hidden', more: '' }
const moreButtonShown: Record<NavTier, string> = { lg: 'lg:hidden', xl: 'xl:hidden', '2xl': '2xl:hidden', more: '' }
// "More" lights up (same treatment as an active link) while the active page is one of the items
// it currently holds.
const moreButtonActive: Record<NavTier, string> = {
  lg: 'max-lg:bg-brand max-lg:text-white max-lg:shadow-[inset_0_-2px_0_0_rgb(var(--rcu-light-blue))]',
  xl: 'max-xl:bg-brand max-xl:text-white max-xl:shadow-[inset_0_-2px_0_0_rgb(var(--rcu-light-blue))]',
  '2xl': 'max-2xl:bg-brand max-2xl:text-white max-2xl:shadow-[inset_0_-2px_0_0_rgb(var(--rcu-light-blue))]',
  more: 'bg-brand text-white shadow-[inset_0_-2px_0_0_rgb(var(--rcu-light-blue))]',
}
// Header (navy surface) item states. Active = RCU blue pill with a light-blue underline, so it
// does not rely on hover or colour alone; inactive = slightly muted white.
const barFocus = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-light'
const barItem = (active: boolean) => `${barFocus} rounded-md px-3 py-2 text-sm transition ${active ? 'bg-brand font-semibold text-white shadow-[inset_0_-2px_0_0_rgb(var(--rcu-light-blue))]' : 'font-medium text-white/80 hover:bg-brand/50 hover:text-white'}`
const tierOrder: NavTier[] = ['lg', 'xl', '2xl', 'more']

const LOGO_SRC = `${import.meta.env.BASE_URL}branding/rcu-pune-logo.png`

// Highlight exactly one item: the most specific link that matches the current page
// (so "Add Activity" does not also light up "My Activities"; detail pages light their list).
export function activeLink(pathname: string, links: readonly string[], home: string) {
  return links
    .filter(to => (to === home ? pathname === home : pathname === to || pathname.startsWith(`${to}/`)))
    .sort((a, b) => b.length - a.length)[0]
}

// Disclosure dropdown: the button toggles a panel of ordinary links. Escape closes and returns
// focus to the button; clicking outside or tabbing away closes it.
function Dropdown({ label, buttonClass, align = 'right', children }: { label: ReactNode; buttonClass: string; align?: 'left' | 'right'; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false); const id = useId(); const root = useRef<HTMLDivElement>(null); const button = useRef<HTMLButtonElement>(null)
  const close = () => setOpen(false)
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])
  function onKeyDown(e: KeyboardEvent) { if (e.key === 'Escape' && open) { e.stopPropagation(); close(); button.current?.focus() } }
  function onBlur(e: FocusEvent<HTMLDivElement>) { if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget as Node)) close() }
  return <div ref={root} className="relative" onKeyDown={onKeyDown} onBlur={onBlur}>
    <button ref={button} type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(o => !o)} className={buttonClass}>{label}</button>
    {open && <div id={id} className={`absolute top-full z-50 mt-2 w-60 rounded-lg border border-line bg-white py-1.5 text-ink shadow-lg shadow-navy/15 ${align === 'right' ? 'right-0' : 'left-0'}`}>{children(close)}</div>}
  </div>
}

const menuLink = (active: boolean) => `flex items-center gap-3 px-4 py-2 text-sm focus:outline-none focus-visible:bg-tint focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-bright ${active ? 'bg-tint font-semibold text-brand shadow-[inset_3px_0_0_0_rgb(var(--rcu-blue))]' : 'font-medium text-ink hover:bg-tint hover:text-brand'}`

export interface TopHeaderProps {
  home: string
  nav: readonly NavItem[]
  label: string
  account: { name: string; detail: string; profile?: string }
  notice?: string
  onLogout: () => void
}

// Shared top navigation header for every signed-in workspace (ADMIN / DCU / SBU / ALC).
export default function TopHeader({ home, nav, label, account, notice, onLogout }: TopHeaderProps) {
  const { pathname } = useLocation()
  const current = activeLink(pathname, nav.map(([, to]) => to), home)
  const activeTier = nav.find(([, to]) => to === current)?.[3]
  const lastTier = tierOrder[Math.max(-1, ...nav.map(([, , , tier]) => tierOrder.indexOf(tier)))]
  return <header className="sticky top-0 z-40 border-b-2 border-brand bg-navy text-white shadow-md shadow-navy/20" data-testid="top-header">
    <div className="flex h-[72px] items-center gap-3 px-4 lg:gap-5 lg:px-6">
      <Link to={home} aria-label="RCU Pune — go to dashboard" className={`shrink-0 rounded-md ${barFocus}`}>
        <img src={LOGO_SRC} alt="RCU Pune — Regional Coordination unit, MKCL" width={989} height={592} className="h-12 w-auto rounded-md sm:h-14"/>
      </Link>
      <nav aria-label={label} className="flex min-w-0 flex-1 items-center gap-1">
        {nav.map(([text, to, , tier]) => tier !== 'more' && <Link key={to} to={to} aria-current={to === current ? 'page' : undefined} className={`${inBar[tier]} shrink-0 items-center whitespace-nowrap ${barItem(to === current)}`}>{text}</Link>)}
        {lastTier && <div className={moreButtonShown[lastTier]}>
          <Dropdown align="left" buttonClass={`inline-flex items-center gap-1.5 whitespace-nowrap ${barItem(false)} ${activeTier ? moreButtonActive[activeTier] : ''}`} label={<><Menu className="h-4 w-4 lg:hidden"/><span className="lg:hidden">Menu</span><span className="hidden lg:inline">More</span><ChevronDown className="h-4 w-4"/></>}>
            {close => <ul aria-label={`${label} (more)`}>{nav.map(([text, to, Icon, tier]) => <li key={to} className={inMore[tier]}><Link to={to} onClick={close} aria-current={to === current ? 'page' : undefined} className={menuLink(to === current)}><Icon className="h-4 w-4 shrink-0"/>{text}</Link></li>)}</ul>}
          </Dropdown>
        </div>}
      </nav>
      <Dropdown buttonClass={`flex shrink-0 items-center gap-2.5 rounded-md py-1.5 pl-1.5 pr-2 text-left transition hover:bg-brand/50 ${barFocus}`} label={<>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand font-bold ring-2 ring-brand-light/40" aria-hidden="true">{account.name[0]?.toUpperCase()}</span>
        <span className="hidden min-w-0 max-w-[11rem] xl:block"><span className="block truncate text-sm font-semibold">{account.name}</span><span className="block truncate text-xs text-brand-light">{account.detail}</span></span>
        <span className="sr-only xl:hidden">Account: {account.name}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-brand-light" aria-hidden="true"/>
      </>}>
        {close => <>
          <div className="border-b border-line px-4 pb-2.5 pt-1.5"><p className="truncate text-sm font-semibold text-navy">{account.name}</p><p className="truncate text-xs text-muted">{account.detail}</p></div>
          <ul className="pt-1">
            {account.profile && <li><Link to={account.profile} onClick={close} aria-current={pathname === account.profile ? 'page' : undefined} className={menuLink(pathname === account.profile)}><Settings className="h-4 w-4 shrink-0"/>Profile</Link></li>}
            <li><button type="button" onClick={() => { close(); onLogout() }} className={`${menuLink(false)} w-full`}><LogOut className="h-4 w-4 shrink-0"/>Sign out</button></li>
          </ul>
        </>}
      </Dropdown>
    </div>
    {notice && <p role="status" className="border-t border-white/10 bg-amber-400/15 px-4 py-2 text-center text-xs text-amber-100 lg:px-6">{notice}</p>}
  </header>
}
