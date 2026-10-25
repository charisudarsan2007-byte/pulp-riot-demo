import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useApp } from '../lib/state'
import { useApi, qs, getJson } from '../lib/api'
import { addDays, bottles, dateLabel, dateLong, diffDays, inr, inrShort, n0, pct } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { useFeatureIndex } from '../components/FeatureIndex'
import { Card, Details, Empty, Gate, Hero, Loading, Note, Pill, Section, Stat, TrustTags } from '../components/ui'

const END = '2026-06-30'

function useJob(path: string) {
  const [state, setState] = useState<any>(null)
  useEffect(() => {
    let live = true
    let timer: any
    const tick = async () => {
      try {
        const r = await fetch(path).then((x) => x.json())
        if (!live) return
        setState(r)
        if (r.status === 'running') timer = setTimeout(tick, 3000)
      } catch (e: any) { if (live) setState({ status: 'error', error: String(e.message || e) }) }
    }
    tick()
    return () => { live = false; clearTimeout(timer) }
  }, [path])
  return state
}

export default function Replay() {
  const { asof, setAsof } = useApp()
  const [playing, setPlaying] = useState(false)
  const [reveal, setReveal] = useState(false)
  const tl = useApi<any>(`/api/replay/timeline${qs({ reveal })}`)
  const [tick, setTick] = useState(0)
  useEffect(() => { if (tl.data && !tl.data.complete) { const id = setTimeout(() => tl.reload(), 5000); return () => clearTimeout(id) } }, [tl.data]) // eslint-disable-line
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => { setAsof(addDays(asof, 1) > END ? END : addDays(asof, 1)); if (asof >= END) setPlaying(false) }, 900)
    return () => clearInterval(id)
  }, [playing, asof]) // eslint-disable-line
  const rows: any[] = tl.data?.rows || []
  const upto = rows.filter((r) => r.asof <= asof && !r.error)
  const flags = useMemo(() => {
    const first = (k: string) => upto.map((r) => r[k]).find((v) => v)
    const crit = upto.find((r) => r.pf_tier_if_21d_closure === 'CRITICAL')
    return { watch: first('first_watch'), alert: first('first_alert'), pulp: crit?.asof }
  }, [upto])
  const awareness = tl.data?.awareness || '2026-04-06'
  return (
    <>
      <Hero kicker="Replay of last summer" title="Replay of last summer"
        lead="Step through the Dragon Passion surge and the 21-day highway closure as the agent would have seen them. On every date the engine sees only data that had arrived by then; what happened afterwards stays hidden unless you choose to reveal it.">
      </Hero>

      <Section id="crisis" label="Historical crisis simulator" code="H1 · H2 · H3" tone="surface" title="How early the agent would have caught last summer's shortage"
        lead="Move the replay date with the bar above, or press play. Charts stop at the replay date: no later data is used. Markers are workbook replay labels."
        aside={<div className="flex flex-wrap items-center gap-3">
          <button className="btn" onClick={() => { if (asof < '2026-03-01' || asof >= END) setAsof('2026-03-02'); setPlaying(!playing) }} aria-label={playing ? 'Pause the replay' : 'Play the replay'}>{playing ? <Pause size={16} /> : <Play size={16} />}{playing ? 'Pause' : 'Play'}</button>
          <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Show hidden synthetic truth (evaluation view)</label>
        </div>}>
        {tl.data && !tl.data.complete && <p className="text-sm text-soft mb-4"><Pill tone="amber">Preparing</Pill> Replay table: {tl.data.done} of {tl.data.total} dates computed. The charts below work for any date already computed; the rest fills in.</p>}
        <div className="space-y-8">
          <SurgeCharts asof={asof} reveal={reveal} events={tl.data?.events || []} awareness={awareness} />
          <div className="grid lg:grid-cols-2 gap-8">
                        <FlagsPanel asof={asof} flags={flags} upto={upto} awareness={awareness} />
          </div>
        </div>
        {reveal && <p className="mt-4 text-sm st-amber rounded-sm p-3">Evaluation view: grey dotted lines show hidden synthetic demand, which the engine never sees. It is there to judge the engine, not to run it.</p>}
      </Section>

      <Scenarios />
    </>
  )
}

