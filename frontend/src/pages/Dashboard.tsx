import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, apiPage, download, statusColor, ui } from '../api'
import { Pager, SortHeader } from '../components'
const PAGE = 20
type M = { total_runs: number; failed_runs: number; pass_rate: number | null; median_duration_ms: number | null; failure_categories: [string, number][]; seeded_runs_included: number }
type R = { id: string; scenario: string; status: string; duration_ms: number; created_at: string; failure_kinds: string[]; seeded: boolean }
type Opt = { id: string; name: string; online?: boolean }
type F = { status: string; project_id: string; device_id: string; date_from: string; date_to: string }
export function buildQuery(f: Partial<F>) {
  const p = new URLSearchParams(); Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v) }); const s = p.toString(); return s ? `?${s}` : ''
}
export default function Dashboard() {
  const [f, setF] = useState<F>({ status: '', project_id: '', device_id: '', date_from: '', date_to: '' })
  const [m, setM] = useState<M | null>(null); const [runs, setRuns] = useState<R[] | null>(null)
  const [offset, setOffset] = useState(0); const [sort, setSort] = useState('-created_at'); const [total, setTotal] = useState(0)
  const [projects, setProjects] = useState<Opt[]>([]); const [devices, setDevices] = useState<Opt[]>([]); const [err, setErr] = useState('')
  const q = buildQuery({ ...f, date_to: f.date_to ? `${f.date_to}T23:59:59` : '' })
  useEffect(() => { api<Opt[]>('/projects').then(setProjects).catch(e => setErr(e.message)); api<Opt[]>('/devices').then(setDevices).catch(e => setErr(e.message)) }, [])
  useEffect(() => { setRuns(null); api<M>(`/metrics${q}`).then(setM).catch(e => setErr(e.message)); }, [q])
  useEffect(() => { setOffset(0) }, [q])
  useEffect(() => { const sep = q ? '&' : '?'; apiPage<R>(`/runs${q}${sep}sort=${sort}&limit=${PAGE}&offset=${offset}`).then(p => { setRuns(p.items); setTotal(p.total) }).catch(e => setErr(e.message)) }, [q, sort, offset])
  const set = (k: keyof F) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value })
  const exp = (kind: 'csv' | 'html' | 'pdf') => download(`/reports/runs.${kind}${q}`, `robotops-runs.${kind}`).catch(e => setErr(e.message))
  const stat = (l: string, v: string) => <div className={ui.card}><p className="text-xs text-slate-500">{l}</p><p className="text-2xl font-semibold">{v}</p></div>
  const offline = devices.filter(d => d.online === false).length
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Overview</h1>
      {err && <p role="alert" className="text-red-700">{err}</p>}
      <p className="text-xs text-slate-500">Metrics are calculated from stored runs of simulated devices{m && m.seeded_runs_included > 0 ? `; ${m.seeded_runs_included} of them are seeded demo runs` : ''}.</p>
      <div className="grid gap-3 sm:grid-cols-4">{stat('Total runs', String(m?.total_runs ?? '…'))}{stat('Pass rate', m?.pass_rate == null ? '—' : `${Math.round(m.pass_rate * 100)}%`)}{stat('Median duration', m?.median_duration_ms == null ? '—' : `${m.median_duration_ms} ms`)}{stat('Devices offline', `${offline} / ${devices.length}`)}</div>
      {m && m.failure_categories.length > 0 && <section className={ui.card} aria-label="Failure categories"><h2 className="mb-2 font-medium">Failure categories</h2><div className="h-40"><ResponsiveContainer><BarChart data={m.failure_categories.map(([k, v]) => ({ k, v }))}><XAxis dataKey="k" /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="v" fill="#b91c1c" /></BarChart></ResponsiveContainer></div></section>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">Status<select aria-label="Filter by status" className={`${ui.input} block`} value={f.status} onChange={set('status')}><option value="">all</option><option>passed</option><option>failed</option><option>running</option><option>cancelled</option></select></label>
        <label className="text-xs">Project<select aria-label="Filter by project" className={`${ui.input} block`} value={f.project_id} onChange={set('project_id')}><option value="">all</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label className="text-xs">Device<select aria-label="Filter by device" className={`${ui.input} block`} value={f.device_id} onChange={set('device_id')}><option value="">all</option>{devices.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label className="text-xs">From<input type="date" aria-label="From date" className={`${ui.input} block`} value={f.date_from} onChange={set('date_from')} /></label>
        <label className="text-xs">To<input type="date" aria-label="To date" className={`${ui.input} block`} value={f.date_to} onChange={set('date_to')} /></label>
        <div className="ml-auto flex gap-2">{(['csv', 'html', 'pdf'] as const).map(k => <button key={k} className={ui.ghost} onClick={() => exp(k)}>Export {k.toUpperCase()}</button>)}</div>
      </div>
      {runs === null ? <p>Loading…</p> : runs.length === 0 ? <p className="text-slate-500">No runs match. Queue a simulation or simulate a scenario from a project.</p> :
        <div className="overflow-x-auto"><table className={`${ui.card} w-full text-sm`}><thead><tr className="text-left"><th>Scenario</th><SortHeader field="status" label="Status" sort={sort} onSort={s => { setSort(s); setOffset(0) }} /><SortHeader field="duration_ms" label="Duration" sort={sort} onSort={s => { setSort(s); setOffset(0) }} /><th>Failures</th><SortHeader field="created_at" label="When" sort={sort} onSort={s => { setSort(s); setOffset(0) }} /></tr></thead><tbody>
          {runs.map(r => <tr key={r.id} className="border-t"><td><Link className="underline" to={`/runs/${r.id}`}>{r.scenario}</Link>{r.seeded && <span className="ml-2 rounded bg-amber-100 px-1 text-xs">demo seed</span>}</td><td><span className={`rounded px-2 ${statusColor(r.status)}`}>{r.status}</span></td><td>{r.duration_ms} ms</td><td>{r.failure_kinds.join(', ') || '—'}</td><td>{new Date(r.created_at + (r.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString()}</td></tr>)}</tbody></table></div>}
      <Pager label="Run" total={total} offset={offset} limit={PAGE} onChange={setOffset} />
    </div>
  )
}
