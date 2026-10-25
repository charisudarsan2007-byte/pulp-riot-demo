import { useFeatureIndex } from '../components/FeatureIndex'
import React, { useEffect, useState } from 'react'
import { Plane, Ship, Truck, Snowflake } from 'lucide-react'
import { useApp } from '../lib/state'
import { useApi, qs } from '../lib/api'
import { bottles, dateLabel, dateLong, inr, inrShort, n0, pct, diffDays } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { Card, Details, Empty, Gate, Hero, Note, Pill, Section, Seg, Select, Status, TrustTags } from '../components/ui'

export default function Supply() {
  const { asof } = useApp()
  const ix = useFeatureIndex('supply', [
    { id: 'runway', title: 'Days of passionfruit and dragonfruit pulp left', desc: 'How long each fruit pulp lasts at the current use, against the time a closure or delay would take.' },
    { id: 'corridor', title: 'Fruit delivery routes and where they can break', desc: 'The two inbound routes, their status and what a closure would do to pulp runway.' },
    { id: 'conflict', title: 'Drinks that lose production when a shared pulp runs short', desc: 'Where two drinks use the same pulp, which one loses bottles and how many.' },
    { id: 'tradeoff', title: 'Best use of each kilo of scarce passionfruit pulp', desc: 'Compares the drinks that use passionfruit by money earned per kilo so the pulp goes where it pays most.' },
    { id: 'capacity', title: 'Extra production available and the last day to book the co-packer', desc: 'Spare plant capacity, the co-packer limit and premium, and the latest date to order so bottles arrive in time.' },
    { id: 'mix', title: 'Move bottling time to the flavors running out', desc: 'Suggested weekly shifts of line time from flavors with plenty of cover to flavors running short.' },
    { id: 'expiry', title: 'Frozen pulp close to its six month limit', desc: 'Every frozen pulp lot by age, with those near the limit flagged so they are used first.' },
  ])
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Supply"
        lead="Fruit pulp, production capacity and the two inbound routes. The plants cannot add lines in summer, the co-packer adds at most 10% at a 25% premium with 14 days notice, and passionfruit comes down one hill highway." />
      {ix.panel}
      {ix.show('runway') && <Runway />}
      {ix.show('corridor') && <Corridor />}
      {ix.show('conflict') && <Conflict />}
      {ix.show('tradeoff') && <Tradeoff />}
      {ix.show('capacity') && <Capacity />}
      {ix.show('mix') && <Mix />}
      {ix.show('expiry') && <Expiry />}
    </>
  )
}

