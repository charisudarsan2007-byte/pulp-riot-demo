import React, { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Search, ShieldCheck, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useApp } from '../lib/state'
import { addDays, dateLabel, dateLong, diffDays, roleName } from '../lib/format'
import { useApi, postJson } from '../lib/api'
import { Gate, Pill, TrustTags, Loading } from './ui'

const NAV = [
  { to: '/', label: 'Control tower', end: true },
  { to: '/demand', label: 'Demand' },
  { to: '/stock', label: 'Stock' },
  { to: '/supply', label: 'Supply' },
  { to: '/money', label: 'Money at stake' },
  { to: '/decisions', label: 'Decisions' },
  { to: '/replay', label: 'Replay' },
]

const ALL_JUMPS = [
  { d: '2026-03-02', t: 'Dragon Passion launches' },
  { d: '2026-03-16', t: 'Influencer reel goes viral' },
  { d: '2026-03-21', t: 'First surge flag' },
  { d: '2026-03-23', t: 'First stockout' },
  { d: '2026-04-06', t: 'Planners notice (as-was)' },
  { d: '2026-05-04', t: 'Highway closes' },
  { d: '2026-05-12', t: 'Air freight booked' },
  { d: '2026-07-12', t: 'Discounts begin' },
]
const JUMPS = ALL_JUMPS.filter((j) => j.d >= '2026-03-01' && j.d <= '2026-06-30')

