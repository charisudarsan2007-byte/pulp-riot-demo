import React, { useEffect, useMemo, useState } from 'react'
import { ArrowRight, Clock, Lock } from 'lucide-react'
import { useApp } from '../lib/state'
import { useApi, postJson, qs, clearCache } from '../lib/api'
import { dateLabel, dateLong, inr, inrShort, n0, roleName } from '../lib/format'
import { Card, Details, Empty, Gate, Hero, Labeled, Modal, Note, Pill, Section, Seg, Stat, TrustTags } from '../components/ui'

const TYPE_LABEL: Record<string, string> = {
  reforecast: 'Re-forecast', rebalance: 'Move stock between depots', plant: 'Ship from plant stock', copacker: 'Co-packer order', fefo_redirect: 'Sell oldest lots first',
  air_freight: 'Air freight for pulp', pulp_buffer: 'Pulp buffer', mix_shift: 'Change production mix', allocation_reset: 'Reset allocation', no_option: 'No option can deliver',
}
const STATUS_TONE: Record<string, any> = { PENDING: 'ink', APPROVED: 'green', REJECTED: 'red', MODIFIED: 'amber' }

export default function Decisions() {
  const { asof, meta, version } = useApp()
  const al = useApi<any>(`/api/alerts${qs({ asof })}`, version)
  const ac = useApi<any>(`/api/actions${qs({ asof })}`, version)
  const [filter, setFilter] = useState<'open' | 'decided' | 'all'>('open')
  const [group, setGroup] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(decodeURIComponent(window.location.hash.replace('#', '')) || null)
  const rows: any[] = ac.data?.rows || []
  const open = rows.find((r) => r.id === openId) || null
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Decisions"
        lead="Every recommendation waits here until a named person approves, rejects or changes it. This tool never buys, produces, moves stock or sends messages: it saves the decision and the reason.">
        <Gate q={al} what="the alert summary" h={120}>{(d) => (
          <div className="grid grid-cols-3 gap-6">
            <Stat label="Waiting" value={d.pending} sub="recommendations" />
            <Stat label="Escalated" value={d.groups.filter((g: any) => g.escalated).length} sub="alert cards past 24 h" tone={d.groups.some((g: any) => g.escalated) ? 'amber' : undefined} />
            <Stat label="Past deadline" value={d.groups.filter((g: any) => g.overdue).length} sub="alert cards" tone={d.groups.some((g: any) => g.overdue) ? 'red' : undefined} />
          </div>)}</Gate>
      </Hero>

      <Section id="alerts" label="Alert centre" code="G4 · G11" tone="surface" title="Open alerts grouped by cause, with owner and deadline"
        lead={`Duplicate alerts (the same flavor and kind of problem across depots) collapse into one card. If nobody decides within ${al.data?.sla_hours ?? 24} hours, the card passes to the next role up and finally to the COO.`}>
        <Gate q={al} what="the alert cards">{(d) => d.groups.length === 0 ? <Empty>Nothing is waiting at this date.</Empty> : (
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            {d.groups.map((g: any) => (
              <Card key={g.group_key} className={g.overdue ? 'border-[var(--red)]' : ''}>
                <div className="flex items-start justify-between gap-3">
                  <div><div className="label mb-1">{g.kinds.map((k: string) => TYPE_LABEL[k] || k).join(' · ')}</div><div className="h-card">{g.flavor_name || g.group_key.replace(/^[a-z]+:/, '')}</div></div>
                  {g.merged_count > 1 && <Pill tone="neutral" title="Duplicate alerts merged into this card">{g.merged_count} merged</Pill>}
                </div>
                <p className="text-sm text-soft mt-2">{g.depots.length ? `${g.depots.length} depot${g.depots.length > 1 ? 's' : ''}: ${g.depots.slice(0, 4).join(', ')}${g.depots.length > 4 ? '…' : ''}.` : 'Network-wide.'}</p>
                <div className="grid grid-cols-2 gap-3 mt-4"><div><div className="label">At stake</div><div className="stat-md num">{inrShort(g.exposure)}</div></div><div><div className="label">Decide by</div><div className="stat-md num">{dateLabel(g.deadline)}</div></div></div>
                <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
                  <Pill tone="neutral">Owner now: {roleName(g.owner)}</Pill>
                  {g.escalated ? <Pill tone="amber"><Clock size={12} aria-hidden /> Escalated</Pill> : g.hours_to_next != null && <span className="label">escalates in {Math.max(0, g.hours_to_next).toFixed(0)} h</span>}
                  {g.overdue && <Pill tone="red">Deadline passed</Pill>}
                </div>
                <button className="btn btn-ghost btn-sm mt-4" onClick={() => { setGroup(g.group_key); setFilter('open'); document.getElementById('register')?.scrollIntoView({ behavior: 'smooth' }) }}>Open the {g.merged_count} item{g.merged_count > 1 ? 's' : ''} <ArrowRight size={14} aria-hidden /></button>
              </Card>))}
          </div>)}</Gate>
      </Section>

      <Section id="register" label="Register" tone="bg" title="Every recommendation, who decides it, and the outcome"
        lead="Open an item to see the numbers behind it and decide. Each item is decided by its named approver."
        aside={<div className="flex gap-3 items-end">{group && <button className="btn btn-ghost btn-sm" onClick={() => setGroup(null)}>Clear card filter</button>}<Seg value={filter} onChange={setFilter} label="Show" options={[{ id: 'open', label: 'Waiting' }, { id: 'decided', label: 'Decided' }, { id: 'all', label: 'All' }]} /></div>}>
        <Gate q={ac} what="the decision register">{(d) => {
          const list = rows.filter((r) => (filter === 'all' || (filter === 'open' ? r.status_at_asof === 'PENDING' && r.active : r.status_at_asof !== 'PENDING')) && (!group || r.group_key === group))
          return list.length === 0 ? <Empty>No items match.</Empty> : (
            <div className="overflow-x-auto"><table className="tbl" style={{ minWidth: 880 }}>
              <thead><tr><th>Recommendation</th><th>Approver</th><th>Decide by</th><th className="r">Cost</th><th className="r">At stake</th><th>Status</th></tr></thead>
              <tbody>{list.map((r) => (
                <tr key={r.id} id={r.id} className="cursor-pointer hover:bg-muted" onClick={() => setOpenId(r.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setOpenId(r.id)}>
                  <td className="max-w-md"><div className="label mb-0.5">{TYPE_LABEL[r.type] || r.type}</div><div className="font-medium leading-snug">{r.title}</div>{!r.active && r.status_at_asof === 'PENDING' && <div className="text-xs text-soft mt-1">No longer triggered at this date.</div>}</td>
                  <td>{roleName(r.approver_role)}{r.escalation?.escalated && <div className="text-xs" style={{ color: 'var(--amber)' }}>now with {roleName(r.escalation.current_owner)}</div>}</td>
                  <td className="num whitespace-nowrap">{dateLabel(r.deadline)}{r.escalation?.deadline_passed && <div className="text-xs" style={{ color: 'var(--red)' }}>passed</div>}</td>
                  <td className="r num">{r.cost_inr != null ? inrShort(r.cost_inr) : '–'}</td><td className="r num">{r.exposure_inr != null ? inrShort(r.exposure_inr) : '–'}</td>
                  <td><Pill tone={STATUS_TONE[r.status_at_asof]}>{r.status_at_asof.toLowerCase()}</Pill></td></tr>))}</tbody></table></div>)
        }}</Gate>
      </Section>

      <DecisionModal action={open} onClose={() => { setOpenId(null); history.replaceState({}, '', window.location.pathname + window.location.search) }} />
      <AuditTrail />
    </>
  )
}

