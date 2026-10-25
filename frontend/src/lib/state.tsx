import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react'
import { useApi } from './api'

export type Econ = 'margin' | 'revenue'

type State = {
  asof: string
  setAsof: (d: string) => void
  econ: Econ
  setEcon: (e: Econ) => void
  role: string
  setRole: (r: string) => void
  dark: boolean
  setDark: (b: boolean) => void
  meta: any | null
  version: number
  bump: () => void
  trustOpen: boolean
  setTrustOpen: (b: boolean) => void
  askOpen: boolean
  setAskOpen: (b: boolean) => void
}

const Ctx = createContext<State>(null as any)
export const useApp = () => useContext(Ctx)

const lsGet = (k: string, d: string) => { try { return localStorage.getItem(k) || d } catch { return d } }
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* private window */ } }

export function AppProvider({ children }: { children: React.ReactNode }) {
  const metaQ = useApi<any>('/api/meta')
  const urlAsof = new URLSearchParams(window.location.search).get('asof')
  const [asof, setAsofState] = useState<string>(urlAsof || '2026-03-24')
  const [econ, setEcon] = useState<Econ>((lsGet('econ', 'margin') as Econ))
  const [role, setRole] = useState<string>(lsGet('role', 'ROLE_PLN'))
  const [dark, setDark] = useState<boolean>(true)
  const [version, setVersion] = useState(0)
  const [trustOpen, setTrustOpen] = useState(false)
  const [askOpen, setAskOpen] = useState(false)

  const setAsof = useCallback((d: string) => {
    setAsofState(d)
    const u = new URL(window.location.href)
    u.searchParams.set('asof', d)
    window.history.replaceState({}, '', u)
  }, [])
  useEffect(() => { document.documentElement.setAttribute('data-theme', 'dark') }, [dark])
  useEffect(() => lsSet('econ', econ), [econ])
  useEffect(() => lsSet('role', role), [role])
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t?.tagName)) { e.preventDefault(); setAskOpen(true) }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [])
  const value = useMemo<State>(() => ({
    asof, setAsof, econ, setEcon, role, setRole, dark, setDark, meta: metaQ.data, version, bump: () => setVersion((v) => v + 1),
    trustOpen, setTrustOpen, askOpen, setAskOpen,
  }), [asof, econ, role, dark, metaQ.data, version, trustOpen, askOpen, setAsof])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const FLAVOR_COLOR: Record<string, string> = { MPB: '#8a6d3b', GC: '#4f7a57', PP: '#8f2f1c', DB: '#5b6770', DP: '#1a1a1a' }