export default function Shell() {
  const { asof, setAsof, meta, setTrustOpen, setAskOpen, trustOpen, askOpen } = useApp()
  const loc = useLocation()
  const min = meta?.asof?.min || '2026-03-01'
  const max = meta?.asof?.max || '2026-06-30'
  const span = diffDays(max, min)
  const [drag, setDrag] = useState<number | null>(null)
  useEffect(() => { if (drag === null) return; const id = setTimeout(() => { setAsof(addDays(min, drag)); setDrag(null) }, 500); return () => clearTimeout(id) }, [drag]) // eslint-disable-line
  const pos = drag ?? Math.max(0, Math.min(span, diffDays(asof, min)))
  const printing = loc.pathname.startsWith('/weekly-review')
  return (
    <div className="min-h-screen flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-surface focus:p-3">Skip to content</a>
      {!printing && (
        <>
          <header className="sticky top-0 z-40 bg-bg border-b border-line no-print">
            <div className="container-nds flex items-center gap-6 h-16">
              <NavLink to="/" className="flex flex-col leading-none shrink-0" aria-label="Pulp Riot supply chain agent, home">
                <span className="serif text-[1.65rem]">Pulp Riot</span>
                <span className="label" style={{ fontSize: '0.62rem' }}>Supply chain agent</span>
              </NavLink>
              <nav aria-label="Main" className="hidden lg:flex items-center gap-1 text-[0.92rem] overflow-x-auto">
                {NAV.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end}
                    className={({ isActive }) => `px-3 py-2 whitespace-nowrap border-b-2 ${isActive ? 'border-fg font-medium' : 'border-transparent text-soft hover:text-fg'}`}>{n.label}</NavLink>
                ))}
              </nav>
              <div className="ml-auto flex items-center gap-2">
                <button className="btn btn-ghost btn-sm whitespace-nowrap" onClick={() => setAskOpen(true)} aria-label="Ask the agent (press /)"><Search size={15} aria-hidden /><span className="hidden md:inline">Ask the agent</span><span className="label border border-line rounded-sm px-1 hidden md:inline">/</span></button>
                <button className="btn btn-ghost btn-sm whitespace-nowrap" onClick={() => setTrustOpen(true)}><ShieldCheck size={15} aria-hidden /><span className="hidden md:inline">Data trust</span></button>
              </div>
            </div>
            <nav aria-label="Main (compact)" className="lg:hidden container-nds flex gap-1 overflow-x-auto text-sm pb-1">
              {NAV.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `px-2.5 py-1.5 whitespace-nowrap border-b-2 ${isActive ? 'border-fg font-medium' : 'border-transparent text-soft'}`}>{n.label}</NavLink>
              ))}
            </nav>
          </header>
          <div className="sticky top-16 lg:top-16 z-30 bg-muted border-b border-line no-print">
            <div className="container-nds py-3 flex flex-wrap items-center gap-x-6 gap-y-2">
              <div className="flex items-center gap-2">
                <span className="label label-strong">Replay date</span>
                <button className="btn btn-ghost btn-sm" aria-label="Previous day" onClick={() => setAsof(addDays(asof, -1) < min ? min : addDays(asof, -1))}><ChevronLeft size={15} /></button>
                <input aria-label="Replay date" type="date" className="field !min-h-0 py-1" min={min} max={max} value={asof} onChange={(e) => e.target.value && setAsof(e.target.value)} />
                <button className="btn btn-ghost btn-sm" aria-label="Next day" onClick={() => setAsof(addDays(asof, 1) > max ? max : addDays(asof, 1))}><ChevronRight size={15} /></button>
              </div>
              <div className="flex-1 min-w-[220px] flex items-center gap-3">
                <span className="label hidden md:inline">{dateLabel(min)}</span>
                <div className="relative flex-1">
                  <input aria-label="Move the replay date" type="range" min={0} max={span} value={pos} onChange={(e) => setDrag(Number(e.target.value))} />
                  <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 pointer-events-none h-0">
                    {JUMPS.map((j) => <span key={j.d} title={`${dateLabel(j.d)}: ${j.t}`} className="absolute w-px h-3 bg-fg opacity-50 -top-1.5" style={{ left: `${(diffDays(j.d, min) / span) * 100}%` }} />)}
                  </div>
                </div>
                <span className="label hidden md:inline">{dateLabel(max)}</span>
              </div>
              <label className="flex items-center gap-2">
                <span className="label">Jump to</span>
                <select className="field !min-h-0 py-1 text-sm" value="" onChange={(e) => e.target.value && setAsof(e.target.value)} aria-label="Jump to a moment in the replay">
                  <option value="">Choose a moment</option>
                  {JUMPS.map((j) => <option key={j.d} value={j.d}>{dateLabel(j.d)}: {j.t}</option>)}
                </select>
              </label>
              <Pill tone="neutral" title="All figures come from the supplied synthetic workbook. Dates are a replay, nothing is live.">Synthetic replay, not live</Pill>
            </div>
          </div>
        </>
      )}
      <main id="main" className="flex-1"><Outlet /></main>
      {!printing && (
        <footer className="rule no-print">
          <div className="container-nds py-10 grid md:grid-cols-3 gap-8 text-sm text-soft">
            <div><div className="label label-strong mb-2">What you are looking at</div>Supplied synthetic workbook, replayed as of {dateLong(asof)}. Each screen reads only data that had arrived by 12:00 on that date. Nothing is live, nothing is executed.</div>
            <div><div className="label label-strong mb-2">How to read a figure</div><TrustTags prov={['OBSERVED']} compact /> reported &nbsp; <TrustTags prov={['CALCULATED']} compact /> arithmetic &nbsp; <TrustTags prov={['ESTIMATED']} compact /> model estimate &nbsp; <TrustTags prov={['ASSUMED']} compact /> assumption</div>
            <div><div className="label label-strong mb-2">Approvals</div>Every recommendation stays pending until a named person approves, rejects or changes it. Decisions are saved in a local demo database with an audit trail.</div>
          </div>
        </footer>
      )}
      <AskModal open={askOpen} onClose={() => setAskOpen(false)} />
      <TrustDrawer open={trustOpen} onClose={() => setTrustOpen(false)} />
    </div>
  )
}