function chainOf(meta: any, role: string): string[] {
  const parent: Record<string, string | null> = Object.fromEntries((meta?.roles || []).map((r: any) => [r.role_id, r.escalates_to]))
  const out = [role]
  let cur = role
  while (parent[cur] && !out.includes(parent[cur] as string)) { cur = parent[cur] as string; out.push(cur) }
  return out
}

function DecisionModal({ action, onClose }: { action: any | null; onClose: () => void }) {
  const { asof, meta, bump } = useApp()
  const role = action?.approver_role || ''
  const [reason, setReason] = useState('')
  const [qty, setQty] = useState<number>(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { setReason(''); setMsg(null); setQty(Math.round(action?.qty || 0)) }, [action?.id])
  const audit = useApi<any>(action ? `/api/audit${qs({ action_id: action.id })}` : null, msg?.ok ? 1 : 0)
  if (!action) return null
  const allowed = true
  const pending = action.status_at_asof === 'PENDING'
  const submit = async (decision: 'approve' | 'reject' | 'modify') => {
    setBusy(true); setMsg(null)
    try {
      await postJson(`/api/actions/${encodeURIComponent(action.id)}/decision`, { decision, actor_role: role, asof, reason: reason || null, qty: decision === 'modify' ? qty : null })
      clearCache('/api/actions'); clearCache('/api/alerts'); clearCache('/api/audit'); clearCache('/api/finance'); clearCache('/api/control-tower')
      setMsg({ ok: true, text: `Saved as ${decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : 'approved with a changed quantity'}. Nothing was executed; the people involved carry it out.` })
      bump()
    } catch (e: any) { setMsg({ ok: false, text: e.message }) } finally { setBusy(false) }
  }
  const p = action.payload || {}
  const snap = action.source_snapshot || {}
  return (
    <Modal open onClose={onClose} title={TYPE_LABEL[action.type] || action.type} wide>
      <div className="space-y-5">
        <div><h4 className="serif text-[1.7rem] leading-snug">{action.title}</h4><p className="text-soft mt-2">{action.rationale}</p></div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div><div className="label">Quantity</div><div className="stat-md num">{action.qty != null ? n0(action.qty) : '–'}</div><div className="text-xs text-soft">{action.unit}</div></div>
          <div><div className="label">Cost</div><div className="stat-md num">{action.cost_inr != null ? inrShort(action.cost_inr) : '–'}</div></div>
          <div><div className="label">At stake</div><div className="stat-md num">{action.exposure_inr != null ? inrShort(action.exposure_inr) : '–'}</div></div>
          <div><div className="label">Decide by</div><div className="stat-md num">{dateLabel(action.deadline)}</div></div>
        </div>
        <div className="flex flex-wrap gap-2 items-center"><Pill tone="neutral">Approver: {roleName(action.approver_role)}</Pill>
          {action.escalation && <Pill tone={action.escalation.escalated ? 'amber' : 'neutral'}>Owner now: {roleName(action.escalation.current_owner)}</Pill>}
          <Pill tone={STATUS_TONE[action.status_at_asof]}>{action.status_at_asof.toLowerCase()}</Pill>
          <TrustTags prov={['SYNTHETIC', 'ESTIMATED']} interval={p.interval_type || 'SCENARIO'} warnings={p.warnings} asof={asof} /></div>
        {p.late && <p className="st-amber rounded-sm p-3 text-sm">This option arrives after the depot's earliest-likely stockout date. It still reduces the loss later, but it will not prevent the first stockout.</p>}
        {p.options && (
          <Details summary="the options that were compared"><table className="tbl"><thead><tr><th>Option</th><th className="r">Bottles</th><th>Arrives</th><th className="r">₹/bottle</th><th>On time?</th></tr></thead>
            <tbody>{p.options.map((o: any) => <tr key={o.key}><td>{o.label}</td><td className="r num">{n0(o.qty)}</td><td>{o.arrival_date ? dateLabel(o.arrival_date) : '–'}</td><td className="r num">{o.cost_per_bottle.toFixed(2)}</td><td>{o.key === 'nothing' ? '–' : o.qty === 0 ? 'not available' : o.arrives_too_late ? 'too late' : 'in time'}</td></tr>)}</tbody></table></Details>)}
        <Details summary="the data snapshot this rests on"><p>Replay date {snap.asof}; only rows that had arrived by {snap.arrival_cutoff}. Model {snap.model_version}, configuration {snap.config_hash}. {snap.provenance}.</p></Details>

        {pending ? (
          <div className="rule pt-5">
            <Labeled label="Reason (needed to reject or change)"><textarea className="field w-full" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} /></Labeled>
            <div className="flex flex-wrap items-end gap-3 mt-4">
              <button className="btn" disabled={!allowed || busy} onClick={() => submit('approve')}>Approve as recommended</button>
              <button className="btn btn-ghost" disabled={!allowed || busy || !reason.trim()} onClick={() => submit('reject')}>Reject</button>
              <span className="mx-2 text-soft">or</span>
              <label className="block"><span className="label block mb-1">New quantity</span><input type="number" min={0} className="field w-36" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></label>
              <button className="btn btn-ghost" disabled={!allowed || busy || !reason.trim()} onClick={() => submit('modify')}>Approve with this quantity</button>
            </div>
          </div>
        ) : (
          <div className="rule pt-5 text-sm"><b>{roleName(action.decided_by)}</b> {action.status.toLowerCase()} this on {dateLong(action.decided_asof)}{action.status === 'MODIFIED' ? ` with quantity ${n0(action.approved_qty)}` : ''}. {action.decision_reason && <>Reason: “{action.decision_reason}”.</>}</div>
        )}
        {msg && <div role={msg.ok ? 'status' : 'alert'} className={`rounded-sm p-3 text-sm ${msg.ok ? 'st-green' : 'st-red'}`}>{msg.text}</div>}
        {audit.data?.rows?.length > 0 && <Details summary="audit trail for this item"><table className="tbl"><thead><tr><th>Replay date</th><th>Who</th><th>What</th><th>Change</th><th>Reason</th></tr></thead>
          <tbody>{audit.data.rows.map((a: any) => <tr key={a.id}><td className="num">{dateLabel(a.asof)}</td><td>{a.actor_role === 'ENGINE' ? 'Engine' : roleName(a.actor_role)}</td><td>{a.event.toLowerCase()}</td><td>{a.from_status || '–'} → {a.to_status}{a.qty != null ? ` (${n0(a.qty)})` : ''}</td><td className="text-soft">{a.reason}</td></tr>)}</tbody></table></Details>}
      </div>
    </Modal>
  )
}

