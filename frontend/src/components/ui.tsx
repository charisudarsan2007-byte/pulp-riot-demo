import React, { useState } from 'react'
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Loader2, ArrowUpRight, X } from 'lucide-react'
import { dateLabel } from '../lib/format'

/* ---------- trust tags (A5): every figure says whether it is reported, calculated, estimated or assumed ---------- */
const PROV_HELP: Record<string, string> = {
  REPORTED: 'A figure reported by a source system (here: the supplied synthetic workbook).',
  CALCULATION: 'Arithmetic on reported figures; no model uncertainty.',
  ESTIMATE: 'A model estimate. It carries an uncertainty type.',
  ASSUMPTION: 'A value the case does not give. It is configurable and shown as an assumption.',
}
const WARN_HELP: Record<string, string> = {
  LOWER_BOUND_ONLY: 'More than 30% of recent days were stocked out: sales are a floor, demand is higher.',
  EXTRAPOLATING: 'No own history: built from analogs and a launch template.',
  UNITS_SOLD_SIGNAL: 'The feed has no uncensored orders column; detection uses units sold and skips stockout days.',
  EVENT_SHAPE_UNKNOWN: 'The event has not peaked yet, so the size mixture stays at its prior.',
  SURGE_SCENARIO: 'Demand multiplied by the surge you chose.',
  PHASED: 'Monthly total spread evenly over calendar days (no daily data exists for GT/MT).',
  RECONCILED: 'Scaled to add up to the parent total.',
  DATA_QUALITY: 'A data-quality issue touches this figure.',
  STALE: 'The source is older than its freshness limit.',
}

export function provClass(prov: string[] = []): 'REPORTED' | 'CALCULATION' | 'ESTIMATE' | 'ASSUMPTION' {
  const p = new Set(prov)
  if (p.has('ASSUMED')) return 'ASSUMPTION'
  if (p.has('ESTIMATED') || p.has('CENSOR_ADJUSTED') || p.has('PROXY')) return 'ESTIMATE'
  if (p.has('CALCULATED') || p.has('MEASURED')) return 'CALCULATION'
  return 'REPORTED'
}

const PROV_STYLE: Record<string, string> = {
  REPORTED: 'bg-fg text-bg border-fg',
  CALCULATION: 'border-fg text-fg',
  ESTIMATE: 'border-fg text-fg border-dashed',
  ASSUMPTION: 'border-bronze text-bronze border-dotted',
}

export function TrustTags({ prov = [], interval, grain, warnings = [], asof, source, compact }: {
  prov?: string[]; interval?: string | null; grain?: string | null; warnings?: string[]; asof?: string | null; source?: string | null; compact?: boolean
}) {
  const cls = provClass(prov)
  const chips: React.ReactNode[] = []
  chips.push(
    <span key="p" title={`${PROV_HELP[cls]}\nTags: ${prov.join(', ') || 'none'}`} className={`label inline-flex items-center rounded-sm border px-1.5 py-0.5 normal-case-none ${PROV_STYLE[cls]}`} style={{ letterSpacing: '0.08em' }}>{cls}</span>)
  if (prov.includes('SYNTHETIC') && !compact) chips.push(<span key="s" title="All data in this prototype is the supplied synthetic workbook, replayed as of a chosen date." className="label border border-line rounded-sm px-1.5 py-0.5">Synthetic</span>)
  if (interval) chips.push(<span key="i" title={interval === 'SCENARIO' ? 'Uncalibrated scenario interval: sigma0 x sqrt(horizon) x grain factor. Not a measured error band.' : interval === 'EVENT_SCENARIO' ? 'Quantiles of the event scenario mixture (transient, plateau, saturating growth).' : 'Calibrated on the engine\'s own backtest errors (at least 30 pairs).'} className="label border border-line rounded-sm px-1.5 py-0.5">{interval.replace('_', ' ')}</span>)
  if (grain) chips.push(<span key="g" title={WARN_HELP[grain] || grain} className="label border border-line rounded-sm px-1.5 py-0.5">{grain.replace('_', ' ')}</span>)
  warnings.filter((w) => w !== grain).slice(0, compact ? 2 : 6).forEach((w) => chips.push(
    <span key={w} title={WARN_HELP[w] || w} className="label st-amber rounded-sm px-1.5 py-0.5">{w.replace(/_/g, ' ')}</span>))
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 align-middle">
      {chips}
      {asof && <span className="label">as of {dateLabel(asof)}</span>}
      {source && <span className="label hidden md:inline">{source}</span>}
    </span>
  )
}

