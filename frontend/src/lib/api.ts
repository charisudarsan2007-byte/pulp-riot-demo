import { useEffect, useRef, useState, useCallback } from 'react'

const cache = new Map<string, any>()
const inflight = new Map<string, Promise<any>>()

export async function getJson(path: string): Promise<any> {
  if (cache.has(path)) return cache.get(path)
  if (inflight.has(path)) return inflight.get(path)
  const p = fetch(path).then(async (r) => {
    const body = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(body?.detail || body?.error || `Request failed (${r.status})`)
    cache.set(path, body)
    return body
  }).finally(() => inflight.delete(path))
  inflight.set(path, p)
  return p
}

export async function postJson(path: string, body: any): Promise<any> {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const out = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : out?.error || `Request failed (${r.status})`)
  return out
}

export function clearCache(prefix?: string) {
  for (const k of Array.from(cache.keys())) if (!prefix || k.startsWith(prefix)) cache.delete(k)
}

export type Loaded<T> = { data: T | null; loading: boolean; error: string | null; reload: () => void }

/** Fetch a GET endpoint; `path` null skips. Keeps the previous data visible while a new request is in flight. */
export function useApi<T = any>(path: string | null, bust = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState<boolean>(!!path)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const last = useRef<string | null>(null)
  useEffect(() => {
    if (!path) { setLoading(false); return }
    let live = true
    setLoading(true)
    setError(null)
    if (tick > 0 || bust > 0) clearCache(path)
    getJson(path).then((d) => { if (live) { setData(d); setLoading(false); last.current = path } })
      .catch((e) => { if (live) { setError(String(e.message || e)); setLoading(false) } })
    return () => { live = false }
  }, [path, tick, bust])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, loading, error, reload }
}

export function qs(params: Record<string, any>): string {
  const u = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') u.set(k, String(v)) })
  const s = u.toString()
  return s ? `?${s}` : ''
}
