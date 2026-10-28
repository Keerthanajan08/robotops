import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ui } from '../api'
type Opt = { id: string; name: string }
type Job = { id: string; status: string; device_id: string; scenario_id: string; events_total: number; events_emitted: number; error: string | null; run_id: string | null; fail: boolean }
export default function Simulations() {
  const [devs, setDevs] = useState<Opt[]>([]); const [scs, setScs] = useState<Opt[]>([]); const [jobs, setJobs] = useState<Job[] | null>(null)
  const [dev, setDev] = useState(''); const [sc, setSc] = useState(''); const [fail, setFail] = useState(false); const [err, setErr] = useState('')
  const loadJobs = useCallback(() => api<Job[]>('/simulations').then(setJobs).catch(e => setErr(e.message)), [])
  useEffect(() => {
    api<Opt[]>('/devices').then(d => { setDevs(d); setDev(x => x || d[0]?.id || '') }).catch(e => setErr(e.message))
    api<Opt[]>('/projects').then(async ps => {
      const all = (await Promise.all(ps.map(async p => (await api<Opt[]>(`/projects/${p.id}/scenarios`)).map(s => ({ id: s.id, name: `${p.name} / ${s.name}` }))))).flat()
      setScs(all); setSc(x => x || all[0]?.id || '')
    }).catch(e => setErr(e.message))
    loadJobs()
  }, [loadJobs])
  const active = jobs?.some(j => j.status === 'queued' || j.status === 'running')
  useEffect(() => { if (!active) return; const t = setInterval(loadJobs, 700); return () => clearInterval(t) }, [active, loadJobs])
  async function queue(e: React.FormEvent) { e.preventDefault(); setErr(''); try { await api(`/devices/${dev}/simulations`, { method: 'POST', body: { scenario_id: sc, fail } }); loadJobs() } catch (x) { setErr((x as Error).message) } }
  async function cancel(j: Job) { try { await api(`/simulations/${j.id}/cancel`, { method: 'POST' }); loadJobs() } catch (x) { setErr((x as Error).message) } }
  const nm = (l: Opt[], id: string) => l.find(x => x.id === id)?.name ?? id.slice(0, 8)
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Simulations</h1>
      <p className="text-sm text-slate-500">A background worker emits a generated (simulated) event sequence from the chosen device, then records a run for the scenario.</p>
      {devs.length === 0 || scs.length === 0 ? <p className="text-slate-500">Register a device and save a scenario first.</p> :
        <form onSubmit={queue} className={`${ui.card} flex flex-wrap items-end gap-3`}>
          <label className="text-xs">Device<select aria-label="Device" className={`${ui.input} block`} value={dev} onChange={e => setDev(e.target.value)}>{devs.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <label className="text-xs">Scenario<select aria-label="Scenario" className={`${ui.input} block`} value={sc} onChange={e => setSc(e.target.value)}>{scs.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="text-sm"><input type="checkbox" checked={fail} onChange={e => setFail(e.target.checked)} /> Simulate failing sequence</label>
          <button className={ui.btn}>Queue simulation</button></form>}
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      {jobs === null ? <p>Loading…</p> : jobs.length === 0 ? <p className="text-slate-500">No simulation jobs yet.</p> :
        <div className="overflow-x-auto"><table className={`${ui.card} w-full text-sm`}><thead><tr className="text-left"><th>Device</th><th>Scenario</th><th>Mode</th><th>Status</th><th>Progress</th><th /></tr></thead><tbody>
          {jobs.map(j => <tr key={j.id} className="border-t"><td>{nm(devs, j.device_id)}</td><td>{nm(scs, j.scenario_id)}</td><td>{j.fail ? 'failing' : 'passing'}</td><td data-testid="job-status">{j.status}{j.error && <span className="block text-xs text-red-700">{j.error}</span>}</td><td>{j.events_emitted}/{j.events_total}</td>
            <td className="space-x-2">{j.run_id && <Link className="underline" to={`/runs/${j.run_id}`}>View run</Link>}{(j.status === 'queued' || j.status === 'running') && <button className={ui.ghost} onClick={() => cancel(j)}>Cancel</button>}</td></tr>)}</tbody></table></div>}
    </div>
  )
}
