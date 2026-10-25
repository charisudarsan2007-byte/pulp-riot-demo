import { useFeatureIndex } from '../components/FeatureIndex'
import React, { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { useApp } from '../lib/state'
import { useApi, postJson, qs } from '../lib/api'
import { bottles, dateLabel, dateLong, inr, inrShort, n0, pct } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { Card, Details, Empty, Gate, Hero, Labeled, Loading, Note, Pill, Section, Seg, Select, Status, TrustTags } from '../components/ui'

function useSel() {
  const [sp, setSp] = useSearchParams()
  const flavor = sp.get('flavor') || 'DP'
  const depot = sp.get('depot') || 'DEP04'
  const set = (f: string, d: string) => setSp({ flavor: f, depot: d }, { replace: true })
  return { flavor, depot, set }
}

export default function Stock() {
  const { asof, meta } = useApp()
  const { flavor, depot, set } = useSel()
  const ix = useFeatureIndex('stock', [
    { id: 'table', title: 'Bottles on hand at every depot, by flavor', desc: 'Stock at each depot and flavor, as reported last week and as estimated today, with days of cover and excess stock.' },
    { id: 'tank', title: 'Days of stock left at this depot', desc: 'For the chosen flavor and depot: stock now, the safety level, and the dates it is likely to run out.' },
    { id: 'shelf', title: 'Days left to sell before shops reject this stock', desc: 'Bottles grouped by remaining shelf life, oldest first, against what modern trade and quick commerce accept.' },
    { id: 'transfer', title: 'Check both depots before moving bottles', desc: 'Pick a sending depot and quantity to see what is left at each end, when bottles land and the transfer cost.' },
    { id: 'shift', title: 'Depots selling differently from the supply plan', desc: 'Depots where real demand has moved away from the monthly plan, so supply can follow it.' },
    { id: 'domino', title: 'How a sales surge empties stock and hides real demand', desc: 'Step by step chain from a surge to empty shelves, and why reported sales then understate demand.' },
    { id: 'fix', title: 'What each day of delay costs, and the fix that arrives in time', desc: 'The cost of waiting per day and a comparison of fixes by cost and arrival date.' },
  ])
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Stock"
        lead="What each depot holds, what it holds today (between weekly reports), how many days it will last, and how to fix a shortage before shelves empty. Choose a depot here. The flavor is chosen in the Stock today section.">
        <div className="grid grid-cols-1 gap-4">
          <Select label="Depot" value={depot} onChange={(v) => set(flavor, v)} options={(meta?.depots || []).map((f: any) => ({ id: f.id, label: f.name }))} />
        </div>
      </Hero>
      {ix.panel}
      {ix.show('table') && <StockTable flavor={flavor} depot={depot} set={set} />}
      {ix.show('tank') && <Tank flavor={flavor} depot={depot} set={set} />}
      {ix.show('shelf') && <Shelf flavor={flavor} depot={depot} />}
      {ix.show('transfer') && <Transfer flavor={flavor} depot={depot} />}
      {ix.show('shift') && <Shift set={set} />}
      {ix.show('domino') && <DominoSurge flavor={flavor} depot={depot} />}
      {ix.show('fix') && <DelayAndMitigation flavor={flavor} depot={depot} />}
    </>
  )
}

