import { useFeatureIndex } from '../components/FeatureIndex'
import React, { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Printer } from 'lucide-react'
import { useApp } from '../lib/state'
import { useApi, qs } from '../lib/api'
import { bottles, dateLabel, dateLong, inr, inrShort, n0, roleName } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { Card, Details, Gate, Hero, Labeled, Note, Pill, Section, Seg, Select, Stat, Status, TrustTags, Loading } from '../components/ui'
import { AskPanel } from '../components/Shell'

const TYPE_LABEL: Record<string, string> = {
  reforecast: 'Re-forecast', rebalance: 'Move stock between depots', plant: 'Ship from plant stock', copacker: 'Co-packer order', fefo_redirect: 'Sell oldest lots first',
  air_freight: 'Air freight for pulp', pulp_buffer: 'Pulp buffer', mix_shift: 'Change production mix', allocation_reset: 'Reset allocation', no_option: 'No option can deliver',
}

export default function ControlTower() {
  const { asof, econ, setEcon, meta } = useApp()
  const ct = useApi<any>(`/api/control-tower${qs({ asof, economics: econ })}`)
  const ix = useFeatureIndex('control', [
    { id: 'ask', title: 'Ask the agent', desc: 'Type a question such as when a flavor runs out at a depot. The answer shows its source and how fresh the data is.' },
    { id: 'first', title: 'Five decisions waiting today, biggest money first', desc: 'The recommendations that need a person to approve or reject, ranked by the money at stake, with who may approve each.' },
    { id: 'grid', title: 'Which flavors will run out, at which depots, and how soon', desc: 'A grid of every flavor and depot coloured by stockout risk over the next days. Red lines need action first.' },
    { id: 'pulse', title: 'Where supply is getting stuck between fruit and shelf', desc: 'One flow from suppliers and plants to depots and channels. Wide lines are high volume; red nodes are blocked or empty.' },
    { id: 'moves', title: 'Stock moves that prevent a stockout today', desc: 'Depot to depot transfers that are possible today, with cost and landing date, plus a quick stock check for any flavor and depot.' },
    { id: 'yesterday', title: "Yesterday's quick-commerce sales, old stock and pulp left", desc: 'Yesterday against a normal day for each flavor, stock close to its sell-by limit, and days of fruit pulp left.' },
  ])
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Control tower"
        lead="Where stock, demand and supply stand today, what needs a decision first, and how sure we are. Everything below is worked out from data that had arrived by 12:00 on the replay date.">
        <Gate q={ct} what="the summary" h={180}>{(d) => (
          <div className="grid grid-cols-3 gap-6">
            <Stat label="Depot lines in the red" value={<>{d.stock.red}<span className="text-soft text-[0.5em]"> /{d.stock.cells}</span></>} tone={d.stock.red > 0 ? 'red' : 'green'}
              sub="expected to run out inside a week" tags={<TrustTags prov={['ESTIMATED']} interval={d.interval_type} compact />} />
            <Stat label="Decisions waiting" value={d.approvals.pending} sub={`${d.approvals.escalated} escalated, ${d.approvals.overdue} past deadline`} tags={<TrustTags prov={['CALCULATED']} compact />} />
            <Stat label="Passionfruit pulp left" value={(() => { const pf = d.pulp.find((p: any) => p.ingredient === 'ING_PF'); return pf?.runway_days != null ? `${pf.runway_days.toFixed(0)} d` : '–' })()}
              tone={(() => { const pf = d.pulp.find((p: any) => p.ingredient === 'ING_PF'); return pf?.runway_days != null && pf.runway_days < 24 ? 'amber' : undefined })()}
              sub={(() => { const pf = d.pulp.find((p: any) => p.ingredient === 'ING_PF'); return `Highway ${pf?.route?.status || 'open'}` })()} tags={<TrustTags prov={['CALCULATED']} compact />} />
          </div>
        )}</Gate>
      </Hero>

      {ix.panel}
      {ix.show('ask') && (
      <div className="container-nds pb-10">
        <div className="panel p-5 md:p-7">
          <div className="flex items-center justify-between mb-4"><div><span className="label label-strong">Ask the agent</span> <span className="label border border-line rounded-sm px-1.5 ml-2" title="Feature A1">A1</span></div>
            <span className="label hidden md:inline">Answers come from the same engine functions as the screens, with source and freshness. Press / anywhere.</span></div>
          <AskPanel />
        </div>
      </div>

      )}

      {ix.show('first') && <FirstThings />}
      {ix.show('grid') && <RiskGrid />}
      {ix.show('pulse') && <Pulse />}
      {ix.show('moves') && <MovesAndCheck />}
      {ix.show('yesterday') && <Yesterday />}
    </>
  )
}