function AuditTrail() {
  const { version } = useApp()
  const q = useApi<any>('/api/audit', version)
  const hist = useApi<any>('/api/decisions-history')
  const [reset, setReset] = useState<string | null>(null)
  const { bump } = useApp()
  return (
    <Section id="audit" label="History" tone="surface" title="Who decided what, and what the log held before this tool"
      lead="Every change is logged with who, when (replay date), the data snapshot and the quantities. Decisions persist in a local demo database, not a company system."
      aside={<div><button className="btn btn-ghost btn-sm" onClick={async () => { await postJson('/api/demo/reset', {}); clearCache(); bump(); setReset('Demo decisions cleared.') }}>Reset demo decisions</button>{reset && <div className="label mt-1">{reset}</div>}</div>}>
      <div className="grid lg:grid-cols-2 gap-8">
        <div>
          <div className="label label-strong mb-2">Latest changes in this tool</div>
          <Gate q={q} what="the audit log" h={120}>{(d) => {
            const rows = d.rows.filter((r: any) => r.event !== 'CREATED').slice(0, 12)
            return rows.length === 0 ? <Empty>No decision has been made yet. Open an item above to decide.</Empty> : (
              <table className="tbl"><thead><tr><th>Date</th><th>Who</th><th>What</th><th>Item</th></tr></thead>
                <tbody>{rows.map((a: any) => <tr key={a.id}><td className="num">{dateLabel(a.asof)}</td><td>{roleName(a.actor_role)}</td><td>{a.event.toLowerCase()}</td><td className="text-xs">{a.action_id}</td></tr>)}</tbody></table>)
          }}</Gate>
        </div>
        <div>
          <div className="label label-strong mb-2">Prior decisions in the workbook <TrustTags prov={['SYNTHETIC']} compact /></div>
          <Gate q={hist} what="the prior log" h={120}>{(d) => (
            <div className="max-h-72 overflow-y-auto"><table className="tbl"><thead><tr><th>Date</th><th>Type</th><th>Flavor</th><th>Decision</th><th>Who</th></tr></thead>
              <tbody>{d.rows.slice(0, 30).map((r: any) => <tr key={r.decision_id}><td className="num">{dateLabel(String(r.decided_at).slice(0, 10))}</td><td>{r.action_type}</td><td>{r.flavor_id}</td><td>{r.decision}</td><td>{roleName(r.approver_role)}</td></tr>)}</tbody></table></div>)}</Gate>
          <Note>This is the workbook's synthetic demo log; it is not part of the live register.</Note>
        </div>
      </div>
    </Section>
  )
}