/* ------------------------------ C1 + C3 ------------------------------ */
function StockTable({ flavor, depot, set }: { flavor: string; depot: string; set: (f: string, d: string) => void }) {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/inventory/table${qs({ asof })}`)
  const [show, setShow] = useState<'nowcast' | 'reported' | 'cover'>('nowcast')
  return (
    <Section id="table" label="Stock on hand" code="C1 · C3" tone="surface" title="Bottles on hand at every depot, by flavor"
      lead="Reported is what the depot said in its last weekly report. Today is the nowcast between reports. Stock in different places cannot stand in for one another: a bottle at the plant is not a bottle on a depot shelf."
      aside={<Seg value={show} onChange={setShow} label="Show" options={[{ id: 'nowcast', label: 'Today (nowcast)' }, { id: 'reported', label: 'Last report' }, { id: 'cover', label: 'Days of cover' }]} />}>
      <Gate q={q} what="the stock table">{(d) => {
        const dn = Object.fromEntries((meta?.depots || []).map((x: any) => [x.id, x.name]))
        const flavors = ['DP', 'PP', 'MPB', 'GC', 'DB']
        const get = (f: string, dp: string) => d.rows.find((r: any) => r.flavor === f && r.depot === dp)
        return (
          <>
            <div className="overflow-x-auto"><table className="tbl" style={{ minWidth: 900 }}>
              <thead><tr><th>Flavor</th>{(meta?.depots || []).map((x: any) => <th key={x.id} className="r">{x.name}</th>)}<th className="r">All depots</th></tr></thead>
              <tbody>{flavors.map((f) => {
                const row = (meta?.depots || []).map((x: any) => get(f, x.id))
                if (!row.length || !row[0]) return null
                const tot = row.reduce((s: number, c: any) => s + (show === 'reported' ? c.reported : c.nowcast_p50), 0)
                return <tr key={f}><td className="font-medium">{row[0].flavor_name}</td>{row.map((c: any) => {
                  const selected = f === flavor && c.depot === depot
                  const tone = c.status === 'RED' ? 'st-red' : c.status === 'AMBER' ? 'st-amber' : ''
                  return <td key={c.depot} className="r num p-0"><button className={`w-full text-right px-2.5 py-2 ${tone} ${selected ? 'outline outline-2 outline-[var(--fg)]' : ''}`} onClick={() => set(f, c.depot)}
                    aria-label={`${c.flavor_name} at ${c.depot_name}: ${bottles(c.nowcast_p50)} today, ${bottles(c.reported)} last reported, ${c.cover_p50.toFixed(0)} days of cover`}>
                    {show === 'cover' ? `${c.cover_p50 >= 60 ? '60+' : c.cover_p50.toFixed(0)} d` : bottles(show === 'reported' ? c.reported : c.nowcast_p50, 1)}{c.excess && <span className="ml-1 text-[0.65rem]" title="Excess stock">▲</span>}</button></td>
                })}<td className="r num font-medium">{show === 'cover' ? '' : bottles(tot, 1)}</td></tr>
              })}</tbody></table></div>
            <p className="mt-3 text-sm text-soft">Report dated {dateLong(d.report_date)}. Shaded cells are amber or red; ▲ marks more than 45 days of cover. Click a cell to follow it below. <TrustTags prov={d.provenance} asof={asof} /></p>
            <div className="mt-12">
              <div className="label label-strong mb-3">Stock by location, bottles (raw pulp shown as the bottles it could make)</div>
              <Chart height={340} data={[
                ['plant_fg', 'Plant finished goods', t.ink], ['in_transit', 'In trucks', t.bronze], ['depot', 'At depots (nowcast)', t.green], ['raw_pulp_bottle_eq', 'Raw pulp (bottle-equivalent)', t.slate],
              ].map(([k, lab, colr]: any) => ({ type: 'bar', name: lab, x: d.locations.rows.map((r: any) => r.name), y: d.locations.rows.map((r: any) => r[k]), marker: { color: colr }, hovertemplate: '%{x}: %{y:,.0f}<extra>' + lab + '</extra>' }))}
                layout={{ barmode: 'stack', yaxis: { title: { text: 'Bottles', font: { size: 11 } } } }} />
              <Note>{d.locations.note} Plant stock is from the week of {dateLabel(d.locations.plant_fg_week)}.</Note>
            </div>
          </>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ C2 · C4 · C5 · C6 ------------------------------ */
function Tank({ flavor, depot, set }: { flavor: string; depot: string; set: (f: string, d: string) => void }) {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/inventory/tank${qs({ asof, flavor, depot })}`)
  return (
    <Section id="tank" label="Stock today" code="C2 · C4 · C5 · C6" tone="bg" title="Days of stock left at this depot"
      aside={<Select label="Flavor" value={flavor} onChange={(v) => set(v, depot)} options={(meta?.flavors || []).map((f: any) => ({ id: f.id, label: f.name }))} />}
      lead="Left: the depot tank. The dashed outline is last week's report; the solid fill is today's estimate. Right: the same stock running down, with the earliest-likely and expected empty dates.">
      <Gate q={q} what="the depot tank">{(d) => {
        const target = d.safety.target_final
        const old = d.safety.old_rule?.target
        const max = Math.max(d.reported, d.nowcast.p90, target, old || 0) * 1.15
        const H = 300, W = 170
        const y = (v: number) => H - (v / max) * H
        const traj = d.trajectory
        const c = d.cover
        return (
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-5 lg:sticky lg:top-44 lg:self-start">
              <div className="flex gap-6 items-start">
                <svg width={W + 140} height={H + 40} role="img" aria-label={`Tank: last report ${n0(d.reported)} bottles, today about ${n0(d.nowcast.p50)}, smart safety target ${n0(target)}`}>
                  <g transform="translate(20,10)">
                    <rect x={0} y={0} width={W} height={H} rx={6} fill="none" stroke="var(--fg)" strokeWidth={2} />
                    <rect x={3} y={y(d.nowcast.p50)} width={W - 6} height={H - y(d.nowcast.p50)} rx={3} fill="var(--bronze)" opacity={0.85} />
                    <rect x={3} y={y(d.nowcast.p90)} width={W - 6} height={Math.max(0, y(d.nowcast.p50) - y(d.nowcast.p90))} fill="var(--bronze)" opacity={0.25} />
                    <rect x={-6} y={y(d.reported)} width={W + 12} height={H - y(d.reported)} fill="none" stroke="var(--fg)" strokeDasharray="5 4" strokeWidth={1.5} />
                    <line x1={-8} x2={W + 8} y1={y(target)} y2={y(target)} stroke="var(--green)" strokeWidth={2.5} strokeDasharray="8 4" />
                    {old != null && <line x1={-8} x2={W + 8} y1={y(old)} y2={y(old)} stroke="var(--red)" strokeWidth={2} strokeDasharray="2 4" />}
                    <g transform={`translate(${W / 2 - 40},${Math.max(18, y(d.reported) - 4)})`}><text fontSize="11" fill="var(--soft)">last report</text></g>
                    <text x={W + 14} y={y(target) + 4} fontSize="11" fill="var(--green)" fontWeight="600">smart target</text>
                    {old != null && <text x={W + 14} y={y(old) + 4} fontSize="11" fill="var(--red)">old 21-day rule</text>}
                  </g>
                </svg>
                <div className="text-sm space-y-3 pt-2">
                  <div className="flex items-center gap-2" style={{ color: 'var(--green)' }}><ArrowUp size={16} aria-hidden /><span><b className="num">+{n0(d.receipts_since)}</b> receipts since the report</span></div>
                  <div className="flex items-center gap-2" style={{ color: 'var(--red)' }}><ArrowDown size={16} aria-hidden /><span><b className="num">−{n0(d.outflow_since.qc_observed + d.outflow_since.gtmt_estimated_p50)}</b> estimated sales since the report</span></div>
                  <div className="text-soft text-xs">of which {n0(d.outflow_since.qc_observed)} quick-commerce (reported next day) and {n0(d.outflow_since.gtmt_estimated_p50)} GT/MT (estimated)</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mt-6">
                <div><div className="label">Last report</div><div className="stat-md num">{bottles(d.reported, 1)}</div><div className="text-xs text-soft">{dateLabel(d.report_date)}, {d.report_age_days} d ago</div></div>
                <div><div className="label">Today (nowcast)</div><div className="stat-md num">{bottles(d.nowcast.p50, 1)}</div><div className="text-xs text-soft">{bottles(d.nowcast.p10, 1)} to {bottles(d.nowcast.p90, 1)}</div></div>
                <div><div className="label">Smart target</div><div className="stat-md num">{bottles(target, 1)}</div><div className="text-xs text-soft">old rule {bottles(old, 1)}</div></div>
              </div>
              <div className="mt-4"><TrustTags prov={d.provenance} interval={d.interval_type} asof={asof} /></div>
              <Details summary="the equation and timestamps">
                <p><b>Today = last report + receipts − sales since the report.</b> {d.equation}.</p>
                <p>Report received {d.report_received_at}. Receipts are shipments dispatched and due before today ({d.receipts_detail.map((r: any) => `${dateLabel(r.date)}: ${n0(r.qty)}`).join('; ') || 'none'}). Inbound still on the road: {d.inbound.map((r: any) => `${n0(r.qty)} on ${dateLabel(r.eta)} from ${r.from}`).join('; ') || 'none'}.</p>
                <p>Smart target = the {pct(d.safety.service_level)} quantile of demand summed over the {d.safety.lead_time_days}-day lead time plus {d.safety.review_days}-day review ({d.safety.formula}). {d.safety.shelf_life_cap_applied ? 'Capped by the 60-day MT/QC rule. ' : ''}{d.safety.gt_rule_note}</p>
              </Details>
            </div>
            <div className="lg:col-span-7">
              <Chart height={330} data={[
                { x: traj.map((p: any) => p.date), y: traj.map((p: any) => p.p90), mode: 'lines', line: { width: 0 }, hoverinfo: 'skip', showlegend: false },
                { x: traj.map((p: any) => p.date), y: traj.map((p: any) => p.p10), mode: 'lines', line: { width: 0 }, fill: 'tonexty', fillcolor: t.band, name: 'Low to high stock' },
                { x: traj.map((p: any) => p.date), y: traj.map((p: any) => p.p50), mode: 'lines', line: { color: t.ink, width: 2.5 }, name: 'Expected stock (P50)' },
              ]} layout={{ yaxis: { title: { text: 'Bottles on the shelf', font: { size: 11 } }, rangemode: 'tozero' },
                shapes: [
                  { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: target, y1: target, line: { color: t.green, dash: 'dash', width: 2 } },
                  { type: 'line', x0: c.date_p10, x1: c.date_p10, yref: 'paper', y0: 0, y1: 1, line: { color: t.amber, width: 2.5 } },
                  { type: 'line', x0: c.date_p50, x1: c.date_p50, yref: 'paper', y0: 0, y1: 1, line: { color: t.red, width: 2.5 } }],
                annotations: [{ x: c.date_p10, y: 0.7, yref: 'paper', text: `Earliest likely out ${dateLabel(c.date_p10)}`, showarrow: false, xanchor: 'right', font: { size: 11, color: t.amber }, bgcolor: 'rgba(0,0,0,0)' },
                  { x: c.date_p50, y: 0.55, yref: 'paper', text: `Expected out ${dateLabel(c.date_p50)}`, showarrow: false, xanchor: 'left', font: { size: 11, color: t.red } },
                  { xref: 'paper', x: 0.01, y: target, text: 'smart safety target', showarrow: false, yshift: 10, xanchor: 'left', font: { size: 10, color: t.green } }] }} />
              <div className="mt-3">
                <div className="label mb-2">Days of cover from today</div>
                <div className="relative h-9 bg-muted rounded-sm overflow-hidden" role="img" aria-label={`Cover: earliest ${c.days_p10.toFixed(0)} days, expected ${c.days_p50.toFixed(0)} days`}>
                  {(() => { const span = Math.max(30, c.days_p90 * 1.15); const w = (v: number) => `${Math.min(100, (v / span) * 100)}%`; return (<>
                    <div className="absolute inset-y-0 left-0" style={{ width: w(c.days_p10), background: 'var(--green-bg)' }} />
                    <div className="absolute inset-y-0" style={{ left: w(c.days_p10), width: `calc(${w(c.days_p50)} - ${w(c.days_p10)})`, background: 'var(--amber-bg)' }} />
                    <div className="absolute inset-y-0 w-1" style={{ left: w(c.days_p10), background: 'var(--amber)' }} /><div className="absolute inset-y-0 w-1" style={{ left: w(c.days_p50), background: 'var(--red)' }} /></>) })()}
                </div>
                <div className="flex flex-wrap gap-x-8 gap-y-1 mt-2 text-sm">
                  <span><b className="num">{c.days_p10.toFixed(0)} days</b> to the earliest-likely stockout ({dateLong(c.date_p10)})</span>
                  <span><b className="num">{c.days_p50.toFixed(0)} days</b> expected ({dateLong(c.date_p50)})</span>
                </div>
                <p className="text-xs text-soft mt-2">{c.definition} A range, not an exact day: the inputs only support this. {c.lower_bound_only && 'Many recent days were stocked out, so true demand is higher than the data shows and cover may be shorter.'}</p>
              </div>
            </div>
          </div>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ C7 + C10 ------------------------------ */
function Shelf({ flavor, depot }: { flavor: string; depot: string }) {
  const { asof } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/inventory/sellable${qs({ asof, flavor, depot })}`)
  return (
    <Section id="shelf" label="Bottle age" code="C7 · C10" tone="surface" title="Days left to sell before shops reject this stock"
      lead="A bottle lasts 90 days, and MT and QC buyers refuse anything with under 60 left: about 30 days to sell a fresh bottle to them. General trade is assumed to take any age, but that rule is not verified in the case.">
      <Gate q={q} what="the bottle age clock">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-5 lg:sticky lg:top-44 lg:self-start">
            <div className="label">Days left on the oldest bottle MT/QC will still accept</div>
            <div className="stat num" style={{ color: d.oldest_eligible_days_left != null && d.oldest_eligible_days_left <= 7 ? 'var(--red)' : undefined }}>{d.oldest_eligible_days_left ?? '–'}<span className="text-soft text-[0.4em]"> days</span></div>
            <Chart height={260} data={[{ type: 'bar', orientation: 'h', y: d.buckets.map((b: any) => b.label), x: d.buckets.map((b: any) => b.qty), marker: { color: d.buckets.map((b: any) => b.lo < 0 ? t.slate : b.hi <= 7 ? t.red : b.hi <= 14 ? t.amber : t.green) }, hovertemplate: '%{y}: %{x:,.0f} bottles<extra></extra>' }]}
              layout={{ margin: { l: 170 }, xaxis: { title: { text: 'Bottles', font: { size: 11 } } }, yaxis: { autorange: 'reversed' } }} />
            <p className="text-sm mt-2"><b className="num">{n0(d.at_risk_mtqc)}</b> bottles will not sell to MT/QC before the cutoff at the current MT+QC rate (about {n0(d.mtqc_rate_per_day)} a day).</p>
            <div className="mt-3"><TrustTags prov={d.provenance} asof={asof} /></div>
            <Note>{d.rule}</Note>
          </div>
          <div className="lg:col-span-7">
            <div className="label label-strong mb-2">Oldest stock first: ship in this order</div>
            {d.fefo.plan.length === 0 ? <Empty>No stock of this flavor at this depot.</Empty> : (
              <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>#</th><th>Lot</th><th className="r">Bottles</th><th className="r">Age</th><th className="r">MT/QC days left</th><th>Do this</th></tr></thead>
                <tbody>{d.fefo.plan.map((p: any) => <tr key={p.lot_id}><td className="num">{p.order}</td><td className="num text-xs">{p.lot_id.replace('LOT-', '')}</td><td className="r num">{n0(p.qty)}</td><td className="r num">{p.age_days} d</td>
                  <td className="r num">{p.days_left_mtqc <= 0 ? <Pill tone="neutral">GT only</Pill> : <Pill tone={p.days_left_mtqc <= 7 ? 'red' : p.days_left_mtqc <= 14 ? 'amber' : 'green'}>{p.days_left_mtqc} d</Pill>}</td><td className="text-sm">{p.action}</td></tr>)}</tbody></table></div>)}
            <Note>{d.fefo.rule}</Note>
          </div>
        </div>)}</Gate>
    </Section>
  )
}

/* ------------------------------ C12 ------------------------------ */
function Transfer({ flavor, depot }: { flavor: string; depot: string }) {
  const { asof, meta } = useApp()
  const [donor, setDonor] = useState('DEP01')
  const [qty, setQty] = useState(40000)
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const recipient = depot
  const geo = useApi<any>(`/api/rebalancing${qs({ asof })}`)
  useEffect(() => { if (donor === recipient) setDonor(recipient === 'DEP01' ? 'DEP02' : 'DEP01') }, [recipient]) // eslint-disable-line
  const go = async () => {
    setBusy(true); setErr(null)
    try { setRes(await postJson('/api/inventory/transfer-preview', { asof, flavor, donor, recipient, qty })) } catch (e: any) { setErr(e.message); setRes(null) } finally { setBusy(false) }
  }
  useEffect(() => { setRes(null) }, [asof, flavor, depot])
  const dn = Object.fromEntries((meta?.depots || []).map((x: any) => [x.id, x.name]))
  return (
    <Section id="transfer" label="Before you move stock" code="C12" tone="bg" title="Check both depots before moving bottles"
      lead="Pick a donor and a quantity for the depot you are following. The check shows what is left at each end, when the bottles land against the recipient's stockout dates, and the ₹1.50 per bottle transfer cost. It records nothing; a transfer is only proposed on the Decisions page.">
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-4 space-y-4 lg:sticky lg:top-44 lg:self-start">
          <Select label="Send from (donor)" value={donor} onChange={setDonor} options={(meta?.depots || []).filter((x: any) => x.id !== recipient).map((x: any) => ({ id: x.id, label: x.name }))} />
          <div><div className="label mb-1.5">Receiving depot</div><div className="field bg-muted">{dn[recipient]}</div></div>
          <Labeled label={`Bottles: ${n0(qty)}`}><input type="range" min={5000} max={400000} step={5000} value={qty} onChange={(e) => setQty(Number(e.target.value))} /></Labeled>
          <button className="btn" onClick={go} disabled={busy}>{busy ? 'Checking…' : 'Check this move'}</button>
          {err && <div role="alert" className="st-red p-3 rounded-sm text-sm">{err}</div>}
        </div>
        <div className="lg:col-span-8">
          <TransferMap depots={geo.data?.depots || []} donor={donor} recipient={recipient} qty={qty} res={res} />
          {!res ? <Empty>Choose a donor and quantity, then check the move.</Empty> : (
            <>
              <div className="flex flex-wrap items-center gap-3 mb-4">{res.feasible ? <Pill tone="green">Feasible</Pill> : <Pill tone="red">Not feasible</Pill>}
                <Pill tone={res.arrives_before_recipient_p10 ? 'green' : 'red'}>{res.arrives_before_recipient_p10 ? 'Lands before the earliest-likely stockout' : 'Lands after the earliest-likely stockout'}</Pill>
                <span className="text-sm">Lands {dateLong(res.arrival_date)} ({res.transit_days} days) · cost {inr(res.cost_total)} (₹{res.cost_per_bottle.toFixed(2)} a bottle)</span></div>
              {res.reasons?.map((r: string) => <p key={r} className="text-sm st-red p-2 rounded-sm mb-2">{r}</p>)}
              {res.warnings?.includes('SOME_BOTTLES_GT_ONLY_ON_ARRIVAL') && <p className="text-sm st-amber p-2 rounded-sm mb-2">{n0(res.gt_only_on_arrival)} of these bottles would arrive with under 60 days left and could only go to general trade.</p>}
              <div className="grid md:grid-cols-2 gap-6">
                {[['Donor: ' + dn[res.donor.depot], res.donor, 'stock_after'], ['Recipient: ' + dn[res.recipient.depot], res.recipient, 'stock_after_arrival']].map(([title, s, k]: any) => (
                  <Card key={title} muted>
                    <div className="label label-strong mb-3">{title}</div>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div><div className="label">Before</div><div className="stat-md num">{bottles(s.stock_before, 1)}</div><div className="text-soft">out around {dateLabel(s.cover_before.date_p50)}</div></div>
                      <div><div className="label">After</div><div className="stat-md num">{bottles(s[k], 1)}</div><div className="text-soft">out around {dateLabel(s.cover_after.date_p50)}</div></div>
                    </div>
                    <div className="mt-3 text-sm text-soft">Earliest-likely stockout {dateLabel(s.cover_before.date_p10)} → {dateLabel(s.cover_after.date_p10)}. Smart target {bottles(s.safety_target, 1)}{s.below_target_after ? ' (left below target)' : ''}{s.need != null ? `; recipient need ${bottles(s.need, 1)}` : ''}.</div>
                  </Card>))}
              </div>
              {res.fefo_lots.length > 0 && <p className="mt-4 text-sm text-soft">Lots to send (oldest first): {res.fefo_lots.slice(0, 4).map((l: any) => `${n0(l.qty)} aged ${l.age_days} d`).join('; ')}.</p>}
              <p className="mt-2"><TrustTags prov={res.provenance} /> <Pill tone="neutral">{res.status}</Pill></p>
            </>)}
        </div>
      </div>
    </Section>
  )
}

function TransferMap({ depots, donor, recipient, qty, res }: { depots: any[]; donor: string; recipient: string; qty: number; res: any }) {
  const t = useChartTheme()
  if (!depots.length) return null
  const by: Record<string, any> = Object.fromEntries(depots.map((x) => [x.id, x]))
  const role = (id: string) => id === recipient ? 'receives' : id === donor ? 'sends' : ''
  const traces: any[] = [{ type: 'scatter', mode: 'markers+text', x: depots.map((x) => x.x), y: depots.map((x) => x.y),
    text: depots.map((x) => x.name + (role(x.id) ? ` (${role(x.id)})` : '')), textposition: 'bottom center', hoverinfo: 'text', showlegend: false, textfont: { size: 11, color: t.fg },
    marker: { size: depots.map((x) => x.id === recipient || x.id === donor ? 22 : 13), color: depots.map((x) => x.id === recipient ? t.red : x.id === donor ? t.green : t.slate), line: { color: t.fg, width: 1.5 } } }]
  const d0 = by[donor], r0 = by[recipient]
  const ann: any[] = []
  if (d0 && r0) {
    ann.push({ x: r0.x, y: r0.y, ax: d0.x, ay: d0.y, xref: 'x', yref: 'y', axref: 'x', ayref: 'y', showarrow: true, arrowhead: 3, arrowsize: 1.2, arrowwidth: 2 + Math.min(4, qty / 80000), arrowcolor: res && !res.feasible ? t.red : t.green, standoff: 14 })
    ann.push({ x: (d0.x + r0.x) / 2, y: (d0.y + r0.y) / 2 + 0.9, xref: 'x', yref: 'y', showarrow: false, text: `${n0(qty)} bottles` + (res ? `, lands ${dateLabel(res.arrival_date)}` : ''), font: { size: 12, color: t.fg }, bgcolor: t.surface })
    if (res) {
      ann.push({ x: d0.x, y: d0.y + 1.2, xref: 'x', yref: 'y', showarrow: false, text: `${bottles(res.donor.stock_before, 1)} to ${bottles(res.donor.stock_after, 1)}`, font: { size: 11, color: t.green } })
      ann.push({ x: r0.x, y: r0.y + 1.2, xref: 'x', yref: 'y', showarrow: false, text: `${bottles(res.recipient.stock_before, 1)} to ${bottles(res.recipient.stock_after_arrival, 1)}`, font: { size: 11, color: t.red } })
    }
  }
  return (
    <div className="mb-6">
      <Chart data={traces} height={380} layout={{ xaxis: { visible: false, range: [70, 94] }, yaxis: { visible: false, range: [10, 31], scaleanchor: 'x' }, annotations: ann, margin: { l: 8, r: 8, t: 8, b: 8 } }} />
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-soft">
        <span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3 rounded-full" style={{ background: t.green }} /> Sends the bottles</span>
        <span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3 rounded-full" style={{ background: t.red }} /> Receives them</span>
        <span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3 rounded-full" style={{ background: t.slate }} /> Other depots</span>
        <span>Stock figures under each depot are before and after the move.</span>
      </div>
    </div>
  )
}

/* ------------------------------ C15 ------------------------------ */
function Shift({ set }: { set: (f: string, d: string) => void }) {
  const { asof, meta } = useApp()
  const q = useApi<any>(`/api/inventory/shift${qs({ asof })}`)
  const flavors = ['DP', 'PP', 'MPB', 'GC', 'DB']
  return (
    <Section id="shift" label="Plan vs reality" code="C15" tone="surface" title="Depots selling differently from the supply plan"
      lead="Allocations are reset monthly on last year's mix. This compares each depot's share of recent demand (corrected for stockout days) with its share of this month's allocation. Positive: demand is bigger than the plan gives it."
      >
      <Gate q={q} what="the plan-versus-demand shift">{(d) => {
        const get = (f: string, dp: string) => d.rows.find((r: any) => r.flavor === f && r.depot === dp)
        const flagged = d.rows.filter((r: any) => r.flag !== 'IN_LINE').sort((a: any, b: any) => Math.abs(b.shift_pp) - Math.abs(a.shift_pp)).slice(0, 8)
        return (
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-8 overflow-x-auto lg:sticky lg:top-44 lg:self-start">
              <table className="w-full border-separate" style={{ borderSpacing: 3, minWidth: 720 }}>
                <thead><tr><th />{(meta?.depots || []).map((x: any) => <th key={x.id} className="label font-normal text-center pb-1">{x.name}</th>)}</tr></thead>
                <tbody>{flavors.map((f) => <tr key={f}><th scope="row" className="text-left font-medium text-sm pr-2">{(meta?.flavors || []).find((x: any) => x.id === f)?.name}</th>
                  {(meta?.depots || []).map((x: any) => { const c = get(f, x.id); if (!c) return <td key={x.id} />
                    const tone = c.flag === 'UNDER_SUPPLIED' ? 'st-red' : c.flag === 'OVER_SUPPLIED' ? 'st-amber' : 'st-neutral'
                    return <td key={x.id} className="p-0"><button className={`w-full py-2 text-center rounded-sm num text-sm ${tone}`} onClick={() => set(f, x.id)} aria-label={`${c.flavor_name} at ${c.depot_name}: demand share ${(c.demand_share * 100).toFixed(1)}%, planned ${(c.allocation_share * 100).toFixed(1)}%`}>{c.shift_pp > 0 ? '+' : ''}{c.shift_pp.toFixed(1)}<span className="text-[0.65rem]"> pp</span></button></td> })}</tr>)}</tbody></table>
              <div className="mt-3 flex flex-wrap gap-4 text-sm"><Pill tone="red">Under-supplied</Pill><span className="text-soft">demand share ≥ 3 points and 20% above plan</span><Pill tone="amber">Over-supplied</Pill><span className="text-soft">≥ 3 points and 20% below plan</span></div>
            </div>
            <div className="lg:col-span-4">
              <div className="label label-strong mb-2">Largest gaps</div>
              {flagged.length === 0 ? <p className="text-soft">Every depot is in line with its allocation.</p> : <ul className="space-y-2 text-sm">{flagged.map((r: any, i: number) => <li key={i}><b>{r.flavor_name}</b> at {r.depot_name}: demand {(r.demand_share * 100).toFixed(1)}% vs plan {(r.allocation_share * 100).toFixed(1)}% <Pill tone={r.flag === 'UNDER_SUPPLIED' ? 'red' : 'amber'}>{r.flag === 'UNDER_SUPPLIED' ? 'short' : 'long'}</Pill></li>)}</ul>}
              <p className="mt-4"><TrustTags prov={d.provenance} asof={asof} /></p>
            </div>
          </div>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ C16 + C17 ------------------------------ */
function DominoSurge({ flavor, depot }: { flavor: string; depot: string }) {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const dq = useApi<any>(`/api/inventory/domino${qs({ asof, flavor, depot })}`)
  const [mult, setMult] = useState(2)
  const [debounced, setDebounced] = useState(2)
  useEffect(() => { const id = setTimeout(() => setDebounced(mult), 350); return () => clearTimeout(id) }, [mult])
  const sq = useApi<any>(`/api/inventory/surge${qs({ asof, flavor, multiplier: debounced })}`)
  const kindStyle: Record<string, string> = { OBSERVED: 'bg-fg text-bg', MODEL: 'border border-fg border-dashed', INFERENCE: 'st-amber', DECISION: 'st-red' }
  return (
    <Section id="domino" label="Cause and effect" code="C16 · C17" tone="bg" title="How a sales surge empties stock and hides real demand"
      lead="Left: the chain from the first signal to a decision, with each link marked as a reported fact, a model estimate or something inferred. Right: drag the slider to see how fast each depot empties if demand runs at 1x to 4x.">
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-6 lg:sticky lg:top-44 lg:self-start">
          <Gate q={dq} what="the cause-and-effect chain">{(d) => (
            <ol className="relative">
              {d.chain.map((c: any, i: number) => (
                <li key={i} className="grid grid-cols-[2.4rem_1fr] gap-4 pb-6 relative">
                  {i < d.chain.length - 1 && <span className="absolute left-[1.05rem] top-9 bottom-0 w-px bg-line" aria-hidden />}
                  <span className="serif text-[1.7rem] leading-none w-9 h-9 flex items-center justify-center rounded-full border border-line bg-surface">{c.step}</span>
                  <div><div className="flex flex-wrap items-center gap-2 mb-1"><span className="font-medium">{c.title}</span><span className={`label rounded-sm px-1.5 py-0.5 ${kindStyle[c.kind]}`} style={{ color: c.kind === 'OBSERVED' ? 'var(--bg)' : undefined }}>{({ OBSERVED: 'reported fact', MODEL: 'model estimate', INFERENCE: 'inferred', DECISION: 'waiting for a person' } as any)[c.kind]}</span></div>
                    <p className="text-soft text-[0.95rem]">{c.text}</p></div>
                </li>))}
            </ol>)}</Gate>
        </div>
        <div className="lg:col-span-6">
          <div className="flex items-end justify-between"><h3 className="h-card">If demand runs at {mult.toFixed(1)}x</h3><span className="label">C17</span></div>
          <input type="range" min={1} max={4} step={0.5} value={mult} onChange={(e) => setMult(Number(e.target.value))} aria-label="Sales surge multiplier" className="my-3" />
          <div className="flex justify-between label"><span>1x</span><span>2x</span><span>3x</span><span>4x</span></div>
          <Gate q={sq} what="the surge run-out dates" h={300}>{(d) => (
            <>
              <Chart height={360} data={[
                { type: 'bar', orientation: 'h', name: 'Normal demand', y: d.rows.map((r: any) => r.depot_name), x: d.rows.map((r: any) => r.base.days_p50), marker: { color: t.slate }, hovertemplate: '%{y}: %{x:.0f} days<extra>normal</extra>' },
                { type: 'bar', orientation: 'h', name: `Surge ${d.multiplier.toFixed(1)}x`, y: d.rows.map((r: any) => r.depot_name), x: d.rows.map((r: any) => r.surge.days_p50), marker: { color: t.red }, hovertemplate: '%{y}: %{x:.0f} days<extra>surge</extra>' },
              ]} layout={{ barmode: 'group', margin: { l: 90 }, xaxis: { title: { text: 'Days until the depot runs out (expected)', font: { size: 11 } }, rangemode: 'tozero' }, yaxis: { autorange: 'reversed' } }} />
              <Note>{d.note} <TrustTags prov={d.provenance} compact /></Note>
            </>)}</Gate>
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------ C18 + C21 ------------------------------ */
function DelayAndMitigation({ flavor, depot }: { flavor: string; depot: string }) {
  const { asof, econ, setEcon } = useApp()
  const t = useChartTheme()
  const dc = useApi<any>(`/api/inventory/delay-clock${qs({ asof, economics: econ })}`)
  const mq = useApi<any>(`/api/inventory/mitigation${qs({ asof, flavor, depot, economics: econ })}`)
  return (
    <Section id="fix" label="Cost of waiting and ways to fix" code="C18 · C21" tone="surface" title="What each day of delay costs, and the fix that arrives in time"
      lead="The clock counts days from the first detectable surge to the day planners knew, and prices only what has been lost so far. The chart compares four ways to cover a shortage by when they arrive against the depot's earliest-likely stockout."
      aside={<Seg value={econ} onChange={setEcon} label="Money basis" options={[{ id: 'margin', label: 'Margin' }, { id: 'revenue', label: 'Revenue' }]} />}>
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-4 lg:sticky lg:top-44 lg:self-start">
          <Gate q={dc} what="the delay clock" h={260}>{(d) => d.active ? (
            <Card>
              <div className="label label-strong mb-2">Information delay clock</div>
              <div className="label">{d.name}: surge first flagged {dateLong(d.signal_date)}</div>
              <div className="stat num mt-2">{d.days_elapsed}<span className="text-soft text-[0.4em]"> days</span></div>
              <div className="text-sm text-soft">{d.awareness_date ? `until planners knew (${dateLong(d.awareness_date)}, as-was)` : 'and counting: planners have not been told yet in the as-was process'}</div>
              <div className="mt-5 grid grid-cols-2 gap-4"><div><div className="label">Cost per day</div><div className="stat-md num">{inrShort(d.cost_per_day)}</div></div><div><div className="label">Lost so far</div><div className="stat-md num">{inrShort(d.accrued_value_p50)}</div></div></div>
              <p className="text-xs text-soft mt-3">{d.lost_bottles_p50 ? `${n0(d.lost_bottles_p50)} bottles` : 'No lost bottles yet'} (low {n0(d.lost_bottles_p10)}, high {n0(d.lost_bottles_p90)}). {d.basis}. {d.caveat}</p>
              <p className="text-xs text-soft mt-2">{d.awareness_note}</p>
              <div className="mt-3"><TrustTags prov={d.provenance} interval={d.interval_type} asof={asof} /></div>
            </Card>) : <Card>{d.text}</Card>}</Gate>
        </div>
        <div className="lg:col-span-8">
          <Gate q={mq} what="the fix options" h={420}>{(d) => {
            const opts = d.options
            const late = (o: any) => o.arrives_too_late
            const xs = opts.map((o: any) => o.days_vs_p10_stockout ?? 12)
            const maxX = Math.max(10, ...xs.map((x: number) => Math.abs(x))) + 3
            const sizeref = Math.max(...opts.map((o: any) => o.qty), 1) / 2200
            return (
              <>
                <div className="flex flex-wrap items-center gap-3 mb-2"><span className="font-medium">{d.flavor} at {d.depot}</span><span className="text-sm text-soft">earliest-likely stockout {dateLong(d.stockout.date_p10)}, expected {dateLong(d.stockout.date_p50)}; needs about {n0(d.need_bottles)} bottles to cover {d.target_days} days</span></div>
                <Chart height={400} data={[{ type: 'scatter', mode: 'markers+text', x: xs, y: opts.map((o: any) => o.cost_per_bottle), text: opts.map((o: any) => o.label), textposition: 'top center',
                  marker: { size: opts.map((o: any) => Math.max(o.qty, d.need_bottles * 0.06)), sizemode: 'area', sizeref, sizemin: 14, color: opts.map((o: any) => !o.feasible && o.key !== 'nothing' ? t.line : late(o) ? t.red : t.green), line: { color: t.fg, width: 1.5 }, opacity: 0.85 },
                  hovertemplate: opts.map((o: any) => `${o.label}<br>${o.arrival_date ? 'Arrives ' + o.arrival_date : 'Never arrives'}<br>${n0(o.qty)} bottles<br>₹${o.cost_per_bottle.toFixed(2)} a bottle<extra></extra>`) }]}
                  layout={{ showlegend: false, xaxis: { title: { text: 'Arrival vs the depot\'s earliest-likely stockout date (days; right of 0 is too late)', font: { size: 11 } }, range: [-maxX, maxX], zeroline: false },
                    yaxis: { title: { text: 'Cost per bottle, ₹', font: { size: 11 } }, rangemode: 'tozero' },
                    shapes: [{ type: 'rect', xref: 'x', yref: 'paper', x0: 0, x1: maxX, y0: 0, y1: 1, fillcolor: 'rgba(143,47,28,0.10)', line: { width: 0 }, layer: 'below' }, { type: 'line', x0: 0, x1: 0, yref: 'paper', y0: 0, y1: 1, line: { color: t.red, width: 2 } }],
                    annotations: [{ x: maxX * 0.55, y: 1, yref: 'paper', text: 'Arrives too late', showarrow: false, font: { color: t.red, size: 12 }, yanchor: 'top' }, { x: -maxX * 0.55, y: 1, yref: 'paper', text: 'Arrives in time', showarrow: false, font: { color: t.green, size: 12 }, yanchor: 'top' }] }} />
                <div className="overflow-x-auto mt-2"><table className="tbl"><thead><tr><th>Option</th><th className="r">Bottles</th><th>Arrives</th><th className="r">₹ / bottle</th><th className="r">Net value</th><th>Basis</th></tr></thead>
                  <tbody>{opts.map((o: any) => <tr key={o.key}><td className="font-medium">{o.label} {late(o) && o.key !== 'nothing' && o.qty > 0 && <Pill tone="red">Too late</Pill>}{o.qty === 0 && o.key !== 'nothing' && <Pill tone="neutral">Not available</Pill>}</td><td className="r num">{n0(o.qty)}</td><td>{o.arrival_date ? `${dateLabel(o.arrival_date)} (${o.days_vs_p10_stockout > 0 ? '+' : ''}${o.days_vs_p10_stockout} d vs P10)` : '–'}</td><td className="r num">{o.cost_per_bottle.toFixed(2)}</td><td className="r num">{o.key === 'nothing' ? <span className="text-soft">loses {inrShort(d.lost_if_nothing.mean * d.unit_value)}</span> : inrShort(o.net_value_mean)}</td><td className="text-xs text-soft max-w-xs">{o.basis}{o.notes?.length ? ' ' + o.notes[0] : ''}</td></tr>)}</tbody></table></div>
                <p className="mt-3"><TrustTags prov={d.provenance} interval={d.interval_type} asof={asof} /></p>
              </>)
          }}</Gate>
        </div>
      </div>
    </Section>
  )
}
