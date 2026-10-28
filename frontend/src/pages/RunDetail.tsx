import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, statusColor, ui } from '../api'
type Run = { id: string; status: string; duration_ms: number; replay_of: string | null; device_id: string | null; scenario: { name: string; version: number; steps: { type: string; within_ms: number | null; required: boolean }[] }
  events: { type: string; ts_ms: number }[]; findings: { kind: string; step: number | null; message: string }[]
  device_state: { name: string; online: boolean; last_seen: string | null; captured_at: string } | null
  logs: { at: string; level: string; msg: string; request_id: string }[] }
export default function RunDetail() {
  const { id } = useParams(); const nav = useNavigate()
  const [run, setRun] = useState<Run | null>(null); const [err, setErr] = useState('')
  const load = useCallback(() => api<Run>(`/runs/${id}`).then(setRun).catch(e => setErr(e.message)), [id])
  useEffect(() => { load() }, [load])
  const act = (path: string, go: boolean) => async () => { setErr(''); try { const r = await api<Run>(path, { method: 'POST' }); if (go) nav(`/runs/${r.id}`); else setRun(r) } catch (x) { setErr((x as Error).message) } }
  if (!run) return err ? <p role="alert" className="text-red-700">{err}</p> : <p>Loading…</p>
  const evs = [...run.events].sort((a, b) => a.ts_ms - b.ts_ms); const done = run.status === 'passed' || run.status === 'failed'
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3"><h1 className="text-xl font-semibold">{run.scenario.name}</h1><span data-testid="run-status" className={`rounded px-2 py-0.5 text-sm ${statusColor(run.status)}`}>{run.status}</span>
        <span className="text-sm text-slate-500">v{run.scenario.version} · {run.duration_ms} ms (virtual) {run.replay_of && <>· replay of <Link className="underline" to={`/runs/${run.replay_of}`}>earlier run</Link></>}</span>
        <div className="ml-auto flex gap-2">
          {run.status === 'running' && <><button className={ui.btn} onClick={act(`/runs/${run.id}/complete`, false)}>Complete run</button><button className={ui.ghost} onClick={act(`/runs/${run.id}/cancel`, false)}>Cancel run</button></>}
          {run.replay_of && <Link className={ui.ghost} to={`/compare?a=${run.replay_of}&b=${run.id}`}>Compare with original</Link>}
          <Link className={ui.ghost} to={`/compare?a=${run.id}`}>Compare…</Link>
          {done && <button className={ui.btn} onClick={act(`/runs/${run.id}/replay`, true)}>Replay run</button>}</div></div>
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      <section className={ui.card}><h2 className="font-medium">Findings</h2>{run.status === 'running' ? <p className="text-sm text-slate-500">Awaiting events.</p> : run.findings.length === 0 ? <p className="text-sm text-green-700">All assertions passed.</p> :
        <ul className="mt-2 space-y-1 text-sm">{run.findings.map((f, i) => <li key={i}><b className="text-red-700">{f.kind}</b> — {f.message}</li>)}</ul>}</section>
      <div className="grid gap-4 md:grid-cols-2">
        <section className={ui.card}><h2 className="font-medium">Expected</h2><ol className="mt-2 list-decimal pl-5 text-sm">{run.scenario.steps.map((s, i) => <li key={i}>{s.type}{s.within_ms != null && ` (≤ ${s.within_ms} ms)`}{!s.required && ' [optional]'}</li>)}</ol></section>
        <section className={ui.card}><h2 className="font-medium">Actual timeline</h2><ol className="mt-2 text-sm">{evs.map((e, i) => <li key={i}><code>{String(e.ts_ms).padStart(6, ' ')} ms</code> {e.type}</li>)}{evs.length === 0 && <li className="text-slate-500">No events recorded.</li>}</ol></section>
      </div>
      <section className={ui.card}><h2 className="font-medium">Device state when recorded</h2>{run.device_state ? <p className="mt-1 text-sm">{run.device_state.name} was <b>{run.device_state.online ? 'online' : 'offline'}</b> (simulated), last seen {run.device_state.last_seen ?? 'never'}. {run.device_id && <Link className="underline" to={`/devices/${run.device_id}`}>Open device</Link>}</p> : <p className="mt-1 text-sm text-slate-500">No device was attached to this run.</p>}</section>
      <section className={ui.card}><h2 className="font-medium">Structured logs</h2>{run.logs.length === 0 ? <p className="mt-1 text-sm text-slate-500">No log entries (older or seeded run).</p> :
        <div className="mt-2 overflow-x-auto"><pre className="text-xs leading-5">{run.logs.map(l => `${l.at.slice(11, 23)} ${l.level.toUpperCase().padEnd(7)} [${l.request_id.slice(0, 8)}] ${l.msg}`).join('\n')}</pre></div>}</section>
    </div>
  )
}