/* ------------------------------ E1 ------------------------------ */
function Runway() {
  const { asof } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/supply/runway${qs({ asof })}`)
  return (
    <Section id="runway" label="Fruit pulp" code="E1" tone="surface" title="Days of passionfruit and dragonfruit pulp left"
      lead="Cold-store stock divided by the last four weeks' use. The red line is a repeat of last summer: a 21-day highway closure plus 3 days on the road.">
      <Gate q={q} what="the pulp runway">{(d) => {
        const crit = d.rows.filter((r: any) => r.critical)
        return (
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
              <Chart height={300} data={[
                { type: 'bar', orientation: 'h', y: d.rows.map((r: any) => r.name.replace(' (fresh leaves, pressed on site)', '')), x: d.rows.map((r: any) => r.runway_days), name: 'At last four weeks\' use', marker: { color: d.rows.map((r: any) => r.critical && r.runway_days < 24 ? t.amber : t.slate) }, hovertemplate: '%{y}: %{x:.0f} days<extra></extra>' },
                { type: 'bar', orientation: 'h', y: crit.map((r: any) => r.name), x: crit.map((r: any) => r.forecast_runway?.p50), name: 'At forecast demand (middle)', marker: { color: t.ink }, hovertemplate: '%{y}: %{x:.0f} days<extra>forecast</extra>' },
              ]} layout={{ barmode: 'group', margin: { l: 150 }, xaxis: { title: { text: 'Days of pulp', font: { size: 11 } }, rangemode: 'tozero' }, yaxis: { autorange: 'reversed' },
                shapes: [{ type: 'line', x0: 24, x1: 24, yref: 'paper', y0: 0, y1: 1, line: { color: t.red, width: 2, dash: 'dash' } }], annotations: [{ x: 24, y: 1, yref: 'paper', text: 'closure 21 d + transit 3 d', showarrow: false, xanchor: 'left', font: { size: 10, color: t.red } }] }} />
              <p className="text-sm text-soft">Pulp snapshot {dateLong(d.rows[0].snapshot_date)}. Use is derived from production (SYNTHETIC). At forecast demand is an upper bound because plants cannot add capacity. <TrustTags prov={['SYNTHETIC', 'CALCULATED']} asof={asof} /></p>
            </div>
            <div className="lg:col-span-5 space-y-5">
              {crit.map((r: any) => (
                <Card key={r.ingredient} muted>
                  <div className="flex justify-between items-start"><div><div className="label">{r.name}</div><div className="stat-md num">{n0(r.stock_kg)} kg</div></div>
                    {r.runway_days != null && <Status s={r.runway_days < 24 ? 'AMBER' : 'GREEN'} label={`${r.runway_days.toFixed(0)} days`} />}</div>
                  <div className="text-sm text-soft mt-2">{n0(r.daily_use_kg)} kg a day; inbound on order {n0(r.inbound_kg)} kg{r.inbound_blocked_by_closure ? ' (blocked by the closure)' : ''}.</div>
                </Card>))}
              <div>
                <div className="label label-strong mb-2">If the highway closed now</div>
                <table className="tbl"><thead><tr><th>Closure</th><th className="r">Days short</th><th className="r">Pulp short</th><th className="r">Air freight</th></tr></thead>
                  <tbody>{Object.values(d.exposure).map((e: any) => <tr key={e.closure_days}><td>{e.closure_days} days</td><td className="r num">{e.days_short.toFixed(0)}</td><td className="r num">{n0(e.kg_short)} kg</td><td className="r num">{inr(e.air_cost_inr)}</td></tr>)}</tbody></table>
                <p className="text-xs text-soft mt-2">Air freight at ₹400 a kg, five times normal. Last summer's bill was ₹3.2 crore.</p>
              </div>
            </div>
          </div>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ E17 ------------------------------ */
function Corridor() {
  const { asof } = useApp()
  const [hazard, setHazard] = useState<string | null>(null)
  const [closure, setClosure] = useState(21)
  const [launch, setLaunch] = useState('2027-03-01')
  const q = useApi<any>(`/api/supply/corridor${qs({ asof, hazard, closure_days: closure, launch_date: launch })}`)
  return (
    <Section id="corridor" label="Inbound routes" code="E17" tone="bg" title="Fruit delivery routes and where they can break"
      lead="Lane 1 is one hill highway with a 3-day truck, no alternative road, and an air bridge at five times the cost. Lane 2 is imported dragonfruit by sea: six to eight weeks, ordered once a quarter.">
      <Gate q={q} what="the inbound corridor">{(d) => {
        const l1 = d.lane1, l2 = d.lane2
        const hz = hazard || l1.hazard
        const tone = hz === 'RED' ? 'st-red' : hz === 'AMBER' ? 'st-amber' : 'st-green'
        const ab = l1.air_bridge
        const lo = '2026-01-01', hi = '2027-04-15'
        const sp = diffDays(hi, lo)
        const pos = (s: string) => `${Math.max(0, Math.min(100, (diffDays(s, lo) / sp) * 100))}%`
        return (
          <div className="space-y-10">
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-6">
                <div className="max-w-xl"><div className="flex items-center gap-2 mb-2"><Truck size={20} aria-hidden /><h3 className="h-card">Lane 1: hill-region passionfruit highway</h3></div>
                  <p className="text-soft text-sm">3-day road truck from the single cooperative. Closed for a week or more in {l1.history_long_closures} of the last {l1.history_years} summers on record. Live status: <b>{l1.route.status}</b>{l1.route.status === 'closed' ? ` (day ${l1.route.closure_day})` : ''}.</p></div>
                <div><div className="label mb-1.5">Hazard setting (what-if)</div>
                  <div className="inline-flex border border-line rounded-sm overflow-hidden" role="group" aria-label="Monsoon and landslide hazard">
                    {[['GREEN', 'Open'], ['AMBER', 'Monsoon risk'], ['RED', 'Landslide: closed']].map(([k, lab]) => <button key={k} aria-pressed={hz === k} onClick={() => setHazard(k)} className={`px-3 py-1.5 text-sm ${hz === k ? (k === 'RED' ? 'st-red font-medium' : k === 'AMBER' ? 'st-amber font-medium' : 'st-green font-medium') : 'bg-surface hover:bg-muted'}`}>{lab}</button>)}
                  </div>
                  {hazard && <button className="block label underline mt-1" onClick={() => setHazard(null)}>reset to live status</button>}</div>
              </div>
              <div className="mt-8 grid grid-cols-[auto_1fr_auto] items-center gap-3 text-sm">
                <div className="flex flex-col items-center"><Snowflake size={22} aria-hidden /><span className="label">cooperative</span></div>
                <div className={`relative h-10 rounded-sm flex items-center px-3 ${tone}`}>
                  <Truck size={18} aria-hidden /><div className="flex-1 mx-3 border-t-2 border-dashed" style={{ borderColor: 'currentColor' }} /><span className="font-medium">{hz === 'GREEN' ? 'Open: 3 days' : hz === 'AMBER' ? 'At risk: 3 days if it holds' : `Closed for ${closure} days`}</span>
                </div>
                <div className="flex flex-col items-center"><span className="serif text-xl">Plants</span><span className="label">PLT1 · PLT2</span></div>
              </div>
              <div className={`mt-3 grid grid-cols-[auto_1fr_auto] items-center gap-3 text-sm ${hz === 'GREEN' ? 'opacity-40' : ''}`}>
                <div className="w-14" /><div className="relative h-10 rounded-sm border border-line flex items-center px-3"><Plane size={18} aria-hidden /><div className="flex-1 mx-3 border-t-2 border-dotted border-fg" /><span><b>Air bridge</b> at ₹{l1.air_rate_per_kg}/kg, five times normal</span></div><div className="w-14" />
              </div>
              <div className="mt-6 grid md:grid-cols-4 gap-6 items-end">
                <div><div className="label mb-1.5">Closure length: {closure} days</div><input type="range" min={3} max={42} value={closure} onChange={(e) => setClosure(Number(e.target.value))} aria-label="Closure length in days" /></div>
                <div><div className="label">Runway</div><div className="stat-md num">{ab.runway_days != null ? ab.runway_days.toFixed(0) : '–'} d</div></div>
                <div><div className="label">Pulp short</div><div className="stat-md num">{n0(ab.kg_short)} kg</div></div>
                <div><div className="label">Air bridge cost</div><div className="stat-md num">{inr(ab.air_cost_inr)}</div></div>
              </div>
              <p className="text-xs text-soft mt-3">{ab.rule} <TrustTags prov={['CASE', 'SYNTHETIC', 'CALCULATED']} compact /></p>
            </Card>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-6">
                <div className="max-w-xl"><div className="flex items-center gap-2 mb-2"><Ship size={20} aria-hidden /><h3 className="h-card">Lane 2: imported dragonfruit, sea to plant</h3></div>
                  <p className="text-soft text-sm">42 to 56 days from order to plant, ordered once a quarter. A new flavor needs marketing to tell procurement at least 14 days before launch.</p></div>
                <label><span className="label block mb-1.5">Planned launch date (what-if)</span><input type="date" className="field" value={launch} onChange={(e) => setLaunch(e.target.value)} /></label>
              </div>
              <div className="relative h-28 mt-8 border-y border-line" role="img" aria-label="Quarterly import order dates and arrival dates">
                {[0, 3, 6, 9, 12, 15].map((m) => { const ds = new Date(2026, m, 1).toISOString().slice(0, 10); return <div key={m} className="absolute top-0 bottom-0 border-l border-line" style={{ left: pos(ds) }}><span className="label absolute -top-6 left-1">{new Date(2026, m, 1).toLocaleString('en-GB', { month: 'short', year: '2-digit' })}</span></div> })}
                {l2.orders.map((o: any, i: number) => (
                  <div key={o.id} className="absolute" style={{ left: pos(o.order_date), width: `calc(${pos(o.eta)} - ${pos(o.order_date)})`, top: `${14 + (i % 2) * 34}px` }}>
                    <div className="h-4 bg-muted border border-fg rounded-sm relative"><Ship size={13} className="absolute -top-0.5 right-1 bg-muted" aria-hidden /></div>
                    <div className="label whitespace-nowrap mt-0.5">{o.id}: order {dateLabel(o.order_date)} → plant {dateLabel(o.eta)}</div>
                  </div>))}
                <div className="absolute top-0 bottom-0 w-0.5 bg-fg" style={{ left: pos(asof) }}><span className="label label-strong absolute -bottom-6 -translate-x-1/2 whitespace-nowrap">replay date</span></div>
                {l2.marketing_gate && <div className="absolute top-0 bottom-0 w-0.5" style={{ left: pos(launch), background: 'var(--bronze)' }}><span className="label absolute -top-6 -translate-x-1/2 whitespace-nowrap" style={{ color: 'var(--bronze)' }}>launch</span></div>}
              </div>
              <div className="mt-10 grid md:grid-cols-3 gap-6 text-sm">
                <div><div className="label">Next quarterly order</div><div className="stat-md num">{l2.next_order ? dateLabel(l2.next_order.order_date) : '–'}</div><div className="text-soft">{l2.next_order ? `lands ${dateLabel(l2.next_order.eta)} for ${l2.next_order.covers}` : 'none after this date'}</div></div>
                {l2.marketing_gate && <>
                  <div><div className="label">Marketing must tell procurement by</div><div className="stat-md num">{dateLong(l2.marketing_gate.notice_deadline)}</div><div className="text-soft">14-day notice gate before launch</div></div>
                  <div><div className="label">Import order needed by</div><div className="stat-md num">{dateLong(l2.marketing_gate.import_order_needed_by)}</div><div className="text-soft">{l2.marketing_gate.lead_days}-day lead time; {l2.marketing_gate.covered_by_next_order ? 'next order lands in time' : 'next quarterly order lands too late'}</div></div></>}
              </div>
              <p className="text-xs text-soft mt-3">Order dates and quantities are from the import calendar (SYNTHETIC, derived from the case cadence). <TrustTags prov={['CASE', 'SYNTHETIC']} compact /></p>
            </Card>
          </div>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ E2 ------------------------------ */
function Conflict() {
  const { asof } = useApp()
  const [rule, setRule] = useState('pro_rata')
  const q = useApi<any>(`/api/supply/conflict${qs({ asof, rule, days: 28 })}`)
  return (
    <Section id="conflict" label="Shared pulp" code="E2" tone="surface" title="Drinks that lose production when a shared pulp runs short"
      lead="Passionfruit goes into Punch and Dragon Passion; dragonfruit into Bloom and Dragon Passion. Choose how a shortage is shared and see who loses volume over the next four weeks."
      aside={<Seg value={rule} onChange={setRule} label="How the shortage is shared" options={[{ id: 'pro_rata', label: 'Shared pro rata' }, { id: 'dp_first', label: 'Dragon Passion first' }, { id: 'margin_per_kg', label: 'Most bottles per kg first' }]} />}>
      <Gate q={q} what="the pulp conflict">{(d) => (
        <>
          <div className="grid md:grid-cols-3 gap-4">
            {d.rows.map((r: any) => (
              <div key={r.flavor} className={`rounded-md p-5 ${r.status === 'RED' ? 'st-red' : r.status === 'AMBER' ? 'st-amber' : 'st-green'}`}>
                <div className="flex items-center justify-between"><div className="h-card" style={{ color: 'inherit' }}>{r.flavor_name}</div><Status s={r.status} label={r.status === 'RED' ? 'Loses volume' : r.status === 'AMBER' ? 'Some loss' : 'No loss'} /></div>
                <div className="stat num mt-3" style={{ color: 'inherit' }}>{r.loss_share > 0 ? `−${(r.loss_share * 100).toFixed(0)}%` : '0%'}</div>
                <div className="text-sm mt-2">{bottles(r.bottles_lost, 1)} of {bottles(r.demand_bottles, 1)} bottles{r.binding_ingredient ? ` short of ${r.binding_ingredient === 'ING_PF' ? 'passionfruit' : 'dragonfruit'} pulp` : ''}.</div>
              </div>))}
          </div>
          <p className="mt-4 text-sm text-soft">{d.note} Highway: {d.route.status}. <TrustTags prov={d.provenance} interval="SCENARIO" asof={asof} /></p>
        </>)}</Gate>
    </Section>
  )
}

/* ------------------------------ E15 ------------------------------ */
function Tradeoff() {
  const { asof, econ, setEcon } = useApp()
  const t = useChartTheme()
  const [share, setShare] = useState(0.5)
  const q = useApi<any>(`/api/supply/tradeoff${qs({ asof, pp_share: share, economics: econ })}`)
  return (
    <Section id="tradeoff" label="Scarce passionfruit" code="E15" tone="bg" title="Best use of each kilo of scarce passionfruit pulp"
      lead="Punch uses 60 g of passionfruit per bottle; Dragon Passion uses 30 g plus dragonfruit. Slide the split to see what the same pulp is worth."
      aside={<Seg value={econ} onChange={setEcon} label="Money basis" options={[{ id: 'margin', label: 'Margin' }, { id: 'revenue', label: 'Revenue' }]} />}>
      <Gate q={q} what="the trade-off">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-5 space-y-5 lg:sticky lg:top-44 lg:self-start">
            {['PP', 'DP'].map((f) => { const r = d.rows[f]; return (
              <Card key={f} muted><div className="flex justify-between"><div className="h-card">{f === 'PP' ? 'Passionfruit Punch' : 'Dragon Passion'}</div><span className="label">{(r.kg_per_bottle * 1000).toFixed(0)} g of passionfruit a bottle</span></div>
                <div className="stat num mt-3">{inrShort(r.value_per_kg_pf)}<span className="text-soft text-[0.35em]"> per kg</span></div><div className="text-sm text-soft">{r.bottles_per_kg.toFixed(1)} bottles a kg at ₹{r.price.toFixed(0)} each</div></Card>) })}
            <p className="text-sm">Dragon Passion earns <b>{d.dp_per_kg_advantage.toFixed(1)}x</b> as much per kilogram of passionfruit{d.plan.df_binding ? ', but dragonfruit pulp limits it at this split' : ''}.</p>
            <p className="text-sm text-soft">{d.note}</p>
          </div>
          <div className="lg:col-span-7">
            <div className="flex items-center gap-4 mb-2"><span className="label">Share of {n0(d.pf_kg)} kg to Punch</span><input type="range" min={0} max={1} step={0.1} value={share} onChange={(e) => setShare(Number(e.target.value))} aria-label="Share of passionfruit pulp given to Passionfruit Punch" /><span className="num w-12 text-right">{pct(share)}</span></div>
            <Chart height={340} data={[
              { type: 'bar', x: d.sweep.map((s: any) => `${(s.pp_share * 100).toFixed(0)}%`), y: d.sweep.map((s: any) => s.pp_value), name: 'Punch', marker: { color: t.bronze } },
              { type: 'bar', x: d.sweep.map((s: any) => `${(s.pp_share * 100).toFixed(0)}%`), y: d.sweep.map((s: any) => s.dp_value), name: 'Dragon Passion', marker: { color: t.ink } },
            ]} layout={{ barmode: 'stack', xaxis: { title: { text: 'Share of passionfruit pulp given to Punch', font: { size: 11 } } }, yaxis: { title: { text: econ === 'margin' ? 'Margin earned, ₹' : 'Revenue earned, ₹', font: { size: 11 } } },
              shapes: [{ type: 'rect', xref: 'x', yref: 'paper', x0: Math.round(share * 10) - 0.5, x1: Math.round(share * 10) + 0.5, y0: 0, y1: 1, line: { width: 2, color: t.red }, fillcolor: 'rgba(0,0,0,0)' }] }} />
            <p className="text-sm">At {pct(share)} to Punch: {bottles(d.plan.pp_bottles, 1)} Punch + {bottles(d.plan.dp_bottles, 1)} Dragon Passion bottles, worth {inr(d.plan.pp_value + d.plan.dp_value)}. <TrustTags prov={d.provenance} /></p>
          </div>
        </div>)}</Gate>
    </Section>
  )
}

/* ------------------------------ E8 + E9 ------------------------------ */
function Capacity() {
  const { asof } = useApp()
  const cq = useApi<any>(`/api/supply/capacity${qs({ asof })}`)
  const dq = useApi<any>(`/api/supply/copacker-deadline${qs({ asof })}`)
  return (
    <Section id="capacity" label="Capacity" code="E8 · E9" tone="surface" title="Extra production available, and the last day to book the co-packer"
      lead="In-house extra is only the unused part of the lines. The co-packer is a separate, slower and dearer source. The clock shows the last date an order still lands before the earliest-likely stockout.">
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
          <Gate q={cq} what="capacity options">{(d) => (
            <div className="grid md:grid-cols-2 gap-5">
              <Card muted><div className="label label-strong mb-2">In-house plants</div><div className="stat num">{bottles(d.inhouse.headroom_month, 1)}</div><div className="text-sm text-soft">bottles a month of headroom ({pct(d.inhouse.utilisation, 1)} used of {bottles(d.inhouse.capacity_month, 1)})</div>
                <ul className="text-sm mt-4 space-y-1"><li>Extra cost per bottle: <b>₹0</b></li><li>Notice needed: <b>none</b>, but no new lines mid-season</li></ul><p className="text-xs text-soft mt-3">{d.inhouse.basis}</p></Card>
              <Card muted><div className="label label-strong mb-2">Co-packer</div><div className="stat num">{bottles(d.copacker.available, 1)}</div><div className="text-sm text-soft">bottles a month available (cap {bottles(d.copacker.monthly_cap, 1)}, 10% of in-house)</div>
                <ul className="text-sm mt-4 space-y-1"><li>Extra cost per bottle: <b>₹{d.copacker.extra_cost_per_bottle.toFixed(1)}</b> (+25%)</li><li>Notice needed: <b>{d.copacker.notice_days} days</b></li></ul><p className="text-xs text-soft mt-3">{d.copacker.basis}</p></Card>
            </div>)}</Gate>
        </div>
        <div className="lg:col-span-5">
          <Gate q={dq} what="the co-packer deadline">{(d) => d.headline ? (
            <Card>
              <div className="label label-strong mb-2">Co-packer deadline clock</div>
              <div className="stat num" style={{ color: d.headline.days_left < 0 ? 'var(--red)' : d.headline.days_left <= 3 ? 'var(--amber)' : undefined }}>{d.headline.days_left < 0 ? `${-d.headline.days_left} days late` : `${d.headline.days_left} days left`}</div>
              <p className="text-sm mt-2">Order by <b>{dateLong(d.headline.order_by)}</b> for {d.headline.flavor} at {d.headline.depot} to land before its earliest-likely stockout on {dateLong(d.headline.p10_stockout)}.</p>
              <div className="mt-4 max-h-44 overflow-y-auto"><table className="tbl"><tbody>{d.rows.slice(0, 8).map((r: any, i: number) => <tr key={i}><td>{r.flavor} · {r.depot}</td><td className="num">order by {dateLabel(r.order_by)}</td><td className="r"><Pill tone={r.days_left < 0 ? 'red' : r.days_left <= 3 ? 'amber' : 'green'}>{r.days_left < 0 ? 'passed' : `${r.days_left} d`}</Pill></td></tr>)}</tbody></table></div>
              <p className="text-xs text-soft mt-3">{d.rule}</p>
            </Card>) : <Card>Nothing needs a co-packer order right now.</Card>}</Gate>
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------ E10 ------------------------------ */
function Mix() {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/supply/reprioritiser${qs({ asof })}`)
  const fn: Record<string, string> = Object.fromEntries((meta?.flavors || []).map((x: any) => [x.id, x.name]))
  return (
    <Section id="mix" label="Production mix" code="E10" tone="bg" title="Move bottling time to the flavors running out"
      lead="Plants cannot add lines, so the only lever is moving bottling time from flavors with plenty of cover to those running short, within the pulp that exists. This is a recommendation for the COO, not an instruction.">
      <Gate q={q} what="the production mix">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
            <Chart height={320} data={[{ type: 'bar', orientation: 'h', y: Object.keys(d.cover_days).map((f) => fn[f]), x: Object.values(d.cover_days).map((v: any) => Math.min(v, 90)), marker: { color: Object.values(d.cover_days).map((v: any) => v < 21 ? t.red : v > 45 ? t.slate : t.green) }, hovertemplate: '%{y}: %{x:.0f} days<extra></extra>' }]}
              layout={{ margin: { l: 150 }, xaxis: { title: { text: 'Days of national cover (capped at 90)', font: { size: 11 } } }, yaxis: { autorange: 'reversed' },
                shapes: [{ type: 'line', x0: 21, x1: 21, yref: 'paper', y0: 0, y1: 1, line: { color: t.red, dash: 'dash' } }, { type: 'line', x0: 45, x1: 45, yref: 'paper', y0: 0, y1: 1, line: { color: t.slate, dash: 'dash' } }] }} />
            <p className="text-xs text-soft">Under 21 days is urgent; over 45 days has line time to give up. <TrustTags prov={d.provenance} interval="SCENARIO" asof={asof} /></p>
          </div>
          <div className="lg:col-span-5">
            <div className="label label-strong mb-2">Suggested weekly shifts</div>
            {d.moves.length === 0 ? <Empty>No shift is needed.</Empty> : (
              <table className="tbl"><thead><tr><th>Flavor</th><th>Move</th><th className="r">Bottles a week</th></tr></thead>
                <tbody>{d.moves.map((m: any, i: number) => <tr key={i}><td>{fn[m.flavor]}<div className="text-xs text-soft">{m.note || `${m.cover_days.toFixed(0)} days of cover`}</div></td><td><Pill tone={m.action === 'INCREASE' ? 'green' : 'amber'}>{m.action === 'INCREASE' ? 'Give more' : 'Take less'}</Pill>{!m.pulp_ok && <Pill tone="red" title={m.note}>pulp-limited</Pill>}</td><td className="r num">{n0(m.bottles_per_week)}</td></tr>)}</tbody></table>)}
            <p className="text-sm text-soft mt-3">Freed capacity about {bottles(d.freed_capacity_per_week, 1)} a week. Affected low-priority drinks are the ones marked "Take less". {d.rule}</p>
          </div>
        </div>)}</Gate>
    </Section>
  )
}

