import { useFeatureIndex } from '../components/FeatureIndex'
import React, { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApp } from '../lib/state'
import { useApi, postJson, qs } from '../lib/api'
import { addDays, bottles, dateLabel, dateLong, inr, inrShort, n0, pct } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { Card, Details, Empty, Gate, Hero, Loading, Note, Pill, Section, Seg, Select, Labeled, TrustTags, Stat } from '../components/ui'

export default function Demand() {
  const { asof } = useApp()
  const ix = useFeatureIndex('demand', [
    { id: 'forecast', title: 'Bottles we will sell in the next 4 weeks, and the fruit needed', desc: 'Weekly sales forecast for any flavor, depot and channel with a low to high range, and the tonnes of fruit pulp the forecast needs.' },
    { id: 'qc', title: "Yesterday's quick-commerce sales against a normal day", desc: 'Each flavor sold yesterday compared with its seven day average. Empty shelves make the figure a floor.' },
    { id: 'spikes', title: 'Flavors selling far faster or slower than normal, and the money at risk', desc: 'Early alarm when a flavor runs well above or below normal, how many extra bottles that means and what it is worth.' },
    { id: 'newflavor', title: 'Forecast for a new flavor with no sales yet', desc: 'Builds a launch curve from similar past flavors and adjusts it as the first weeks of sales arrive.' },
    { id: 'calendar', title: 'Upcoming launches and promotions that will lift sales', desc: 'Known launches, promotions and influencer moments on a timeline. You can add your own marker.' },
    { id: 'accuracy', title: 'How far past forecasts were from actual sales', desc: 'Month ahead forecasts replayed for March to June and compared with the company forecast of the time.' },
  ])
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Demand"
        lead="What each flavor is selling, what it is likely to sell next, and how early we can tell when that changes. Quick-commerce data is daily; general and modern trade are monthly totals spread over the days, so their early warning comes from depot shipments." />
      {ix.panel}
      {ix.show('forecast') && <ForecastSection />}
      {ix.show('qc') && <QcMonitor />}
      {ix.show('spikes') && <SpikeSection />}
      {ix.show('newflavor') && <NewFlavor />}
      {ix.show('calendar') && <CampaignCalendar />}
      {ix.show('accuracy') && <Accuracy />}
    </>
  )
}