function SurgeCharts({ asof, reveal, events, awareness }: { asof: string; reveal: boolean; events: any[]; awareness: string }) {
  const t = useChartTheme()
  const q = useApi<any>(`/api/replay/series${qs({ asof, flavor: 'DP', region: 'ALL', reveal })}`)
  return (
    <Gate q={q} what="the Dragon Passion series" h={420}>{(d) => {
      const rows = d.rows
      const x = rows.map((r: any) => r.date)
      const tierColor = (tr: string) => tr === 'CRITICAL' ? t.red : tr === 'ALERT' ? t.amber : tr === 'WATCH' ? t.bronze : t.ink
      const ev = events.filter((e) => e.date <= asof && ['influencer', 'stockout', 'route closure', 'route reopen', 'awareness (as-was)', 'air freight', 'competitor'].includes(e.type))
      const shapes = ev.map((e) => ({ type: 'line', x0: e.date, x1: e.date, yref: 'paper', y0: 0, y1: 1, line: { color: t.soft, width: 1, dash: 'dot' } }))
      const anns = ev.map((e: any, i: number) => ({ x: e.date, y: 1 - (i % 4) * 0.07, yref: 'paper', text: e.type.replace(' (as-was)', ''), showarrow: false, xanchor: 'left', font: { size: 10, color: t.soft } }))
      return (
        <div className="grid lg:grid-cols-2 gap-8">
          <div>
          <div className="label label-strong mb-1">Dragon Passion, quick-commerce bottles a day (national)</div>
          <Chart height={330} data={[
            { x, y: rows.map((r: any) => r.baseline), mode: 'lines', name: 'Baseline the engine expects', line: { color: t.slate, dash: 'dash', width: 2 } },
            { x, y: rows.map((r: any) => r.q), mode: 'lines+markers', name: 'Sold (reported, next day)', line: { color: t.ink, width: 2.5 }, marker: { size: 5, color: rows.map((r: any) => r.censored ? t.red : t.ink), symbol: rows.map((r: any) => r.censored ? 'x' : 'circle') }, hovertemplate: '%{x}<br>Sold %{y:,.0f}<extra></extra>' },
            ...(d.truth ? [{ x: d.truth.filter((p: any) => p.date <= asof).map((p: any) => p.date), y: d.truth.filter((p: any) => p.date <= asof).map((p: any) => p.demand_true), mode: 'lines', name: 'Hidden true demand (evaluation only)', line: { color: t.slate, dash: 'dot', width: 2 } }] : []),
          ]} layout={{ xaxis: { range: ['2026-03-01', END] }, yaxis: { title: { text: 'Bottles a day', font: { size: 11 } }, rangemode: 'tozero' }, shapes, annotations: anns }} />
          <p className="text-xs text-soft">Red crosses: shelves were empty at most depots, so sales are a floor, not demand. Detection uses units sold because the feed has no orders column.</p>
          </div>
          <div>
          <div className="label label-strong mb-1">Alert level: sales against baseline</div>
          <Chart height={330} data={[
            { x, y: rows.map((r: any) => r.R), mode: 'lines+markers', line: { color: t.ink, width: 1.5 }, marker: { size: 7, color: rows.map((r: any) => tierColor(r.tier)), line: { color: t.fg, width: 0.5 } }, name: 'Sales ÷ baseline', hovertemplate: '%{x}<br>%{y:.2f}x<extra></extra>' },
          ]} layout={{ showlegend: false, xaxis: { range: ['2026-03-01', END] }, yaxis: { title: { text: 'x baseline', font: { size: 11 } }, rangemode: 'tozero' },
            shapes: [1.5, 2, 3].map((v) => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v, y1: v, line: { color: t.line, dash: 'dash', width: 1 } })),
            annotations: [[1.5, 'watch 1.5x'], [2, 'alert 2x'], [3, 'critical 3x']].map(([v, s]: any) => ({ xref: 'paper', x: 1, y: v, text: s, showarrow: false, xanchor: 'right', yshift: 8, font: { size: 10, color: t.soft } })) }} />
          <p className="text-xs text-soft">Dot colour: ink normal, bronze watch, amber alert, red critical. Because empty shelves cap sales, the ratio cannot reach 3x here even though true demand did; that is the censoring problem the engine is built around.</p>
          </div>
        </div>)
    }}</Gate>
  )
}

