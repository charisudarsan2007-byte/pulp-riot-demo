const IN = new Intl.NumberFormat('en-IN')

export function n0(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  return IN.format(Math.round(v))
}

/** Bottles in Indian units: lakh (1e5) and crore (1e7). */
export function bottles(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  const a = Math.abs(v)
  if (a >= 1e7) return `${(v / 1e7).toFixed(digits)} cr`
  if (a >= 1e5) return `${(v / 1e5).toFixed(digits)} lakh`
  return IN.format(Math.round(v))
}

/** Rupees: crore, lakh or plain. */
export function inr(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  const a = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(digits)} crore`
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(digits)} lakh`
  return `${sign}₹${IN.format(Math.round(a))}`
}

export function inrShort(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  const a = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(1)}cr`
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(1)}L`
  return `${sign}₹${IN.format(Math.round(a))}`
}

export function days(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  return `${v.toFixed(digits)} d`
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  return `${(v * 100).toFixed(digits)}%`
}

export function dateLabel(s: string | null | undefined): string {
  if (!s) return '–'
  const d = new Date(s.length <= 10 ? `${s}T00:00:00` : s.replace(' ', 'T'))
  if (isNaN(d.getTime())) return s
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function dateLong(s: string | null | undefined): string {
  if (!s) return '–'
  const d = new Date(s.length <= 10 ? `${s}T00:00:00` : s.replace(' ', 'T'))
  if (isNaN(d.getTime())) return s
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function addDays(s: string, n: number): string {
  const d = new Date(`${s}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function diffDays(a: string, b: string): number {
  return Math.round((new Date(`${a}T00:00:00Z`).getTime() - new Date(`${b}T00:00:00Z`).getTime()) / 86400000)
}

export const ROLE_NAMES: Record<string, string> = {
  ROLE_PLN: 'Demand / supply planner', ROLE_DEP: 'Depot manager', ROLE_MKT: 'Marketing', ROLE_SAL: 'Sales', ROLE_PRC: 'Procurement',
  ROLE_FIN: 'Finance', ROLE_SCH: 'Supply-chain head', ROLE_COO: 'COO',
}
export function roleName(r?: string | null): string { return r ? ROLE_NAMES[r] || r : '–' }
