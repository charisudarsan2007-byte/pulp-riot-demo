import React from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../lib/state'
import { useApi, qs } from '../lib/api'
import { dateLong, inr, inrShort } from '../lib/format'
import { Chart, useChartTheme } from '../lib/plot'
import { Card, Details, Gate, Hero, Note, Section, Seg, Stat, TrustTags } from '../components/ui'

export default function Money() {
  const { asof, econ, setEcon } = useApp()
  const t = useChartTheme()
  const { meta } = useApp()
  const fl: Record<string, string> = Object.fromEntries((meta?.flavors || []).map((x: any) => [x.id, x.name]))
  const dp: Record<string, string> = Object.fromEntries((meta?.depots || []).map((x: any) => [x.id, x.name]))
  const KIND: Record<string, string> = { fefo_redirect: 'Redirect the oldest stock', copacker: 'Order from the co-packer', plant: 'Ship from plant stock', pulp_buffer: 'Buy extra pulp', transfer: 'Move stock between depots', air_freight: 'Air freight pulp' }
  const lineName = (id: string) => { const [k, f, p] = id.split(':'); return `${KIND[k] || k.replace(/_/g, ' ')}: ${fl[f] || f}${p && p !== '-' ? ' at ' + (dp[p] || p) : ''}` }
  const q = useApi<any>(`/api/finance/waterfall${qs({ asof, economics: econ })}`)
  return (
    <>
      <Hero kicker={`Replay of ${dateLong(asof)}`} title="Money at stake"
        lead="What it costs to do nothing about the current recommendations, what acting costs, and the rupees saved. Only recommended actions are counted, each once.">
        <Gate q={q} what="the money summary" h={140}>{(d) => (
          <div className="grid grid-cols-2 gap-6">
            <Stat label="If nobody acts" value={inrShort(d.do_nothing.total)} tone="red" sub="stockouts, write-offs and air freight" tags={<TrustTags prov={d.provenance} interval="SCENARIO" compact />} />
            <Stat label="Estimated rupees saved" value={inrShort(d.net_saved)} tone="green" sub="after the cost of acting" tags={<TrustTags prov={['ESTIMATED']} compact />} />
          </div>)}</Gate>
      </Hero>
      <Section id="waterfall" label="Do nothing against act" code="F2 · F4" tone="surface" title="Money lost to stockouts and delays, and what each fix saves"
        lead="Red bars are what happens if no recommendation is approved. Green bars are what the recommended actions avoid, and the amber bars what they cost. The last bar is the net."
        aside={<Seg value={econ} onChange={setEcon} label="Money basis" options={[{ id: 'margin', label: 'Profit margin (32%)' }, { id: 'revenue', label: 'Gross revenue' }]} />}>
        <Gate q={q} what="the waterfall" h={420}>{(d) => {
          const a = d.actions, n = d.do_nothing
          const steps = [
            { label: 'Stockout lost ' + (econ === 'margin' ? 'margin' : 'revenue'), v: n.stockout_lost_value, kind: 'rel', color: t.red },
            { label: 'Overstock write-offs and discounts', v: n.overstock_writeoff, kind: 'rel', color: t.red },
            { label: 'Emergency air freight (premium)', v: n.emergency_air_freight, kind: 'rel', color: t.red },
            { label: 'Total if nobody acts', v: 0, kind: 'total', color: t.ink },
            { label: 'Losses the actions avoid', v: -a.loss_avoided, kind: 'rel', color: t.green },
            { label: 'Transfers (₹1.50 a bottle)', v: a.transfer_cost, kind: 'rel', color: t.amber },
            { label: 'Plant freight', v: a.plant_freight, kind: 'rel', color: t.amber },
            { label: 'Co-packer premium (+25%)', v: a.copacker_premium, kind: 'rel', color: t.amber },
            { label: 'Cost with actions', v: 0, kind: 'total', color: t.ink },
          ]
          return (
            <div className="grid lg:grid-cols-12 gap-8">
              <div className="lg:col-span-8">
                <Chart height={440} data={[{ type: 'waterfall', orientation: 'v', x: steps.map((s) => s.label), measure: steps.map((s) => s.kind === 'total' ? 'total' : 'relative'), y: steps.map((s) => s.v),
                  text: steps.map((s) => s.kind === 'total' ? '' : inrShort(s.v)), textposition: 'outside', connector: { line: { color: t.line } },
                  increasing: { marker: { color: t.red } }, decreasing: { marker: { color: t.green } }, totals: { marker: { color: t.ink } }, hovertemplate: '%{x}<br>%{y:,.0f}<extra></extra>' }]}
                  layout={{ yaxis: { title: { text: '₹', font: { size: 11 } }, tickformat: '.2s' }, xaxis: { tickangle: -25 }, margin: { b: 130, l: 90 }, showlegend: false }} />
                <p className="text-sm text-soft">Net rupees saved = do-nothing loss − (loss that remains + cost of acting) = <b className="text-fg">{inr(d.net_saved)}</b> ({d.unit_note}).</p>
              </div>
              <div className="lg:col-span-4 space-y-5">
                <Card muted>
                  <table className="tbl"><tbody>
                    <tr><td>Do-nothing loss</td><td className="r num">{inr(n.total)}</td></tr><tr><td>Loss avoided by actions</td><td className="r num">{inr(a.loss_avoided)}</td></tr>
                    <tr><td>Cost of acting</td><td className="r num">{inr(a.mitigation_total)}</td></tr><tr><td>Loss that remains</td><td className="r num">{inr(d.residual_loss)}</td></tr>
                    <tr><td className="font-medium">Net saved</td><td className="r num font-medium">{inr(d.net_saved)}</td></tr></tbody></table>
                </Card>
                <p className="text-sm text-soft">{d.count} recommendation{d.count === 1 ? '' : 's'} counted. See them and decide on the <Link className="underline underline-offset-4" to="/decisions">Decisions</Link> page.</p>
                <TrustTags prov={d.provenance} interval="SCENARIO" asof={asof} />
              </div>
              <div className="lg:col-span-12">
                <Details summary="how this is calculated">
                  <p>{d.formula}.</p>
                  {d.notes.map((x: string) => <p key={x}>{x}</p>)}
                  <p>Three kinds of number are never mixed here: facts from the case (₹6 crore write-offs, ₹3.2 crore air freight, 8% of summer revenue), results measured in the synthetic replay, and model estimates like these. The replay page lines them up side by side.</p>
                </Details>
                {d.lines.length > 0 && (
                  <div className="mt-6 overflow-x-auto"><table className="tbl"><thead><tr><th>Recommendation</th><th className="r">Loss if nobody acts</th><th className="r">Avoided</th><th className="r">Cost of acting</th></tr></thead>
                    <tbody>{d.lines.map((l: any) => <tr key={l.id}><td>{lineName(l.id)}</td><td className="r num">{inrShort(l.do_nothing_loss)}</td><td className="r num">{inrShort(l.avoided)}</td><td className="r num">{inrShort(l.cost)}</td></tr>)}</tbody></table></div>)}
              </div>
            </div>)
        }}</Gate>
      </Section>
    </>
  )
}