function PulpChart({ rows, asof, events }: { rows: any[]; asof: string; events: any[] }) {
  const t = useChartTheme()
  const x = rows.map((r) => r.asof)
  const closed = rows.filter((r) => r.route_status === 'closed')
  const shapes: any[] = [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 24, y1: 24, line: { color: t.red, dash: 'dash', width: 1.5 } }]
  if (closed.length) shapes.push({ type: 'rect', xref: 'x', yref: 'paper', x0: closed[0].asof, x1: closed[closed.length - 1].asof, y0: 0, y1: 1, fillcolor: 'rgba(143,47,28,0.12)', line: { width: 0 }, layer: 'below' })
  return (
    <div>
      <div className="label label-strong mb-1">Passionfruit pulp: days of runway, and the highway</div>
      <Chart height={260} data={[{ x, y: rows.map((r) => r.pf_runway_days), mode: 'lines', line: { color: t.ink, width: 2.5 }, name: 'Runway at current use', hovertemplate: '%{x}<br>%{y:.0f} days<extra></extra>' }]}
        layout={{ showlegend: false, xaxis: { range: ['2026-03-01', END] }, yaxis: { title: { text: 'Days of passionfruit pulp', font: { size: 11 } }, rangemode: 'tozero' }, shapes,
          annotations: [{ xref: 'paper', x: 0.01, y: 24, text: '21-day closure + 3 days transit', showarrow: false, xanchor: 'left', yshift: 10, font: { size: 10, color: t.red } },
            ...(closed.length ? [{ x: closed[0].asof, y: 1, yref: 'paper', text: 'highway closed', showarrow: false, xanchor: 'left', font: { size: 10, color: t.red } }] : [])] }} />
      <p className="text-xs text-soft">Shaded: days the highway was reported closed. Pulp runway is calculated from the plant cold-store snapshot and the last four weeks' use. <TrustTags prov={['SYNTHETIC', 'CALCULATED']} asof={asof} compact /></p>
    </div>
  )
}

function FlagsPanel({ asof, flags, upto, awareness }: { asof: string; flags: any; upto: any[]; awareness: string }) {
  const st = useApi<any>(`/api/replay/state${qs({ asof })}`)
  const last = upto[upto.length - 1]
  const firstOut = st.data && Object.values(st.data.dp_first_stockouts || {}).sort()[0] as string | undefined
  const Item = ({ ok, label, date, note }: { ok: boolean; label: string; date?: string | null; note?: string }) => (
    <li className="flex gap-3 py-3 border-b border-line last:border-0"><span className={`mt-1 w-2.5 h-2.5 rounded-full shrink-0 ${ok ? 'bg-fg' : 'border border-line'}`} aria-hidden />
      <div><div className="font-medium text-[0.95rem]">{label} <span className="label ml-2">{ok && date ? dateLabel(date) : 'not yet'}</span></div>{note && <div className="text-sm text-soft">{note}</div>}</div></li>)
  return (
    <>
      <Card>
        <div className="label label-strong mb-2">Agent's view on {dateLabel(asof)}</div>
        <ul>
          <Item ok={!!flags.watch} label="Surge watch" date={flags.watch} note={last?.ratio_last_uncensored ? `Dragon Passion at ${last.ratio_last_uncensored.toFixed(1)}x baseline (last uncensored day)` : undefined} />
          <Item ok={!!flags.alert} label="Surge alert" date={flags.alert} note={flags.alert ? `${Math.max(0, diffDays(awareness, flags.alert))} days before planners noticed (${dateLabel(awareness)})` : undefined} />
          <Item ok={!!firstOut} label="First Dragon Passion stockout" date={firstOut} note={firstOut && flags.alert ? (diffDays(firstOut, flags.alert) >= 0 ? `Alert came ${diffDays(firstOut, flags.alert)} day(s) before it` : `Alert came ${-diffDays(firstOut, flags.alert)} day(s) after it`) : undefined} />
          <Item ok={!!flags.pulp} label="Pulp warning" date={flags.pulp} note={flags.pulp ? 'Runway shorter than a 21-day closure plus transit' : undefined} />
          <Item ok={st.data?.route?.status === 'closed'} label="Highway closed" date={st.data?.route?.status === 'closed' ? st.data.route.date : null} />
          <Item ok={(st.data?.air_freight_booked || 0) > 0} label="Air freight booked (as-was)" date={null} note={st.data?.air_freight_booked ? `${inr(st.data.air_freight_booked)} so far` : undefined} />
        </ul>
        <Link to={`/?asof=${asof}`} className="btn mt-4 w-full justify-center">See what the agent would recommend now</Link>
      </Card>
    </>
  )
}