/* ------------------------------ A1: Ask the agent ------------------------------ */
const EXAMPLES = ['When will Dragon Passion run out in Mumbai?', 'Which depots are at risk?', 'How is Dragon Passion selling?', 'How many days of passionfruit pulp are left?', 'What needs approval?', 'When must we order from the co-packer?']

export function AskPanel({ onDone, autoFocus }: { onDone?: () => void; autoFocus?: boolean }) {
  const { asof } = useApp()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus])
  const ask = async (text: string) => {
    if (text.trim().length < 2) return
    setBusy(true); setErr(null)
    try { setRes(await postJson('/api/ask', { question: text, asof })) } catch (e: any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q) }} className="flex gap-2">
        <label className="sr-only" htmlFor="ask-input">Ask about stock, risk, demand or approvals</label>
        <input id="ask-input" ref={ref} className="field flex-1 !min-h-[3rem] text-[1.05rem]" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about stock, risk, demand or approvals…" maxLength={300} />
        <button className="btn" type="submit" disabled={busy}>{busy ? 'Looking…' : 'Ask'}</button>
      </form>
      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((e) => <button key={e} className="btn btn-ghost btn-sm" onClick={() => { setQ(e); ask(e) }}>{e}</button>)}
      </div>
      {err && <div role="alert" className="mt-4 st-red rounded-sm p-3 text-sm">{err}</div>}
      {busy && !res && <div className="mt-6"><Loading what="an answer" h={80} /></div>}
      {res && (
        <div className="mt-6 panel p-5" aria-live="polite">
          <p className="serif text-[1.55rem] leading-snug">{res.answer}</p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {(res.sources || []).map((s: any, i: number) => <span key={i} className="label border border-line rounded-sm px-1.5 py-0.5" title={s.freshness}>{s.function} · {s.tag}</span>)}
            <span className="label">{res.freshness}</span>
          </div>
          <p className="label mt-2">{res.method}</p>
          {res.rows?.length > 0 && (
            <div className="mt-4 overflow-x-auto"><table className="tbl"><thead><tr>{Object.keys(res.rows[0]).map((k) => <th key={k}>{k.replace(/_/g, ' ')}</th>)}</tr></thead>
              <tbody>{res.rows.slice(0, 10).map((r: any, i: number) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} className="num">{typeof v === 'number' ? Math.round(v * 10) / 10 : String(v ?? '–')}</td>)}</tr>)}</tbody></table></div>)}
          {res.followups?.length > 0 && <div className="mt-4 flex flex-wrap gap-2">{res.followups.map((f: string) => <button key={f} className="btn btn-ghost btn-sm" onClick={() => { setQ(f); ask(f) }}>{f}</button>)}</div>}
        </div>
      )}
    </div>
  )
}

function AskModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => { const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); if (open) window.addEventListener('keydown', on); return () => window.removeEventListener('keydown', on) }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 md:p-16 overflow-y-auto" role="dialog" aria-modal="true" aria-label="Ask the agent">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <div className="relative panel w-full max-w-3xl p-6 md:p-8">
        <div className="flex items-start justify-between mb-4"><h3 className="h-card">Ask the agent</h3><button aria-label="Close" className="p-1 hover:bg-muted rounded-sm" onClick={onClose}><X size={20} /></button></div>
        <AskPanel autoFocus />
      </div>
    </div>
  )
}