/* ---------- status with icon and label, never colour alone ---------- */
export function Status({ s, label, small }: { s: 'RED' | 'AMBER' | 'GREEN' | string; label?: string; small?: boolean }) {
  const map: Record<string, { cls: string; icon: React.ReactNode; text: string }> = {
    RED: { cls: 'st-red', icon: <AlertOctagon size={small ? 12 : 14} aria-hidden />, text: 'Stockout likely' },
    AMBER: { cls: 'st-amber', icon: <AlertTriangle size={small ? 12 : 14} aria-hidden />, text: 'Watch' },
    GREEN: { cls: 'st-green', icon: <CheckCircle2 size={small ? 12 : 14} aria-hidden />, text: 'Covered' },
  }
  const m = map[s] || { cls: 'st-neutral', icon: <Info size={14} aria-hidden />, text: s }
  return <span className={`inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-[0.78rem] font-medium ${m.cls}`}>{m.icon}{label ?? m.text}</span>
}

export function Pill({ children, tone = 'neutral', title }: { children: React.ReactNode; tone?: 'red' | 'amber' | 'green' | 'neutral' | 'ink'; title?: string }) {
  const cls = tone === 'ink' ? 'bg-fg text-bg' : `st-${tone}`
  return <span title={title} className={`inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-[0.78rem] font-medium ${cls}`}>{children}</span>
}

/* ---------- layout ---------- */
export function Section({ id, label, code, title, lead, tags, children, tone = 'bg', aside }: {
  id?: string; label?: string; code?: string; title: string; lead?: React.ReactNode; tags?: React.ReactNode; children: React.ReactNode; tone?: 'bg' | 'surface' | 'muted'; aside?: React.ReactNode
}) {
  const bg = tone === 'surface' ? 'bg-surface' : tone === 'muted' ? 'bg-muted' : ''
  return (
    <section id={id} className={`${bg} rule`}>
      <div className="container-nds section-y">
        <div className="flex flex-wrap items-start justify-between gap-6 mb-6">
          <div className="max-w-3xl">
            <div className="flex items-center gap-3 mb-3">
              {label && <span className="label label-strong">{label}</span>}
              {code && <span className="label border border-line rounded-sm px-1.5" title="Feature ID in the capabilities directory">{code}</span>}
            </div>
            <h2 className="h-section">{title}</h2>
            {lead && <p className="mt-3 text-soft text-[1.05rem] max-w-2xl">{lead}</p>}
            {tags && <div className="mt-4">{tags}</div>}
          </div>
          {aside}
        </div>
        {children}
      </div>
    </section>
  )
}

export function Hero({ title, lead, children, kicker }: { title: string; lead?: React.ReactNode; children?: React.ReactNode; kicker?: string }) {
  return (
    <header className="container-nds pt-14 md:pt-20 pb-12 md:pb-16">
      {kicker && <div className="label label-strong mb-5">{kicker}</div>}
      <div className="grid md:grid-cols-12 gap-10 items-end">
        <div className="md:col-span-7">
          <h1 className="h-page">{title}</h1>
          {lead && <p className="mt-6 text-[1.15rem] text-soft max-w-xl">{lead}</p>}
        </div>
        <div className="md:col-span-5">{children}</div>
      </div>
    </header>
  )
}

