import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ui } from '../api'
import { KeyReveal } from '../components'

type D = { id: string; name: string; online: boolean; last_seen: string | null; key_prefix: string | null }
type Created = D & { device_key: string }
const TYPES = ['person_detected', 'voice_prompt_issued', 'acknowledgement_received', 'notification_sent', 'device_offline', 'api_request_failed']

export default function Devices() {
  const [items, setItems] = useState<D[] | null>(null)
  const [name, setName] = useState('')
  const [created, setCreated] = useState<Created | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const load = useCallback(() => api<D[]>('/devices').then(setItems).catch(e => setErr(e.message)), [])
  useEffect(() => {
    load()
  }, [load])
  const wrap = async (f: () => Promise<unknown>, ok = '') => {
    setErr('')
    setMsg('')
    try {
      await f()
      setMsg(ok)
      load()
    } catch (x) {
      setErr((x as Error).message)
    }
  }
  const register = (e: React.FormEvent) => {
    e.preventDefault()
    wrap(async () => {
      setCreated(await api<Created>('/devices', { method: 'POST', body: { name } }))
      setName('')
    })
  }
  const send = (d: D, type: string) =>
    wrap(() => api(`/devices/${d.id}/events`, { method: 'POST', body: { event_id: crypto.randomUUID(), type, ts_ms: Date.now() % 1_000_000 } }), `Sent ${type}`)

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Simulated devices</h1>
      <form onSubmit={register} className="flex gap-2">
        <input aria-label="Device name" placeholder="Device name" className={ui.input} value={name} onChange={e => setName(e.target.value)} />
        <button className={ui.btn}>Register</button>
      </form>
      {created && <KeyReveal deviceName={created.name} deviceKey={created.device_key} onDone={() => setCreated(null)} />}
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      {msg && <p role="status" className="text-sm text-green-700">{msg}</p>}
      {items === null ? <p>Loading…</p> : items.length === 0 ? <p className="text-slate-500">No devices registered.</p> : (
        <div className="overflow-x-auto">
          <table className={`${ui.card} w-full text-sm`}>
            <thead>
              <tr className="text-left"><th>Name</th><th>Status</th><th>Key</th><th>Last seen</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {items.map(d => (
                <tr key={d.id} className="border-t">
                  <td><Link className="underline" to={`/devices/${d.id}`}>{d.name}</Link></td>
                  <td>{d.online ? 'online (simulated)' : 'offline (simulated)'}</td>
                  <td><code className="text-xs">{d.key_prefix ? `${d.key_prefix}…` : 'none'}</code></td>
                  <td>{d.last_seen ?? '—'}</td>
                  <td className="flex flex-wrap gap-2 py-1">
                    <button className={ui.ghost} onClick={() => wrap(() => api(`/devices/${d.id}/${d.online ? 'disconnect' : 'reconnect'}`, { method: 'POST' }))}>
                      {d.online ? 'Disconnect' : 'Reconnect'}
                    </button>
                    <select aria-label={`Send event from ${d.name}`} className={ui.input} value="" onChange={e => e.target.value && send(d, e.target.value)}>
                      <option value="">Send event…</option>
                      {TYPES.map(t => <option key={t}>{t}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