/* ------------------------------ B1 ------------------------------ */
function ForecastSection() {
  const { asof, meta, econ, setEcon } = useApp()
  const t = useChartTheme()
  const [flavor, setFlavor] = useState('DP')
  const [scope, setScope] = useState('ALL')
  const [channel, setChannel] = useState('ALL')
  const [reveal, setReveal] = useState(false)
  const isRegion = scope.startsWith('R-')
  const q = useApi<any>(`/api/demand/forecast${qs({ asof, flavor, channel, depot: scope !== 'ALL' && !isRegion ? scope : undefined, region: isRegion ? scope : undefined, reveal })}`)
  const unit = econ === 'margin' ? 0.32 * 60 : 60
  const money = (v: number) => inr(v * unit)
  return (
    <Section id="forecast" label="Forecast" code="B1" tone="surface" title="Bottles we will sell in the next 4 weeks, and the fruit needed"
      lead="Solid line: what was actually sold. Dashed line: the middle forecast. Shaded band: the low-to-high range. The range is a scenario range, not a measured error band."
      aside={<div className="flex flex-wrap gap-3 items-end">
        <Select label="Flavor" value={flavor} onChange={setFlavor} options={(meta?.flavors || []).map((f: any) => ({ id: f.id, label: f.name }))} />
        <Select label="Where" value={scope} onChange={setScope} options={[{ id: 'ALL', label: 'All depots' }, ...(meta?.regions || []).map((r: any) => ({ id: r.id, label: `${r.name} region` })), ...(meta?.depots || []).map((d: any) => ({ id: d.id, label: d.name }))]} />
        <Select label="Channel" value={channel} onChange={setChannel} options={[{ id: 'ALL', label: 'All channels' }, { id: 'GT', label: 'General trade' }, { id: 'MT', label: 'Modern trade' }, { id: 'QC', label: 'Quick commerce' }]} />
        <Seg value={econ} onChange={setEcon} label="Money basis" options={[{ id: 'margin', label: 'Margin' }, { id: 'revenue', label: 'Revenue' }]} />
      </div>}>
      <Gate q={q} what="the forecast" h={420}>{(d) => {
        const hist = d.history.map((h: any) => ({ x: h.week_start, y: h.actual }))
        const lastHist = hist.length ? hist[hist.length - 1].x : d.asof
        const xs = d.weekly.map((w: any) => w.start)
        const traces: any[] = [
          { x: xs, y: d.weekly.map((w: any) => w.p90), mode: 'lines', line: { width: 0 }, hoverinfo: 'skip', showlegend: false },
          { x: xs, y: d.weekly.map((w: any) => w.p10), mode: 'lines', line: { width: 0 }, fill: 'tonexty', fillcolor: t.band, name: 'Low to high range (P10 to P90)', hovertemplate: 'Low %{y:,.0f}<extra></extra>' },
          { x: xs, y: d.weekly.map((w: any) => w.p50), mode: 'lines+markers', name: 'Middle forecast (P50)', line: { color: t.bronze, width: 2.5, dash: 'dash' }, marker: { size: 6 }, hovertemplate: 'Middle %{y:,.0f}<extra></extra>' },
          { x: hist.map((h: any) => h.x), y: hist.map((h: any) => h.y), mode: 'lines+markers', name: 'Sold (reported)', line: { color: t.ink, width: 2.5 }, marker: { size: 5 }, hovertemplate: 'Sold %{y:,.0f}<extra></extra>' },
        ]
        if (d.revealed.length) traces.push({ x: d.revealed.map((h: any) => h.week_start), y: d.revealed.map((h: any) => h.actual), mode: 'lines+markers', name: 'What happened next (replay only)', line: { color: t.slate, width: 2, dash: 'dot' }, marker: { size: 5, symbol: 'diamond' } })
        return (
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-8 lg:sticky lg:top-44 lg:self-start">
              <Chart data={traces} height={380} layout={{ yaxis: { title: { text: 'Bottles per week', font: { size: 11 } } }, shapes: [{ type: 'line', x0: d.asof, x1: d.asof, yref: 'paper', y0: 0, y1: 1, line: { color: t.soft, width: 1, dash: 'dot' } }],
                annotations: [{ x: d.asof, y: 1, yref: 'paper', text: 'replay date', showarrow: false, xanchor: 'left', font: { size: 10, color: t.soft } }] }} />
              <div className="flex flex-wrap items-center gap-4 mt-1">
                <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Reveal what happened next (replay only; never used by the forecast)</label>
                <TrustTags prov={d.provenance} interval={d.interval_type} warnings={d.warnings} asof={asof} />
              </div>
              <Note>{d.grain}</Note>
            </div>
            <div className="lg:col-span-4 space-y-8">
              <div>
                <div className="label label-strong mb-2">Next weeks, whole totals</div>
                <table className="tbl"><thead><tr><th>Next</th><th className="r">Low</th><th className="r">Middle</th><th className="r">High</th></tr></thead>
                  <tbody>{d.horizons.map((h: any) => <React.Fragment key={h.horizon}>
                    <tr><td>{h.weeks} week{h.weeks > 1 ? 's' : ''}<div className="label">bottles</div></td><td className="r num">{bottles(h.p10)}</td><td className="r num font-medium">{bottles(h.p50)}</td><td className="r num">{bottles(h.p90)}</td></tr>
                    <tr><td className="label">{econ === 'margin' ? 'margin' : 'revenue'}</td><td className="r num text-soft">{money(h.p10)}</td><td className="r num text-soft">{money(h.p50)}</td><td className="r num text-soft">{money(h.p90)}</td></tr></React.Fragment>)}</tbody></table>
                <p className="mt-2 text-xs text-soft">Totals are quantiles of summed sample paths, not sums of weekly quantiles. {d.horizons[0].lower_bound_only && 'Lower bound only: many recent days were stocked out.'}</p>
              </div>
              <div>
                <div className="label label-strong mb-2">Fruit outlook, next 8 weeks</div>
                <table className="tbl"><thead><tr><th>Pulp</th><th className="r">Low</th><th className="r">Middle</th><th className="r">High</th></tr></thead>
                  <tbody>{d.pulp_outlook.map((o: any) => <tr key={o.ingredient}><td>{o.name.replace(' pulp', '').replace(' (fresh leaves, pressed on site)', '')}</td><td className="r num">{n0(o.p10 / 1000)} t</td><td className="r num">{n0(o.p50 / 1000)} t</td><td className="r num">{n0(o.p90 / 1000)} t</td></tr>)}</tbody></table>
                <p className="mt-2 text-xs text-soft">Tonnes of pulp to make all forecast bottles of every flavor (national). Demand is not capped by plant capacity here.</p>
              </div>
            </div>
          </div>
        )
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ B3 ------------------------------ */
function QcMonitor() {
  const { asof } = useApp()
  const t = useChartTheme()
  const q = useApi<any>(`/api/demand/qc-monitor${qs({ asof })}`)
  return (
    <Section id="qc" label="Daily" code="B3" tone="bg" title="Yesterday's quick-commerce sales against a normal day"
      lead="The only daily demand signal in the data. A flagged day means shelves were empty, so the sales figure is a floor, not demand.">
      <Gate q={q} what="the daily monitor">{(d) => (
        <>
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
            {d.rows.map((r: any) => {
              const ratio = r.ratio_vs_avg7
              return (
                <Card key={r.flavor}>
                  <div className="flex items-start justify-between gap-3">
                    <div><div className="h-card">{r.name}</div><div className="label mt-1">{dateLabel(r.date)}</div></div>
                    <div className="text-right"><div className="stat-md num" style={{ color: ratio > 1.5 ? 'var(--red)' : undefined }}>{ratio.toFixed(2)}x</div><div className="label">vs 7-day average</div></div>
                  </div>
                  <Chart height={120} data={[{ x: r.series.map((s: any) => s.date), y: r.series.map((s: any) => s.q), type: 'scatter', mode: 'lines', line: { color: t.ink, width: 2 }, hoverinfo: 'x+y' },
                    { x: r.series.filter((s: any) => s.censored).map((s: any) => s.date), y: r.series.filter((s: any) => s.censored).map((s: any) => s.q), mode: 'markers', marker: { color: t.red, size: 6, symbol: 'x' }, name: 'stocked out', hovertemplate: 'Stocked out: sales are a floor<extra></extra>' }]}
                    layout={{ margin: { l: 4, r: 4, t: 6, b: 18 }, showlegend: false, xaxis: { tickfont: { size: 9 }, nticks: 4 }, yaxis: { visible: false } }} />
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-soft mt-1">
                    <span>Yesterday <b className="text-fg num">{n0(r.yesterday)}</b></span><span>7-day avg <span className="num">{n0(r.avg7)}</span></span>
                    {r.avg_same_weekday_4w && <span>Same weekday, 4 wks <span className="num">{n0(r.avg_same_weekday_4w)}</span></span>}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">{r.regions.map((g: any) => <Pill key={g.region} tone={g.censored ? 'amber' : g.yesterday / Math.max(g.avg7, 1) > 1.5 ? 'red' : 'neutral'} title={g.censored ? 'stocked out: sales are a floor' : ''}>{g.region.replace('R-', '')} {n0(g.yesterday)}</Pill>)}</div>
                  {r.censored && <p className="mt-2 text-xs st-amber rounded-sm p-1.5">Shelves were empty yesterday: sales are a floor.</p>}
                </Card>
              )
            })}
          </div>
          <p className="mt-5 text-sm text-soft">{d.platform_split} <TrustTags prov={d.provenance} warnings={['UNITS_SOLD_SIGNAL']} asof={asof} /></p>
        </>)}</Gate>
    </Section>
  )
}

/* ------------------------------ B4 + B11 ------------------------------ */
function SpikeSection() {
  const { asof, meta, econ } = useApp()
  const t = useChartTheme()
  const [flavor, setFlavor] = useState<string | null>(null)
  const [rho, setRho] = useState(0)
  const q = useApi<any>(`/api/demand/spikes${qs({ asof, rho: rho > 0 ? rho : undefined })}`)
  const sel = flavor || q.data?.selected || 'DP'
  const ex = useApi<any>(`/api/demand/explainer${qs({ asof, flavor: sel })}`)
  const names: Record<string, string> = Object.fromEntries((meta?.flavors || []).map((f: any) => [f.id, f.name]))
  const mult = econ === 'margin' ? 'margin' : 'revenue'
  return (
    <Section id="spikes" label="Spike alarm" code="B4 · B11" tone="surface" title="Flavors selling far faster or slower than normal, and the money at risk"
      lead="Alerts fire at 1.5x, 2x and 3x the frozen baseline (and the mirror image for drops). Money at stake is the extra demand over the next 7, 14 and 28 days, from a mixture of three ways the surge could play out.">
      <Gate q={q} what="the spike alarm">{(d) => {
        const nat = d.rows.filter((r: any) => r.region === 'ALL')
        const row = nat.find((r: any) => r.flavor === sel)
        const regs = d.rows.filter((r: any) => r.flavor === sel && r.region !== 'ALL')
        const tier = (x: string) => x === 'CRITICAL' ? 'red' : x === 'ALERT' ? 'amber' : x === 'WATCH' ? 'neutral' : 'neutral'
        return (
          <div className="grid lg:grid-cols-12 gap-8">
            <div className="lg:col-span-5 lg:sticky lg:top-44 lg:self-start">
              <table className="tbl"><thead><tr><th>Flavor</th><th className="r">Now vs baseline</th><th>Alert</th><th>Stage</th></tr></thead>
                <tbody>{nat.map((r: any) => (
                  <tr key={r.flavor} className={`cursor-pointer ${sel === r.flavor ? 'bg-muted' : ''}`} onClick={() => setFlavor(r.flavor)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setFlavor(r.flavor)} aria-selected={sel === r.flavor}>
                    <td className="font-medium">{names[r.flavor]}</td><td className="r num">{r.censored_today ? <span title="Shelves empty today: sales are a floor">≥ </span> : ''}{r.ratio_R.toFixed(2)}x</td>
                    <td><Pill tone={tier(r.tier) as any}>{r.tier === 'NONE' ? 'Normal' : r.tier}</Pill></td><td className="text-soft text-sm">{r.stage.toLowerCase()}</td></tr>))}</tbody></table>
              <p className="mt-3 text-xs text-soft">Click a flavor to see it on the right. Ratios use units sold; on stocked-out days they are floors.</p>
              {row && regs.length > 0 && <div className="mt-6"><div className="label mb-2">{names[sel]} by region</div>
                <table className="tbl"><tbody>{regs.map((r: any) => <tr key={r.region}><td>{r.region}</td><td className="r num">{r.ratio_R.toFixed(2)}x</td><td><Pill tone={tier(r.tier) as any}>{r.tier === 'NONE' ? 'Normal' : r.tier}</Pill></td><td className="text-soft text-sm">{r.stage.toLowerCase()}</td></tr>)}</tbody></table></div>}
            </div>
            <div className="lg:col-span-7">
              <Gate q={ex} what="the plain-English line" h={120}>{(e) => (
                <>
                  <p className="serif text-[1.9rem] leading-snug">{e.line.text}</p>
                  <div className="mt-3"><TrustTags prov={['ESTIMATED']} interval={e.interval_type} warnings={e.warnings} asof={asof} /></div>
                  <Chart height={320} data={[
                    { x: e.forecast.map((f: any) => f.date), y: e.forecast.map((f: any) => f.p90), mode: 'lines', line: { width: 0 }, hoverinfo: 'skip', showlegend: false },
                    { x: e.forecast.map((f: any) => f.date), y: e.forecast.map((f: any) => f.p10), mode: 'lines', line: { width: 0 }, fill: 'tonexty', fillcolor: t.band, name: 'Low to high forecast' },
                    { x: e.forecast.map((f: any) => f.date), y: e.forecast.map((f: any) => f.p50), mode: 'lines', line: { color: t.bronze, dash: 'dash', width: 2.5 }, name: 'Middle forecast' },
                    { x: e.baseline.map((b: any) => b.date), y: e.baseline.map((b: any) => b.baseline), mode: 'lines', line: { color: t.slate, width: 1.5, dash: 'dot' }, name: 'Baseline before the surge' },
                    { x: e.history.map((h: any) => h.date), y: e.history.map((h: any) => h.q), mode: 'lines+markers', line: { color: t.ink, width: 2.5 }, marker: { size: 4, color: e.history.map((h: any) => h.censored ? t.red : t.ink) }, name: 'Sold (true, reported)' },
                  ]} layout={{ yaxis: { title: { text: 'Bottles per day, quick commerce', font: { size: 11 } } } }} />
                  <p className="text-xs text-soft">Red dots: shelves were empty, so the sold figure understates demand. The forecast is demand, not what stock allows.</p>
                </>)}</Gate>
              {row && row.extra_bottles && (
                <div className="mt-8 grid md:grid-cols-3 gap-4">
                  {['7', '14', '28'].map((h) => {
                    const x = row.extra_bottles[h]; const m = row.money[h][mult]
                    return <Card key={h} muted><div className="label">Next {h} days</div><div className="stat-md num mt-1">{bottles(x.p50, 1)}</div><div className="text-sm text-soft">extra bottles (low {bottles(x.p10, 1)}, high {bottles(x.p90, 1)})</div>
                      <div className="mt-2 num font-medium">{inr(m.p50)} <span className="text-soft font-normal text-sm">at stake</span></div></Card>
                  })}
                </div>)}
              {row?.weights && (
                <div className="mt-6">
                  <div className="label mb-2">How the surge might play out {row.shape_unknown && <Pill tone="amber" title="Event has not peaked; weights stay at the prior">shape unknown</Pill>}</div>
                  {Object.entries(row.weights).map(([k, v]: any) => <div key={k} className="flex items-center gap-3 text-sm mb-1"><span className="w-56 shrink-0">{k}</span><div className="flex-1 h-2 bg-muted rounded-sm"><div className="h-2 bg-fg rounded-sm" style={{ width: `${v * 100}%` }} /></div><span className="num w-12 text-right">{pct(v)}</span></div>)}
                  <p className="text-xs text-soft mt-1">Every scenario keeps at least 5% weight; weights move only after four days past the peak, and stocked-out days are left out of the fit.</p>
                </div>)}
              <div className="mt-8 panel p-5">
                <div className="flex flex-wrap items-center justify-between gap-3"><div className="label label-strong">Does a quick-commerce surge reach general and modern trade?</div><TrustTags prov={['ASSUMED']} compact /></div>
                <p className="text-sm text-soft mt-2">The engine learns this from depot shipments once eight overlapping weeks exist. Until then it is off (0%) and you can set an assumption. Status: <b className="text-fg">{d.pass_through.estimate.status.replace('_', ' ').toLowerCase()}</b> ({d.pass_through.estimate.weeks} of {d.pass_through.estimate.weeks_required} weeks usable).</p>
                <div className="mt-3 flex items-center gap-4"><input type="range" min={0} max={1} step={0.05} value={rho} onChange={(e) => setRho(Number(e.target.value))} aria-label="Share of the quick-commerce surge assumed to reach general and modern trade" /><span className="num w-14 text-right">{(rho * 100).toFixed(0)}%</span></div>
              </div>
            </div>
          </div>)
      }}</Gate>
    </Section>
  )
}

/* ------------------------------ B8 ------------------------------ */
const SCORES = [['ingredient', 'Ingredient overlap'], ['price', 'Price point'], ['channel', 'Channel mix'], ['season', 'Launch season'], ['promo', 'Promo intensity']]
function NewFlavor() {
  const { asof, meta } = useApp()
  const t = useChartTheme()
  const [mode, setMode] = useState<'dp' | 'new'>('dp')
  const [sim, setSim] = useState<Record<string, Record<string, number>> | null>(null)
  const [scale, setScale] = useState(1)
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [analogs, setAnalogs] = useState<string[]>(['MPB', 'GC'])
  const [launch, setLaunch] = useState('2026-09-14')
  const [name, setName] = useState('New flavor')
  const [hs, setHs] = useState<Record<string, number>>({ ingredient: 0.6, price: 0.9, channel: 0.8, season: 0.5, promo: 0.5 })
  const run = async () => {
    setBusy(true)
    try {
      const body: any = { asof, flavor: 'DP', scale }
      if (mode === 'dp' && sim) body.similarity = sim
      if (mode === 'new') body.hypothetical = { name, launch_date: launch, analogs: Object.fromEntries(analogs.map((a) => [a, hs])) }
      setRes(await postJson('/api/demand/launch', body))
    } finally { setBusy(false) }
  }
  useEffect(() => { run() }, [asof, mode]) // eslint-disable-line
  return (
    <Section id="newflavor" label="New product" code="B8" tone="bg" title="Forecast for a new flavor with no sales yet"
      lead="A new flavor borrows the shape of similar flavors: a launch template times each analog's sales in the same season last year, weighted by how similar the analog is. As real weeks arrive, the curve shifts toward them."
      aside={<Seg value={mode} onChange={setMode} label="What to model" options={[{ id: 'dp', label: 'Dragon Passion' }, { id: 'new', label: 'A new flavor' }]} />}>
      <div className="grid lg:grid-cols-12 gap-8">
        <div className="lg:col-span-4 space-y-5 lg:sticky lg:top-44 lg:self-start">
          {mode === 'new' && (<>
            <Labeled label="Name"><input className="field w-full" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} /></Labeled>
            <Labeled label="Launch date"><input type="date" className="field w-full" value={launch} onChange={(e) => setLaunch(e.target.value)} /></Labeled>
            <div><div className="label mb-1.5">Analogs</div><div className="flex flex-wrap gap-2">{(meta?.flavors || []).filter((f: any) => f.id !== 'DP').map((f: any) => <label key={f.id} className="inline-flex items-center gap-1.5 text-sm"><input type="checkbox" checked={analogs.includes(f.id)} onChange={(e) => setAnalogs(e.target.checked ? [...analogs, f.id] : analogs.filter((x) => x !== f.id))} />{f.name}</label>)}</div></div>
          </>)}
          <div>
            <div className="label mb-2">Similarity scores, 0 to 1 <TrustTags prov={['ASSUMED']} compact /></div>
            {mode === 'dp' && res?.available && res.analogs.map((a: any) => (
              <details key={a.id} className="mb-3 border border-line rounded-sm p-3" open={false}>
                <summary className="cursor-pointer flex justify-between text-sm"><span>{a.name}</span><span className="num">{a.similarity.toFixed(2)}</span></summary>
                <div className="mt-3 space-y-2">{SCORES.map(([k, lab]) => (
                  <div key={k} className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-2 text-sm"><span className="text-soft">{lab}</span>
                    <input type="range" min={0} max={1} step={0.05} value={(sim?.[a.id]?.[k] ?? a.scores[k])} onChange={(e) => setSim({ ...(sim || {}), [a.id]: { ...(sim?.[a.id] || a.scores), [k]: Number(e.target.value) } })} aria-label={`${a.name}: ${lab}`} />
                    <span className="num text-right">{(sim?.[a.id]?.[k] ?? a.scores[k]).toFixed(2)}</span></div>))}</div>
              </details>))}
            {mode === 'new' && <div className="space-y-2">{SCORES.map(([k, lab]) => (
              <div key={k} className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-2 text-sm"><span className="text-soft">{lab}</span>
                <input type="range" min={0} max={1} step={0.05} value={hs[k]} onChange={(e) => setHs({ ...hs, [k]: Number(e.target.value) })} aria-label={lab} /><span className="num text-right">{hs[k].toFixed(2)}</span></div>))}</div>}
          </div>
          <div><div className="label mb-1.5">Scale (1 = analog week-4 level)</div><div className="flex items-center gap-3"><input type="range" min={0.3} max={2} step={0.05} value={scale} onChange={(e) => setScale(Number(e.target.value))} aria-label="Launch scale" /><span className="num w-12 text-right">{scale.toFixed(2)}</span></div></div>
          <button className="btn" onClick={run} disabled={busy || (mode === 'new' && analogs.length === 0)}>{busy ? 'Calculating…' : 'Update the curve'}</button>
        </div>
        <div className="lg:col-span-8">
          {!res ? <Loading what="the launch curve" /> : !res.available ? <Empty>{res.reason}</Empty> : (
            <>
              <h3 className="h-card mb-1">{res.name}: weekly bottles from launch ({dateLabel(res.launch_date)})</h3>
              <Chart height={360} data={[
                { x: res.weeks.map((w: any) => w.start), y: res.weeks.map((w: any) => w.p90), mode: 'lines', line: { width: 0 }, showlegend: false, hoverinfo: 'skip' },
                { x: res.weeks.map((w: any) => w.start), y: res.weeks.map((w: any) => w.p10), mode: 'lines', line: { width: 0 }, fill: 'tonexty', fillcolor: t.band, name: 'Low to high' },
                { x: res.weeks.map((w: any) => w.start), y: res.weeks.map((w: any) => w.prior), mode: 'lines', line: { color: t.slate, dash: 'dot', width: 1.5 }, name: 'Analog curve before shifting' },
                { x: res.weeks.map((w: any) => w.start), y: res.weeks.map((w: any) => w.p50), mode: 'lines+markers', line: { color: t.bronze, dash: 'dash', width: 2.5 }, name: 'Forecast curve' },
                { x: res.weeks.filter((w: any) => w.observed != null).map((w: any) => w.start), y: res.weeks.filter((w: any) => w.observed != null).map((w: any) => w.observed), mode: 'lines+markers', line: { color: t.ink, width: 2.5 }, name: 'Sold so far (reported)' },
              ]} layout={{ yaxis: { title: { text: 'Bottles per week, all channels', font: { size: 11 } } } }} />
              <div className="flex flex-wrap items-center gap-3"><TrustTags prov={res.provenance} interval={res.interval_type} warnings={res.warnings} asof={asof} /></div>
              <p className="text-sm text-soft mt-3 max-w-3xl">{res.method} Analogs: {res.analogs.map((a: any) => `${a.name} (similarity ${a.similarity.toFixed(2)})`).join(' and ')}. {res.notes[0]} The curve is shifted toward observed weeks using a precision-weighted average (shift {(Object.values(res.shift)[0] as any).shift.toFixed(2)} in log terms).</p>
            </>)}
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------ B9 ------------------------------ */
function CampaignCalendar() {
  const { asof, role, bump } = useApp()
  const q = useApi<any>(`/api/demand/campaigns${qs({ asof })}`)
  const [form, setForm] = useState({ start_date: addDays(asof, 7), flavor: '', kind: 'promotion', note: '', expected_uplift: '' })
  const [bustN, setBust] = useState(0)
  const q2 = useApi<any>(`/api/demand/campaigns${qs({ asof })}`, bustN)
  const add = async () => {
    await postJson('/api/demand/campaigns', { ...form, flavor: form.flavor || null, expected_uplift: form.expected_uplift ? Number(form.expected_uplift) : null, entered_by: role, entered_asof: asof })
    setBust(bustN + 1)
  }
  const d = q2.data || q.data
  const lo = '2026-02-10', hi = '2026-07-15'
  const span = (new Date(hi).getTime() - new Date(lo).getTime())
  const x = (s: string) => `${Math.max(0, Math.min(100, ((new Date(s).getTime() - new Date(lo).getTime()) / span) * 100))}%`
  return (
    <Section id="calendar" label="Planned moments" code="B9" tone="surface" title="Upcoming launches and promotions that will lift sales"
      lead="Only moments already known on the replay date are shown. The workbook has no forward promotion calendar, so anything marketing enters here is a marker; it does not change the forecast.">
      <Gate q={{ data: d, loading: q.loading, error: q.error, reload: q.reload }} what="the calendar">{(c) => (
        <div className="grid lg:grid-cols-12 gap-8">
          <div className="lg:col-span-8 lg:sticky lg:top-44 lg:self-start">
            <div className="relative h-40 border-y border-line" role="img" aria-label="Timeline of known launches and events">
              {[0, 1, 2, 3, 4, 5].map((m) => { const dte = new Date(2026, 1 + m, 1).toISOString().slice(0, 10); return <div key={m} className="absolute top-0 bottom-0 border-l border-line" style={{ left: x(dte) }}><span className="label absolute -top-6 left-1">{new Date(2026, 1 + m, 1).toLocaleString('en-GB', { month: 'short' })}</span></div> })}
              <div className="absolute top-0 bottom-0 w-0.5 bg-fg" style={{ left: x(asof) }}><span className="label label-strong absolute -bottom-6 -translate-x-1/2 whitespace-nowrap">replay date</span></div>
              {c.events.map((e: any, i: number) => (
                <div key={e.id} className="absolute" style={{ left: x(e.date), top: `${12 + (i % 4) * 26}px` }}>
                  <span className={`block w-3 h-3 rotate-45 ${e.status === 'upcoming' ? 'bg-fg' : 'bg-soft opacity-60'}`} title={`${dateLabel(e.date)}: ${e.description}`} />
                  <span className="label absolute left-4 -top-1 whitespace-nowrap" style={{ color: e.status === 'upcoming' ? 'var(--fg)' : undefined }}>{e.kind}</span>
                </div>))}
              {c.entered.map((e: any, i: number) => <div key={e.id} className="absolute" style={{ left: x(e.start_date), top: `${120 - (i % 2) * 20}px` }}><span className="block w-3 h-3 rounded-full border-2 border-fg bg-surface" title={`${e.kind}: ${e.note || ''}`} /><span className="label absolute left-4 -top-1 whitespace-nowrap">{e.kind}</span></div>)}
            </div>
            <table className="tbl mt-10"><thead><tr><th>Date</th><th>What</th><th>Known from</th><th>Status</th></tr></thead>
              <tbody>{[...c.events.map((e: any) => ({ d: e.date, w: e.description, k: e.known_from, s: e.status, src: 'workbook' })), ...c.entered.map((e: any) => ({ d: e.start_date, w: `${e.kind}${e.note ? ': ' + e.note : ''}`, k: e.entered_asof, s: e.status, src: 'entered here' }))]
                .sort((a, b) => a.d.localeCompare(b.d)).map((r: any, i: number) => <tr key={i}><td className="num whitespace-nowrap">{dateLabel(r.d)}</td><td>{r.w}</td><td className="num">{dateLabel(r.k)}</td><td><Pill tone={r.s === 'upcoming' ? 'ink' : 'neutral'}>{r.s}</Pill> <span className="label">{r.src}</span></td></tr>)}</tbody></table>
            <p className="mt-3 text-sm text-soft">{c.note}</p>
          </div>
          <div className="lg:col-span-4">
            <Card muted>
              <div className="label label-strong mb-3">Add a campaign marker</div>
              <div className="space-y-3">
                <Labeled label="Starts"><input type="date" className="field w-full" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Labeled>
                <Select label="Kind" value={form.kind} onChange={(v) => setForm({ ...form, kind: v })} options={['promotion', 'influencer', 'launch', 'price', 'other'].map((k) => ({ id: k, label: k }))} />
                <Labeled label="Note (short)"><input className="field w-full" maxLength={120} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></Labeled>
                <button className="btn" onClick={add}>Add marker</button>
                <p className="text-xs text-soft">Saved in the local demo database. No uplift is applied: the workbook gives no basis for one.</p>
              </div>
            </Card>
          </div>
        </div>)}</Gate>
    </Section>
  )
}

/* ------------------------------ B10 ------------------------------ */
function Accuracy() {
  const t = useChartTheme()
  const [view, setView] = useState<'depot' | 'flavor'>('depot')
  const { meta } = useApp()
  const q = useApi<any>('/api/demand/accuracy')
  const [poll, setPoll] = useState(0)
  useEffect(() => { const id = setInterval(() => setPoll((p) => p + 1), 3000); return () => clearInterval(id) }, [])
  const dn: Record<string, string> = Object.fromEntries([...(meta?.depots || []), ...(meta?.flavors || [])].map((x: any) => [x.id, x.name]))
  return (
    <Section id="accuracy" label="How good are the forecasts" code="B10" tone="bg" title="How far past forecasts were from actual sales"
      lead="Month-ahead forecasts replayed from the first day of each month using only data that had arrived. The engine is compared with the company's own forecast of the time. A bar above zero means the forecast was too high.">
      <Gate q={q} what="the accuracy scorecard">{(d) => {
        const rows = view === 'depot' ? d.summary.by_depot : d.summary.by_flavor
        const key = view === 'depot' ? 'depot' : 'flavor'
        const ids = Array.from(new Set(rows.map((r: any) => r[key]))) as string[]
        const val = (id: string, m: string) => (rows.find((r: any) => r[key] === id && r.model === m)?.bias ?? 0) * 100
        const ord = d.summary.models.filter((m: any) => m.stratum === 'ordinary')
        const inj = d.injection?.status === 'done' ? d.injection.result : null
        return (
          <>
            <div className="mb-4"><Seg value={view} onChange={setView} label="Group by" options={[{ id: 'depot', label: 'By depot' }, { id: 'flavor', label: 'By flavor' }]} /></div>
            <Chart height={340} data={[
              { type: 'bar', x: ids.map((i) => dn[i] || i), y: ids.map((i) => val(i, 'company')), name: 'Company forecast of the time', marker: { color: t.slate }, hovertemplate: '%{x}: %{y:.1f}%<extra>company</extra>' },
              { type: 'bar', x: ids.map((i) => dn[i] || i), y: ids.map((i) => val(i, 'engine')), name: 'This engine (seasonal naive with growth)', marker: { color: t.ink }, hovertemplate: '%{x}: %{y:.1f}%<extra>engine</extra>' },
            ]} layout={{ barmode: 'group', yaxis: { title: { text: 'Forecast minus sold, % of sold (bias)', font: { size: 11 } }, ticksuffix: '%', zeroline: true, zerolinecolor: t.fg }, margin: { b: 80 } }} />
            <p className="text-sm text-soft">Positive means the forecast overestimates; negative means it underestimates. {d.n_targets} target months (Mar to Jun 2026), {d.summary.n_ordinary} ordinary depot-flavor months scored; {d.summary.n_censored} stocked-out months are scored separately because their sales are floors.</p>
            <div className="grid lg:grid-cols-2 gap-10 mt-10">
              <div>
                <div className="label label-strong mb-2">Error by method (ordinary months)</div>
                <table className="tbl"><thead><tr><th>Method</th><th className="r">Pairs</th><th className="r" title="Sum of absolute errors over sum of sales">Error (WAPE)</th><th className="r">Bias</th><th className="r">80% range covers</th></tr></thead>
                  <tbody>{ord.map((m: any) => <tr key={m.model}><td>{{ engine: 'This engine', company: "Company's own forecast", naive: 'Last month repeated', seasonal_naive: 'Same month last year' }[m.model as string]}</td><td className="r num">{m.n}</td><td className="r num">{pct(m.wape, 1)}</td><td className="r num">{pct(m.scaled_bias, 1)}</td><td className="r num">{m.coverage80 != null ? pct(m.coverage80, 0) : '–'}</td></tr>)}</tbody></table>
                <p className="text-xs text-soft mt-2">The company's 30% error in the brief shows up here as {pct(ord.find((m: any) => m.model === 'company')?.wape, 0)}. The engine's scenario range covers nearly everything, which means it is wider than needed until it is calibrated on its own backtest errors (needs 30 pairs per cell).</p>
              </div>
              <div>
                <div className="label label-strong mb-2">Proposed acceptance checks (measured on this synthetic data)</div>
                <table className="tbl"><tbody>{d.acceptance.map((a: any, i: number) => <tr key={i}><td>{a.criterion}</td><td className="r num whitespace-nowrap">{a.value != null ? (Math.abs(a.value) < 2 ? pct(a.value, 1) : a.value) : '–'}</td><td>{a.status.startsWith('NOT') ? <Pill tone="amber" title={a.status}>Not met</Pill> : a.pass ? <Pill tone="green">Pass</Pill> : <Pill tone="red">Fail</Pill>}</td></tr>)}</tbody></table>
                <p className="text-xs text-soft mt-2">Criteria are proposals from the spec. Results validate the code and the process, not real-world accuracy: re-run on company data before relying on them.</p>
              </div>
            </div>
            <div className="mt-12">
              <div className="label label-strong mb-2">Spike detection test: injected surges and drops into calm series</div>
              {!inj ? <Loading what="the detection test" h={80} /> : (
                <div className="grid lg:grid-cols-2 gap-8">
                  <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>Kind</th><th>Ramp length</th><th>Size</th><th className="r">Median delay</th><th className="r">Slowest 10%</th><th className="r">Missed</th></tr></thead>
                    <tbody>{inj.rows.filter((r: any) => (r.kind === 'ramp' && [1.5, 2, 4].includes(r.peak)) || (r.kind === 'drop' && r.peak !== 0.67)).map((r: any, i: number) => <tr key={i}><td>{r.kind === 'ramp' ? 'Surge' : 'Drop'}</td><td className="num">{r.length_days} d</td><td className="num">{r.peak}x</td><td className="r num">{r.median_delay_days != null ? `${r.median_delay_days} d` : '–'}</td><td className="r num">{r.p90_delay_days != null ? `${r.p90_delay_days.toFixed(0)} d` : '–'}</td><td className="r num">{pct(r.miss_rate, 0)}</td></tr>)}</tbody></table></div>
                  <div>
                    <p className="text-soft text-sm">Delay is days from the start of the injected change to the first ALERT, counting the one-day feed lag. On the same calm stretches without injection, the detector opened {inj.false_alarm_events} false events in {n0(inj.series_days)} series-days ({inj.false_alarms_per_100_series_days.toFixed(2)} per 100). Scaled to 20 series over a summer that is about {inj.summer_equivalent_events_20_series_122_days.toFixed(1)} per summer.</p>
                    {d.injection_acf?.status === 'done' && <p className="text-soft text-sm mt-3">With lag-1 autocorrelated noise (0.3) added: {d.injection_acf.result.false_alarm_events} false events, {d.injection_acf.result.false_alarms_per_100_series_days.toFixed(2)} per 100 series-days.</p>}
                    <p className="text-soft text-sm mt-3">Tuned values on this feed: noise floor {inj.params.sigma_e_floor}, threshold {inj.params.cusum_h}, first {inj.params.month_boundary_guard_days} days of a month held (monthly volumes step at month boundaries in this data), events open only beyond a {inj.params.event_min_ratio}x move.</p>
                  </div>
                </div>)}
            </div>
          </>
        )
      }}</Gate>
    </Section>
  )
}