/* ------------------------------ E13 ------------------------------ */
function Expiry() {
  const { asof } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/supply/expiry${qs({ asof })}`)
  return (
    <Section id="expiry" label="Freezer age" code="E13" tone="surface" title="Frozen pulp close to its six month limit"
      lead="Pulp keeps up to six months in plant cold storage. Lots are flagged from 120 days old; red from 165.">
      <Gate q={q} what="the pulp lots">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
            <Chart height={320} data={[{ type: 'scatter', mode: 'markers', x: d.lots.map((l: any) => l.age_days), y: d.lots.map((l: any) => l.name.replace(' pulp', '') + ' · ' + l.plant), text: d.lots.map((l: any) => `${n0(l.qty_kg)} kg, received ${l.received}`),
              marker: { size: d.lots.map((l: any) => Math.max(8, Math.sqrt(l.qty_kg) / 6)), color: d.lots.map((l: any) => l.flag === 'RED' ? t.red : l.flag === 'AMBER' ? t.amber : t.green), line: { color: t.fg, width: 1 }, opacity: 0.85 }, hovertemplate: '%{y}<br>%{x} days old<br>%{text}<extra></extra>' }]}
              layout={{ margin: { l: 150 }, xaxis: { title: { text: 'Age in the freezer, days (limit 180)', font: { size: 11 } }, range: [0, 185] }, yaxis: { autorange: 'reversed' },
                shapes: [{ type: 'rect', xref: 'x', yref: 'paper', x0: d.flag_days, x1: 180, y0: 0, y1: 1, fillcolor: 'rgba(183,121,31,0.12)', line: { width: 0 }, layer: 'below' }] }} />
            <p className="text-xs text-soft">Bubble size is kilograms. Shaded: 120 days and older. Snapshot {dateLong(d.snapshot_date)}. <TrustTags prov={d.provenance} /></p>
          </div>
          <div className="lg:col-span-5">
            <div className="label label-strong mb-2">{d.flagged_kg > 0 ? `${n0(d.flagged_kg)} kg are 120 days old or more` : 'No lot has reached 120 days'}</div>
            <div className="max-h-72 overflow-y-auto"><table className="tbl"><thead><tr><th>Pulp</th><th>Plant</th><th className="r">Age</th><th className="r">kg</th><th>Left</th></tr></thead>
              <tbody>{d.lots.slice(0, 14).map((l: any) => <tr key={l.lot}><td>{l.name.replace(' pulp', '')}</td><td>{l.plant}</td><td className="r num">{l.age_days} d</td><td className="r num">{n0(l.qty_kg)}</td><td><Pill tone={l.flag === 'RED' ? 'red' : l.flag === 'AMBER' ? 'amber' : 'green'}>{l.days_to_expiry} d</Pill></td></tr>)}</tbody></table></div>
          </div>
        </div>)}</Gate>
    </Section>
  )
}