export function Stat({ label, value, sub, tags, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tags?: React.ReactNode; tone?: string }) {
  return (
    <div>
      <div className="label mb-2">{label}</div>
      <div className="stat num" style={tone ? { color: `var(--${tone})` } : undefined}>{value}</div>
      {sub && <div className="mt-2 text-sm text-soft">{sub}</div>}
      {tags && <div className="mt-2">{tags}</div>}
    </div>
  )
}

export function Card({ children, className = '', muted }: { children: React.ReactNode; className?: string; muted?: boolean }) {
  return <div className={`${muted ? 'panel-muted' : 'panel'} p-5 md:p-6 ${className}`}>{children}</div>
}

/* ---------- controls ---------- */
export function Seg({ value, onChange, options, label }: { value: string; onChange: (v: any) => void; options: { id: string; label: string }[]; label?: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex border border-line rounded-sm overflow-hidden bg-surface">
      {options.map((o) => (
        <button key={o.id} type="button" aria-pressed={value === o.id} onClick={() => onChange(o.id)}
          className={`px-3 py-1.5 text-sm ${value === o.id ? 'bg-fg text-bg' : 'hover:bg-muted'}`}>{o.label}</button>
      ))}
    </div>
  )
}

export function Labeled({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={`block ${className}`}><span className="label block mb-1.5">{label}</span>{children}</label>
}

export function Select({ value, onChange, options, label, className = '' }: { value: string; onChange: (v: string) => void; options: { id: string; label: string }[]; label: string; className?: string }) {
  return (
    <Labeled label={label} className={className}>
      <select className="field w-full" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    </Labeled>
  )
}

/* ---------- states ---------- */
export function Loading({ what = 'this view', h = 220 }: { what?: string; h?: number }) {
  return (
    <div role="status" aria-live="polite" className="panel p-6 flex items-center gap-3 text-soft" style={{ minHeight: h }}>
      <Loader2 className="animate-spin" size={18} aria-hidden />
      <span>Working out {what} from the data that had arrived by this date…</span>
    </div>
  )
}

export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div role="alert" className="panel p-6 st-red" style={{ background: 'var(--red-bg)' }}>
      <div className="font-medium mb-1">This view could not be loaded</div>
      <div className="text-sm mb-3 break-words">{error}</div>
      {retry && <button className="btn btn-ghost btn-sm" onClick={retry}>Try again</button>}
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="panel p-8 text-center text-soft">{children}</div>
}

export function Gate<T>({ q, what, children, h }: { q: { data: T | null; loading: boolean; error: string | null; reload: () => void }; what?: string; children: (d: T) => React.ReactNode; h?: number }) {
  if (q.error && !q.data) return <ErrorBox error={q.error} retry={q.reload} />
  if (!q.data) return <Loading what={what} h={h} />
  return <div className={q.loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>{children(q.data)}</div>
}

/* ---------- details expander for equations ---------- */
export function Details({ summary, children }: { summary: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-4">
      <button type="button" className="label label-strong inline-flex items-center gap-1 underline underline-offset-4" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'} {summary}</button>
      {open && <div className="mt-3 text-sm text-soft border-l-2 border-line pl-4 space-y-2">{children}</div>}
    </div>
  )
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; wide?: boolean }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 md:p-10 overflow-y-auto" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fixed inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <div className={`relative panel w-full ${wide ? 'max-w-4xl' : 'max-w-xl'} p-6 md:p-8 mt-6`}>
        <div className="flex items-start justify-between mb-5"><h3 className="h-card">{title}</h3>
          <button aria-label="Close" className="p-1 hover:bg-muted rounded-sm" onClick={onClose}><X size={20} /></button></div>
        {children}
      </div>
    </div>
  )
}

export function LinkArrow({ children }: { children: React.ReactNode }) {
  return <span className="inline-flex items-center gap-1 underline underline-offset-4">{children}<ArrowUpRight size={14} aria-hidden /></span>
}

export function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-soft max-w-3xl">{children}</p>
}
