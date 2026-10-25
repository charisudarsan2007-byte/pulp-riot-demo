import React, { useEffect, useRef, useState } from 'react'
import { ChevronDown, Info } from 'lucide-react'

export type IndexItem = { id: string; title: string; desc: string }

const MEMORY: Record<string, string[]> = {}          // keeps the choice while moving between pages

/** A checklist of the page's features. Nothing is shown until something is ticked. */
export function useFeatureIndex(page: string, items: IndexItem[], heading = 'Choose what you want to see on this page') {
  const [sel, setSel] = useState<string[]>(MEMORY[page] || [])
  const [open, setOpen] = useState<string | null>(null)
  const all = useRef<HTMLInputElement>(null)
  const set = (v: string[]) => { MEMORY[page] = v; setSel(v) }
  const toggle = (id: string) => set(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id])
  const allOn = sel.length === items.length
  useEffect(() => { if (all.current) all.current.indeterminate = sel.length > 0 && !allOn }, [sel, allOn])
  const panel = (
    <div className="container-nds pb-8 no-print">
      <div className="panel p-5 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <div className="label label-strong">Index</div>
            <div className="serif text-[1.5rem] leading-tight">{heading}</div>
          </div>
          <label className="inline-flex items-center gap-2 cursor-pointer select-none">
            <input ref={all} type="checkbox" checked={allOn} onChange={() => set(allOn ? [] : items.map((i) => i.id))} />
            <span className="label label-strong">Select all ({sel.length} of {items.length})</span>
          </label>
        </div>
        <ul className="grid md:grid-cols-2 gap-x-8">
          {items.map((i) => (
            <li key={i.id} className="border-t border-line py-2.5">
              <div className="flex items-center gap-3">
                <input id={`idx-${page}-${i.id}`} type="checkbox" checked={sel.includes(i.id)} onChange={() => toggle(i.id)} />
                <label htmlFor={`idx-${page}-${i.id}`} className="flex-1 cursor-pointer">{i.title}</label>
                <button type="button" className="p-1 rounded-sm hover:bg-muted text-soft" aria-expanded={open === i.id} aria-label={`What "${i.title}" shows`} onClick={() => setOpen(open === i.id ? null : i.id)}>
                  {open === i.id ? <ChevronDown size={16} /> : <Info size={16} />}
                </button>
              </div>
              {open === i.id && <p className="mt-2 ml-7 text-sm text-soft idx-desc">{i.desc}</p>}
            </li>
          ))}
        </ul>
      </div>
      {sel.length === 0 && <p className="mt-6 text-soft">Nothing is shown yet. Tick one or more features above, or select all.</p>}
    </div>
  )
  const show = (id: string) => sel.includes(id)
  return { panel, show, count: sel.length }
}
