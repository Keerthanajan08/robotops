import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, statusColor, ui } from '../api'
type Brief = { id: string; status: string; duration_ms: number; scenario: string; scenario_version: number; created_at: string }
type Fnd = { kind: string; step: number | null; message: string }
type Cmp = { a: Brief; b: Brief; outcome: 'identical' | 'improved' | 'regressed' | 'changed'; duration_delta_ms: number; events_identical: boolean
  findings: { resolved: Fnd[]; new: Fnd[]; unchanged: Fnd[] }; events: { a: { type: string; ts_ms: number }[]; b: { type: string; ts_ms: number }[] } }
type RunRow = { id: string; scenario: string; status: string; created_at: string }
const tone = { identical: 'bg-slate-100 text-slate-800', improved: 'bg-green-100 text-green-800', regressed: 'bg-red-100 text-red-800', changed: 'bg-amber-100 text-amber-800' }
export default function Compare() {
  const [sp, setSp] = useSearchParams(); const a = sp.get('a') ?? '', b = sp.get('b') ?? ''
  const [runs, setRuns] = useState<RunRow[]>([]); const [c, setC] = useState<Cmp | null>(null); const [err, setErr] = useState('')
  useEffect(() => { api<RunRow[]>('/runs?limit=100').then(setRuns).catch(e => setErr(e.message)) }, [])
  useEffect(() => { setC(null); if (a && b) api<Cmp>(`/runs/${a}/compare/${b}`).then(setC).catch(e => setErr(e.message)) }, [a, b])
  const pick = (k: 'a' | 'b') => (e: React.ChangeEvent<HTMLSelectElement>) => { const n = new URLSearchParams(sp); n.set(k, e.target.value); setSp(n) }
  const sel = (k: 'a' | 'b', v: string, label: string) => <label className="text-xs">{label}<select aria-label={label} className={`${ui.input} block`} value={v} onChange={pick(k)}><option value="">choose a run…</option>{runs.map(r => <option key={r.id} value={r.id}>{r.scenario} · {r.status} · {r.created_at.slice(0, 19)}</option>)}</select></label>
  const group = (title: string, items: Fnd[]) => <div><h3 className="text-sm font-medium">{title} ({items.length})</h3>{items.length === 0 ? <p className="text-xs text-slate-500">none</p> : <ul className="text-sm">{items.map((f, i) => <li key={i}><b>{f.kind}</b> — {f.message}</li>)}</ul>}</div>
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Compare runs</h1>
      <div className="flex flex-wrap gap-3">{sel('a', a, 'Baseline run (A)')}{sel('b', b, 'Compared run (B)')}</div>
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      {!a || !b ? <p className="text-slate-500">Choose two runs to compare.</p> : !c ? <p>Loading…</p> : <>
        <p data-testid="compare-outcome" className={`rounded px-3 py-2 text-sm font-medium ${tone[c.outcome]}`}>Outcome: {c.outcome}{c.outcome === 'identical' && ' — same status, findings and events (expected for a replay).'}</p>
        <div className="overflow-x-auto"><table className={`${ui.card} w-full text-sm`}><thead><tr className="text-left"><th /><th>A (baseline)</th><th>B (compared)</th></tr></thead><tbody>
          <tr className="border-t"><td>Status</td>{[c.a, c.b].map(r => <td key={r.id + 'x'}><span className={`rounded px-2 ${statusColor(r.status)}`}>{r.status}</span></td>)}</tr>
          <tr className="border-t"><td>Scenario version</td><td>v{c.a.scenario_version}</td><td>v{c.b.scenario_version}</td></tr>
          <tr className="border-t"><td>Duration</td><td>{c.a.duration_ms} ms</td><td>{c.b.duration_ms} ms ({c.duration_delta_ms >= 0 ? '+' : ''}{c.duration_delta_ms} ms)</td></tr></tbody></table></div>
        <section className={`${ui.card} grid gap-4 md:grid-cols-3`}>{group('Resolved in B', c.findings.resolved)}{group('New in B', c.findings.new)}{group('Unchanged', c.findings.unchanged)}</section>
        <section className={ui.card}><h2 className="font-medium">Events {c.events_identical ? '(identical)' : '(differ)'}</h2><div className="mt-2 grid gap-4 text-sm md:grid-cols-2">
          {(['a', 'b'] as const).map(k => <ol key={k}>{c.events[k].map((e, i) => { const o = c.events[k === 'a' ? 'b' : 'a'][i]; const diff = !o || o.type !== e.type || o.ts_ms !== e.ts_ms
            return <li key={i} className={diff ? 'bg-amber-50' : ''}><code>{String(e.ts_ms).padStart(6, ' ')} ms</code> {e.type}</li> })}{c.events[k].length === 0 && <li className="text-slate-500">no events</li>}</ol>)}</div></section></>}
    </div>
  )
}