/* ------------------------------ scenarios ------------------------------ */
const SCEN_ITEMS = [
  { id: 'launch', title: 'New flavor launch', desc: 'Dragon Passion launches with no sales history. Shows when the forecast was ready against the launch.' },
  { id: 'surge', title: 'Viral reel and demand surge', desc: 'The reel goes viral and sales jump. Shows when the agent raised the alert against when planners noticed, and the revenue protected if its recommendations were approved that day.' },
  { id: 'stockout', title: 'First stockouts', desc: 'Shelves start emptying at the depots. Shows how early the stockout dates were forecast.' },
  { id: 'closure', title: 'Passionfruit highway closes', desc: 'A landslide cuts the fruit route for 21 days. Shows how early the pulp shortage was flagged.' },
  { id: 'air', title: 'Air freight booked at a premium', desc: 'Air freight is booked at five times the normal rate. Shows the bill and how early the warning stood.' },
  { id: 'rival', title: 'Sales climb again as a rival launches', desc: 'A rival launches a similar drink and sales run well above baseline again. Shows when the agent flagged it and the revenue protected.' },
  { id: 'cut', title: 'Production moved to protect Dragon Passion', desc: 'Another flavor is cut to give Dragon Passion line time. Shows when the agent recommended the same shift.' },
]

function Scenarios() {
  const ix = useFeatureIndex('replay', SCEN_ITEMS, 'Revenue Saved by the Model')
  const job = useJob('/api/replay/scenarios')
  const ok = job && job.status === 'done'
  const d = ok ? job.result : null
  const byId: Record<string, any> = d ? Object.fromEntries(d.scenarios.map((x: any) => [x.id, x])) : {}
  return (
    <section id="scenarios" className="rule"><div className="container-nds section-y">
      {!job || job.status === 'running' ? <Loading what="the scenarios" h={120} /> : job.status === 'error' ? <div role="alert" className="st-red p-4 rounded-sm">{job.error}</div> : (
        <>
          {ix.panel}
          <div className="space-y-5">
            {SCEN_ITEMS.filter((i) => ix.show(i.id)).map((i) => {
              const x = byId[i.id]
              if (!x) return null
              const lead = x.lead_days
              return (
                <Card key={i.id}>
                  <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                    <h3 className="h-card">{x.title}</h3>
                    {lead != null && <Pill tone={lead >= 0 ? 'green' : 'amber'}>{lead >= 0 ? `${lead} ${lead === 1 ? 'day' : 'days'} before ${x.lead_vs}` : `${-lead} ${lead === -1 ? 'day' : 'days'} after ${x.lead_vs}`}</Pill>}
                  </div>
                  <div className="grid md:grid-cols-3 gap-6">
                    <div><div className="label mb-1">What happened</div><div className="stat-md num">{x.happened.date ? dateLabel(x.happened.date) : 'n/a'}</div><p className="text-sm text-soft mt-2">{x.happened.text}</p></div>
                    <div><div className="label mb-1">When the agent saw it</div><div className="stat-md num">{x.model.date ? dateLabel(x.model.date) : 'not flagged'}</div><p className="text-sm text-soft mt-2">{x.model.text}</p></div>
                    <div>
                      <div className="label mb-1">{x.saved ? 'Revenue protected (estimate)' : x.at_stake_inr ? 'Revenue at stake' : 'Revenue effect'}</div>
                      <div className="stat-md num" style={x.saved ? { color: 'var(--green)' } : undefined}>{x.saved ? inrShort(x.saved.net_saved) : x.at_stake_inr ? inrShort(x.at_stake_inr) : 'not estimated'}</div>
                      <p className="text-sm text-soft mt-2">{x.saved ? `${x.saved.actions} recommendations raised ${dateLabel(x.saved.date)}: ${inrShort(x.saved.loss_avoided)} of sales protected, ${inrShort(x.saved.cost_of_acting)} spent on acting, over the next 28 days.` : x.note || 'No money figure applies to this scenario.'}</p>
                    </div>
                  </div>
                </Card>)
            })}
          </div>
          <p className="text-xs text-soft mt-6 max-w-3xl">{d.note}</p>
        </>)}
    </div></section>
  )
}
