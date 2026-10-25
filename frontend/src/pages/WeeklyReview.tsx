import React from 'react'
import { Link } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { useApp } from '../lib/state'
import { useApi, qs } from '../lib/api'
import { bottles, dateLabel, dateLong, inr, inrShort, n0, roleName } from '../lib/format'
import { Gate, Pill, TrustTags } from '../components/ui'

export default function WeeklyReview() {
  const { asof, econ } = useApp()
  const q = useApi<any>(`/api/weekly-review${qs({ asof, economics: econ })}`)
  return (
    <div className="container-nds print-page py-10 max-w-5xl">
      <div className="flex items-center justify-between no-print mb-8">
        <Link to="/" className="underline underline-offset-4">← Back to the control tower</Link>
        <button className="btn" onClick={() => window.print()}><Printer size={16} aria-hidden /> Print or save as PDF</button>
      </div>
      <Gate q={q} what="the weekly review">{(d) => (
        <article>
          <header className="border-b-2 border-fg pb-4 mb-6">
            <div className="label label-strong">Pulp Riot · Weekly supply review</div>
            <h1 className="h-page" style={{ fontSize: '2.6rem' }}>Week of {d.week}</h1>
            <p className="text-soft mt-1">Replay of {dateLong(d.asof)} from the supplied synthetic workbook. Built from data that had arrived by 12:00 that day.</p>
          </header>
          {d.headline && <p className="serif text-[1.6rem] leading-snug mb-6">{d.headline.text}</p>}
          <div className="grid grid-cols-4 gap-4 mb-8">
            <div><div className="label">Red lines now</div><div className="stat-md num">{d.stock.red_now}</div><div className="text-xs text-soft">last week {d.stock.red_week_ago}</div></div>
            <div><div className="label">Excess stock lines</div><div className="stat-md num">{d.stock.excess}</div></div>
            <div><div className="label">Decisions waiting</div><div className="stat-md num">{d.approvals.pending}</div><div className="text-xs text-soft">{d.approvals.escalated} escalated</div></div>
            <div><div className="label">Passionfruit pulp</div><div className="stat-md num">{d.pulp.find((p: any) => p.ingredient === 'ING_PF')?.runway_days?.toFixed(0)} d</div></div>
          </div>
          <h2 className="h-card mb-2">Top five, by money at stake ({econ === 'margin' ? 'margin' : 'revenue'})</h2>
          <ol className="mb-8 space-y-3">{d.top_actions.map((a: any, i: number) => <li key={a.id} className="grid grid-cols-[1.5rem_1fr_auto] gap-3"><span className="serif text-xl">{i + 1}</span><span><b>{a.title}</b><br /><span className="text-sm text-soft">Approver {roleName(a.approver_role)}; decide by {dateLabel(a.deadline)}.</span></span><span className="num font-medium">{inrShort(a.rank_value)}</span></li>)}</ol>
          <h2 className="h-card mb-2">Decisions made this week</h2>
          {d.decisions_this_week.length === 0 ? <p className="text-soft mb-8">None recorded.</p> : <ul className="mb-8 text-sm space-y-1">{d.decisions_this_week.map((a: any) => <li key={a.id}>{dateLabel(a.asof)}: {roleName(a.actor_role)} {a.event.toLowerCase()} {a.action_id}</li>)}</ul>}
          <h2 className="h-card mb-2">Quick-commerce sales, yesterday against the 7-day average</h2>
          <table className="tbl mb-8"><thead><tr><th>Flavor</th><th className="r">Yesterday</th><th className="r">7-day average</th><th className="r">Ratio</th></tr></thead><tbody>{d.qc.rows.map((r: any) => <tr key={r.flavor}><td>{r.name}</td><td className="r num">{n0(r.yesterday)}</td><td className="r num">{n0(r.avg7)}</td><td className="r num">{r.ratio?.toFixed(2)}x</td></tr>)}</tbody></table>
          <footer className="border-t border-line pt-4 text-xs text-soft">
            <TrustTags prov={d.provenance} interval={d.interval_type} asof={d.asof} /> Amounts are scenario estimates, not measured savings. Every recommendation is pending until a named stakeholder decides. Source freshness: {d.trust.map((s: any) => `${s.label} ${s.age_days != null ? s.age_days.toFixed(1) + ' d' : 'n/a'}`).join('; ')}.
          </footer>
        </article>)}</Gate>
    </div>
  )
}