/* ------------------------------ A6 + A3 headline ------------------------------ */
function FirstThings() {
  const { asof, econ, setEcon } = useApp()
  const ct = useApi<any>(`/api/control-tower${qs({ asof, economics: econ })}`)
  return (
    <Section id="first" label="Today" code="A6" tone="surface" title="Five decisions waiting today, biggest money first"
      lead="Ranked by money at stake. Each is a recommendation waiting for a named person; nothing is done until they decide."
      aside={<Seg value={econ} onChange={setEcon} label="Money basis" options={[{ id: 'margin', label: 'Profit margin' }, { id: 'revenue', label: 'Revenue' }]} />}>
      <Gate q={ct} what="the ranked actions">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-8 lg:sticky lg:top-44 lg:self-start">
            {d.top_actions.length === 0 && <Card>No open recommendation at this date.</Card>}
            <ol className="divide-y divide-[var(--line)] border-y border-line">
              {d.top_actions.map((a: any, i: number) => (
                <li key={a.id} className="py-5 grid grid-cols-[2.2rem_1fr_auto] gap-x-4 gap-y-2 items-start">
                  <span className="serif text-[2rem] leading-none text-soft">{i + 1}</span>
                  <div>
                    <div className="flex flex-wrap items-center gap-2 mb-1.5">
                      <Pill tone="neutral">{TYPE_LABEL[a.type] || a.type}</Pill>
                      <Pill tone="neutral" title="Who may approve">Approver: {roleName(a.approver_role)}</Pill>
                      <Pill tone={a.escalation?.deadline_passed ? 'red' : 'amber'}>{a.escalation?.deadline_passed ? 'Deadline passed' : 'Decide by'} {dateLabel(a.deadline)}</Pill>
                      <Pill tone="ink">Pending</Pill>
                    </div>
                    <div className="font-medium text-[1.02rem] leading-snug">{a.title}</div>
                    <div className="text-sm text-soft mt-1 max-w-2xl">{(a.rationale || '').split('. ').slice(0, 2).join('. ')}</div>
                  </div>
                  <div className="text-right">
                    <div className="stat-md num">{inrShort(a.rank_value)}</div>
                    <div className="label mt-1">{econ === 'margin' ? 'margin at stake' : 'revenue at stake'}</div>
                    <Link to={`/decisions#${encodeURIComponent(a.id)}`} className="btn btn-ghost btn-sm mt-3">Review <ArrowRight size={14} aria-hidden /></Link>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-4 flex flex-wrap items-center gap-3"><TrustTags prov={['SYNTHETIC', 'ESTIMATED']} interval="SCENARIO" /><span className="text-sm text-soft">Amounts are scenario estimates under uncalibrated intervals, not measured savings.</span>
              <Link to="/weekly-review" className="btn btn-ghost btn-sm ml-auto"><Printer size={14} aria-hidden /> Weekly review one-pager</Link></div>
          </div>
          <div className="lg:col-span-4">
            <Card muted>
              <div className="label label-strong mb-3">Biggest demand movement</div>
              {d.headline ? (<>
                <p className="serif text-[1.65rem] leading-snug">{d.headline.text}</p>
                <div className="mt-4 flex flex-wrap gap-2"><Pill tone={d.headline.tier === 'CRITICAL' ? 'red' : 'amber'}>{d.headline.tier}</Pill><Pill tone="neutral">{String(d.headline.stage).toLowerCase()}</Pill></div>
                <div className="mt-3"><TrustTags prov={['ESTIMATED']} interval="EVENT_SCENARIO" warnings={d.headline.warnings} /></div>
                <Link to="/demand#spikes" className="inline-flex mt-5 underline underline-offset-4">Open the spike alarm</Link>
              </>) : <p className="text-soft">No flavor is moving away from its baseline right now.</p>}
            </Card>
          </div>
        </div>
      )}</Gate>
    </Section>
  )
}

/* ------------------------------ A4 heatmap ------------------------------ */
function RiskGrid() {
  const { asof, meta } = useApp()
  const nav = useNavigate()
  const q = useApi<any>(`/api/heatmap${qs({ asof })}`)
  const [sort, setSort] = useState<'region' | 'worst'>('worst')
  const [only, setOnly] = useState<'all' | 'risk'>('all')
  const [sel, setSel] = useState<any>(null)
  const names: Record<string, string> = useMemo(() => Object.fromEntries((meta?.flavors || []).map((f: any) => [f.id, f.name])), [meta])
  const dnames: Record<string, string> = useMemo(() => Object.fromEntries((meta?.depots || []).map((f: any) => [f.id, f.name])), [meta])
  return (
    <Section id="grid" label="Stock risk" code="A4" tone="bg" title="Which flavors will run out, at which depots, and how soon"
      lead="One cell per flavor and depot. The number is the earliest-likely days until the shelf is empty (the 10th percentile of the stockout day); colour and label say how urgent that is."
      aside={<div className="flex flex-wrap gap-3">
        <Seg value={sort} onChange={setSort} label="Depot order" options={[{ id: 'worst', label: 'Worst cover first' }, { id: 'region', label: 'By region' }]} />
        <Seg value={only} onChange={setOnly} label="Filter" options={[{ id: 'all', label: 'All cells' }, { id: 'risk', label: 'Red and amber' }]} />
      </div>}>
      <Gate q={q} what="the risk grid">{(d) => {
        const cells: any[] = d.cells
        const flavors = ['DP', 'PP', 'MPB', 'GC', 'DB']
        let depots: string[] = (meta?.depots || []).map((x: any) => x.id)
        if (sort === 'worst') depots = [...depots].sort((a, b) => Math.min(...cells.filter((c) => c.depot === a).map((c) => c.days_p10)) - Math.min(...cells.filter((c) => c.depot === b).map((c) => c.days_p10)))
        const get = (f: string, dp: string) => cells.find((c) => c.flavor === f && c.depot === dp)
        return (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-separate" style={{ borderSpacing: 4, minWidth: 880 }} aria-label="Risk by flavor and depot">
                <thead><tr><th className="text-left label w-44">Flavor</th>{depots.map((dp) => <th key={dp} className="label text-center font-normal pb-1">{dnames[dp]}</th>)}</tr></thead>
                <tbody>{flavors.map((f) => (
                  <tr key={f}>
                    <th scope="row" className="text-left font-medium pr-3 text-[0.95rem]">{names[f]}</th>
                    {depots.map((dp) => {
                      const c = get(f, dp)
                      if (!c) return <td key={dp} />
                      const dim = only === 'risk' && c.status === 'GREEN'
                      const tone = c.status === 'RED' ? 'st-red' : c.status === 'AMBER' ? 'st-amber' : 'st-green'
                      const txt = `${names[f]} at ${dnames[dp]}: ${c.label}. Earliest-likely stockout ${dateLong(c.date_p10)}, expected ${dateLong(c.date_p50)}. About ${n0(c.stock_p50)} bottles on hand today.${c.excess ? ' Excess stock.' : ''}`
                      return (
                        <td key={dp} className="p-0">
                          <button type="button" aria-label={txt} title={txt} onClick={() => setSel(c)}
                            className={`w-full rounded-sm px-2 py-2.5 text-center ${tone} ${dim ? 'opacity-25' : ''} ${sel && sel.flavor === f && sel.depot === dp ? 'outline outline-2 outline-[var(--fg)]' : ''}`}>
                            <div className="num font-medium text-[1rem]">{c.days_p10 >= 60 ? '60+' : Math.round(c.days_p10)}<span className="text-[0.7rem]"> d</span></div>
                            <div className="text-[0.66rem] uppercase tracking-wide" style={{ fontFamily: 'ui-monospace, monospace' }}>{c.status === 'RED' ? 'Stockout' : c.status === 'AMBER' ? 'Watch' : 'Covered'}{c.excess ? ' · excess' : ''}</div>
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <Status s="RED" /> <span className="text-soft">{d.rules.RED}</span>
              <Status s="AMBER" /> <span className="text-soft">{d.rules.AMBER}</span>
              <Status s="GREEN" /> <span className="text-soft">{d.rules.GREEN}</span>
            </div>
            <div className="mt-3"><TrustTags prov={['SYNTHETIC', 'ESTIMATED']} interval={d.interval_type} asof={asof} /></div>
            {sel && (
              <Card className="mt-6" muted>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div><div className="label mb-1">Selected</div><div className="h-card">{names[sel.flavor]} at {dnames[sel.depot]}</div>
                    <p className="mt-2 text-soft max-w-2xl">{sel.label}. About {n0(sel.stock_p50)} bottles on hand (nowcast). Earliest-likely stockout {dateLong(sel.date_p10)}, expected {dateLong(sel.date_p50)}.
                      {sel.runrate_cover_days != null && <> At the last four weeks' run-rate the cover is {sel.runrate_cover_days.toFixed(0)} days.</>} Last depot report {dateLabel(sel.report_date)} ({sel.report_age_days} days ago).</p></div>
                  <div className="flex gap-2"><Link className="btn" to={`/stock?flavor=${sel.flavor}&depot=${sel.depot}`}>Open the stock view</Link><button className="btn btn-ghost" onClick={() => setSel(null)}>Close</button></div>
                </div>
              </Card>
            )}
            <div className="mt-10">
              <div className="label label-strong mb-3">Key fruit pulp at the plants</div>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {d.ingredients.map((i: any) => (
                  <div key={i.ingredient + i.plant} className={`rounded-sm p-3 ${i.status === 'RED' ? 'st-red' : i.status === 'AMBER' ? 'st-amber' : 'st-green'}`}>
                    <div className="label" style={{ color: 'inherit' }}>{i.plant} · {i.name.replace(' pulp', '')}</div>
                    <div className="stat-md num mt-1">{i.runway_days != null ? i.runway_days.toFixed(0) : '–'}<span className="text-[0.9rem]"> days</span></div>
                    <div className="text-[0.8rem] mt-1">{i.blocked ? 'Highway closed. ' : ''}{n0(i.stock_kg)} kg in cold store</div>
                  </div>))}
              </div>
              <p className="text-sm text-soft mt-3">{d.rules.INGREDIENT}. <TrustTags prov={['SYNTHETIC', 'CALCULATED']} compact /></p>
            </div>
          </>
        )
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ A9 Sankey ------------------------------ */
function Pulse() {
  const { asof } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/sankey${qs({ asof })}`)
  const col = (s: string) => s === 'RED' ? t.red : s === 'AMBER' ? t.amber : t.green
  return (
    <Section id="pulse" label="Supply chain pulse" code="A9" tone="surface" title="Where supply is getting stuck between fruit and shelf"
      lead="Suppliers, plants and the co-packer, trucks, the nine depots, then the three channels. Line width is bottle volume over the last four weeks; node colour shows where a bottleneck is building.">
      <Gate q={q} what="the supply chain flow">{(d) => {
        const idx: Record<string, number> = Object.fromEntries(d.nodes.map((n: any, i: number) => [n.id, i]))
        const xs = [0.001, 0.22, 0.42, 0.66, 0.999]
        const data = [{
          type: 'sankey', arrangement: 'snap',
          node: { pad: 14, thickness: 16, line: { color: t.line, width: 0.5 }, label: d.nodes.map((n: any) => n.label + (n.status !== 'GREEN' ? (n.status === 'RED' ? '  [!]' : '  [~]') : '')),
            color: d.nodes.map((n: any) => col(n.status)), x: d.nodes.map((n: any) => xs[n.col]), customdata: d.nodes.map((n: any) => n.note),
            hovertemplate: '%{label}<br>%{customdata}<extra></extra>' },
          link: { source: d.links.map((l: any) => idx[l.source]), target: d.links.map((l: any) => idx[l.target]), value: d.links.map((l: any) => l.value),
            color: d.links.map((l: any) => l.status === 'RED' ? 'rgba(143,47,28,0.45)' : t.band), customdata: d.links.map((l: any) => `${l.unit}. ${l.basis}`),
            hovertemplate: '%{source.label} to %{target.label}<br>%{value:,.0f} (%{customdata})<extra></extra>' },
        }]
        const flagged = d.nodes.filter((n: any) => n.status !== 'GREEN')
        return (
          <>
            <Chart data={data} height={560} layout={{ margin: { l: 8, r: 8, t: 8, b: 8 } }} />
            <div className="mt-2 flex flex-wrap items-center gap-4 text-sm"><span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3" style={{ background: t.red }} /> Red: blocked or empty</span>
              <span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3" style={{ background: t.amber }} /> Amber: building</span>
              <span className="inline-flex items-center gap-2"><span className="inline-block w-3 h-3" style={{ background: t.green }} /> Green: flowing</span>
              <span className="text-soft">Marks [!] and [~] in the labels repeat the colour for those who cannot see it.</span></div>
            <div className="grid md:grid-cols-2 gap-x-10 gap-y-3 mt-6">
              {flagged.map((n: any) => <div key={n.id} className="flex items-start gap-3 text-sm"><Status s={n.status} small label={n.label} /><span className="text-soft">{n.note}</span></div>)}
            </div>
            <p className="mt-4"><TrustTags prov={['SYNTHETIC', 'OBSERVED', 'ESTIMATED']} asof={asof} /></p>
            <Note>{d.note}</Note>
          </>
        )
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ A8 + A2 ------------------------------ */
function MovesAndCheck() {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/rebalancing${qs({ asof })}`)
  const dn: Record<string, string> = Object.fromEntries((meta?.depots || []).map((x: any) => [x.id, x.name]))
  const fn: Record<string, string> = Object.fromEntries((meta?.flavors || []).map((x: any) => [x.id, x.name]))
  return (
    <Section id="moves" label="Stock moves" code="A8 · A2" tone="bg" title="Stock moves that prevent a stockout today"
      lead="Surplus depots on the left, shortage depots on the right of each arrow. A lane is shown only if the donor keeps its minimum cover and the bottles land before the recipient's earliest-likely stockout.">
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
          <Gate q={q} what="feasible lanes">{(d) => {
            const lanes = d.lanes
            const byId: Record<string, any> = Object.fromEntries(d.depots.map((x: any) => [x.id, x]))
            const traces: any[] = [{ type: 'scatter', mode: 'markers+text', x: d.depots.map((x: any) => x.x), y: d.depots.map((x: any) => x.y), text: d.depots.map((x: any) => x.name), textposition: 'top center',
              marker: { size: 13, color: t.surface, line: { color: t.fg, width: 2 } }, hoverinfo: 'text', showlegend: false, textfont: { size: 11, color: t.fg } }]
            const ann = lanes.map((l: any) => ({ x: byId[l.to].x, y: byId[l.to].y, ax: byId[l.from].x, ay: byId[l.from].y, xref: 'x', yref: 'y', axref: 'x', ayref: 'y', showarrow: true, arrowhead: 3, arrowsize: 1.2, arrowwidth: 2 + Math.min(4, l.qty / 40000), arrowcolor: t.green, standoff: 8 }))
            return (
              <>
                <Chart data={traces} height={430} layout={{ xaxis: { visible: false, range: [70, 94] }, yaxis: { visible: false, range: [10, 31], scaleanchor: 'x' }, annotations: ann, margin: { l: 8, r: 8, t: 8, b: 8 } }} />
                {lanes.length === 0 ? <Card muted className="mt-3">No feasible depot-to-depot lane today. Donors are at their minimum cover or the bottles would land after the stockout. See plant stock and the co-packer on the Stock page.</Card> : (
                  <div className="overflow-x-auto mt-3"><table className="tbl"><thead><tr><th>Flavor</th><th>From</th><th>To</th><th className="r">Bottles</th><th>Lands</th><th className="r">Cost</th><th>Recipient runs out (P10)</th></tr></thead>
                    <tbody>{lanes.map((l: any, i: number) => <tr key={i}><td>{fn[l.flavor]}</td><td>{dn[l.from]}</td><td>{dn[l.to]}</td><td className="r num">{n0(l.qty)}</td><td>{dateLabel(l.arrival)} ({l.transit_days} d)</td><td className="r num">{inr(l.cost)}</td><td>{dateLabel(l.recipient_p10)}</td></tr>)}</tbody></table></div>)}
                <p className="mt-3 text-sm text-soft">{d.found} feasible lane{d.found === 1 ? '' : 's'} found; up to five are drawn. Position of each city is geographic and schematic. <TrustTags prov={['SYNTHETIC', 'ESTIMATED']} compact /></p>
              </>
            )
          }}</Gate>
        </div>
        <div className="lg:col-span-5"><StockChecker /></div>
      </div>
    </Section>
  )
}

function StockChecker() {
  const { asof, meta } = useApp()
  const [flavor, setFlavor] = useState('DP')
  const [depot, setDepot] = useState('DEP04')
  const [channel, setChannel] = useState('ALL')
  const q = useApi<any>(`/api/stock-check${qs({ asof, flavor, depot, channel })}`)
  return (
    <Card>
      <div className="flex items-center gap-2 mb-1"><span className="label label-strong">Quick stock check</span><span className="label border border-line rounded-sm px-1.5">A2</span></div>
      <h3 className="h-card mb-4">Pick a flavor, a depot and a channel</h3>
      <div className="grid grid-cols-3 gap-3">
        <Select label="Flavor" value={flavor} onChange={setFlavor} options={(meta?.flavors || []).map((f: any) => ({ id: f.id, label: f.name }))} />
        <Select label="Depot" value={depot} onChange={setDepot} options={[{ id: 'ALL', label: 'All depots' }, ...(meta?.depots || []).map((f: any) => ({ id: f.id, label: f.name }))]} />
        <Select label="Channel" value={channel} onChange={setChannel} options={[{ id: 'ALL', label: 'All' }, { id: 'GT', label: 'General trade' }, { id: 'MT', label: 'Modern trade' }, { id: 'QC', label: 'Quick commerce' }]} />
      </div>
      <div className="mt-5">
        <Gate q={q} what="the stock check" h={200}>{(d) => (
          <div className="space-y-4">
            <div className="flex items-center gap-3"><Status s={d.status?.status || 'GREEN'} label={d.status?.label} /><TrustTags prov={d.provenance} /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><div className="label">Today (nowcast)</div><div className="stat-md num">{bottles(d.nowcast.p50)}</div><div className="text-sm text-soft">range {bottles(d.nowcast.p10)} to {bottles(d.nowcast.p90)}</div></div>
              <div><div className="label">Last depot report</div><div className="stat-md num">{bottles(d.reported)}</div><div className="text-sm text-soft">{d.report_dates.map(dateLabel).join(', ')}</div></div>
            </div>
            {d.cover && <p className="text-sm">Expected to run out around <b>{dateLong(d.cover.date_p50)}</b> (earliest-likely {dateLong(d.cover.date_p10)}). {d.cover.definition}</p>}
            <div><div className="label mb-1">Forecast ({channel === 'ALL' ? 'all channels' : channel})</div>
              <table className="tbl"><thead><tr><th>Next</th><th className="r">Low</th><th className="r">Middle</th><th className="r">High</th><th>Interval</th></tr></thead>
                <tbody>{d.forecast.map((f: any) => <tr key={f.horizon}><td>{{ '7d': '1 week', '14d': '2 weeks', '28d': '4 weeks' }[f.horizon as string]}</td><td className="r num">{bottles(f.p10)}</td><td className="r num">{bottles(f.p50)}</td><td className="r num">{bottles(f.p90)}</td><td><TrustTags prov={[]} interval={f.interval_type} grain={f.grain_tag} compact /></td></tr>)}</tbody></table></div>
            {d.spike && d.spike.stage !== 'NORMAL' && <p className="text-sm st-amber rounded-sm p-2">Demand alert in this region: running at {d.spike.ratio_R.toFixed(1)}x baseline ({d.spike.tier.toLowerCase()}).</p>}
            {d.safety && <p className="text-sm text-soft">Smart safety-stock target {bottles(d.safety.target_final)} vs the old 21-day rule {bottles(d.safety.old_rule?.target)}.</p>}
            <p className="text-xs text-soft">{d.stock_note}</p>
            {depot !== 'ALL' && <Link to={`/stock?flavor=${flavor}&depot=${depot}`} className="underline underline-offset-4 text-sm">Open the full stock view</Link>}
          </div>)}</Gate>
      </div>
    </Card>
  )
}

/* ------------------------------ A3 remainder ------------------------------ */
function Yesterday() {
  const { asof } = useApp()
  const { econ } = useApp()
  const ct = useApi<any>(`/api/control-tower${qs({ asof, economics: econ })}`)
  return (
    <Section id="yesterday" label="Yesterday" code="A3" tone="surface" title="Yesterday's quick-commerce sales, old stock and pulp left"
      lead="Quick-commerce sales arrive the next morning, so they are the earliest daily signal. Compare each flavor with its own last seven days.">
      <Gate q={ct} what="yesterday's sales">{(d) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-7 lg:sticky lg:top-44 lg:self-start">
            <table className="tbl"><thead><tr><th>Flavor</th><th className="r">Yesterday</th><th className="r">7-day average</th><th>Against average</th><th className="r">Depots out</th></tr></thead>
              <tbody>{d.qc.rows.map((r: any) => {
                const ratio = r.ratio ?? 1
                return <tr key={r.flavor}><td className="font-medium">{r.name}</td><td className="r num">{n0(r.yesterday)}</td><td className="r num">{n0(r.avg7)}</td>
                  <td><div className="flex items-center gap-2"><div className="h-2 bg-muted flex-1 rounded-sm overflow-hidden min-w-[90px]"><div className="h-2" style={{ width: `${Math.min(100, ratio * 50)}%`, background: ratio > 1.5 ? 'var(--red)' : ratio < 0.67 ? 'var(--amber)' : 'var(--fg)' }} /></div><span className="num w-14">{ratio.toFixed(2)}x</span>{r.censored && <Pill tone="amber" title="Shelves empty: sales are a floor">floor</Pill>}</div></td>
                  <td className="r num">{r.depots_out}</td></tr>
              })}</tbody></table>
            <p className="mt-3 text-sm text-soft">{d.qc.platform_split} Day shown: {dateLong(d.qc.day)}. <TrustTags prov={['SYNTHETIC', 'OBSERVED']} compact /></p>
          </div>
          <div className="lg:col-span-5 space-y-6">
            <div>
              <div className="label label-strong mb-2">Stock held too long</div>
              <p className="serif text-[2.4rem] leading-none num">{d.stock.excess}</p>
              <p className="text-sm text-soft mt-1">depot-flavor lines hold more than 45 days of cover. See the shelf-life actions in <Link className="underline underline-offset-4" to="/decisions">Decisions</Link>.</p>
            </div>
            <div>
              <div className="label label-strong mb-2">Fruit pulp at current speeds</div>
              {d.pulp.map((p: any) => (
                <div key={p.ingredient} className="mb-3">
                  <div className="flex justify-between text-sm"><span>{p.name}</span><span className="num">{p.runway_days != null ? `${p.runway_days.toFixed(0)} days` : '–'}</span></div>
                  <div className="h-2.5 bg-muted rounded-sm overflow-hidden mt-1"><div className="h-2.5" style={{ width: `${Math.min(100, ((p.runway_days || 0) / 60) * 100)}%`, background: (p.runway_days || 0) < 24 ? 'var(--amber)' : 'var(--green)' }} /></div>
                </div>))}
              <Link to="/supply#runway" className="underline underline-offset-4 text-sm">Open the supply view</Link>
            </div>
          </div>
        </div>)}</Gate>
    </Section>
  )
}