/* ------------------------------ A5: data trust drawer ------------------------------ */
function TrustDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { asof } = useApp()
  const q = useApi<any>(open ? `/api/trust?asof=${asof}` : null)
  useEffect(() => { const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); if (open) window.addEventListener('keydown', on); return () => window.removeEventListener('keydown', on) }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Data trust">
      <div className="fixed inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <aside className="relative bg-bg border-l border-line w-full max-w-xl h-full overflow-y-auto p-6 md:p-8">
        <div className="flex items-start justify-between mb-6"><div><div className="label label-strong mb-1">Data trust</div><h3 className="h-card">Where every number comes from</h3></div>
          <button aria-label="Close" className="p-1 hover:bg-muted rounded-sm" onClick={onClose}><X size={20} /></button></div>
        <Gate q={q} what="the data checks">{(t) => (
          <div className="space-y-8 text-sm">
            <div>
              <div className="label mb-2">Labels used on every figure</div>
              <dl className="space-y-2">{Object.entries(t.badge_legend).map(([k, v]: any) => <div key={k} className="flex gap-3"><dt className="shrink-0 w-28"><TrustTags prov={[{ REPORTED: 'OBSERVED', CALCULATION: 'CALCULATED', ESTIMATE: 'ESTIMATED', ASSUMPTION: 'ASSUMED' }[k] as string]} compact /></dt><dd className="text-soft">{v}</dd></div>)}</dl>
            </div>
            <div>
              <div className="label mb-2">Freshness at 12:00 on {dateLong(asof)}</div>
              <table className="tbl"><thead><tr><th>Source</th><th className="r">Age</th><th className="r">Limit</th><th>State</th></tr></thead>
                <tbody>{t.sources.map((s: any) => <tr key={s.key}><td>{s.label}</td><td className="r num">{s.age_days === null ? '–' : `${s.age_days.toFixed(1)} d`}</td><td className="r num">{s.limit_days} d</td><td>{s.stale ? <Pill tone="red">Stale</Pill> : <Pill tone="green">Fresh</Pill>}</td></tr>)}</tbody></table>
            </div>
            <div>
              <div className="label mb-2">Detection signal</div>
              <p className="text-soft">{t.signal.note}</p>
            </div>
            <div>
              <div className="label mb-2">Dirty-data self-test ({t.fixture.handled} of {t.fixture.total} supplied bad rows handled)</div>
              <p className="text-soft mb-2">{t.cleaning_note}</p>
              <div className="max-h-56 overflow-y-auto border border-line rounded-sm"><table className="tbl"><thead><tr><th>Row</th><th>Problem</th><th>Handled as</th></tr></thead>
                <tbody>{t.fixture.rows.map((r: any) => <tr key={r.row_id}><td className="num">{r.row_id.replace('DIRTY-', '')}</td><td>{r.defect.replace('_', ' ')}</td><td>{r.handled}</td></tr>)}</tbody></table></div>
            </div>
            <div>
              <div className="label mb-2">Checks</div>
              <ul className="list-disc pl-5 space-y-1 text-soft">
                <li>Summer share of annual volume: {t.summer_share_check.share ? `${(t.summer_share_check.share * 100).toFixed(1)}% (${t.summer_share_check.status})` : `${t.summer_share_check.status.replace('_', ' ').toLowerCase()}`}. Expected 48% ± 3 points.</li>
                <li>Depot weeks where the stock balance does not close: {t.balance_gap_weeks}.</li>
                <li>Share of days stocked out in the last four weeks: {Object.entries(t.censored_share_28d).map(([k, v]: any) => `${k} ${(v * 100).toFixed(0)}%`).join(', ')}. Above {(t.censored_limit * 100).toFixed(0)}% the output is marked lower-bound only.</li>
              </ul>
            </div>
            <div>
              <div className="label mb-2">Not in the supplied data</div>
              <ul className="list-disc pl-5 space-y-1 text-soft">{t.missing_inputs.map((m: string) => <li key={m}>{m}</li>)}</ul>
            </div>
            <div>
              <div className="label mb-2">Kept out of the engine on purpose</div>
              <p className="text-soft">Hidden synthetic truth and label sheets ({t.truth_sheets_not_used.join(', ')}) and hindsight columns ({t.hindsight_removed.map((h: any) => `${h.table}: ${h.columns.join(', ')}`).join('; ')}) never reach the forecasting or inventory code. They appear only in the replay evaluation views, marked as truth.</p>
            </div>
          </div>
        )}</Gate>
      </aside>
    </div>
  )
}
