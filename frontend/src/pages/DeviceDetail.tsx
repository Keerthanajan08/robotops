import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ui } from '../api'
import { KeyReveal } from '../components'

type D = {
  id: string
  name: string
  online: boolean
  last_seen: string | null
  key_prefix: string | null
  key_rotated_at: string | null
  recent_events: { type: string; ts_ms: number; correlation_id: string }[]
}
type F = { id: string; event_id: string; type: string; reason: string; status: string; created_at: string }

export default function DeviceDetail() {
  const { id } = useParams()
  const [d, setD] = useState<D | null>(null)
  const [fails, setFails] = useState<F[]>([])
  const [newKey, setNewKey] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = useCallback(() => {
    api<D>(`/devices/${id}`).then(setD).catch(e => setErr(e.message))
    api<F[]>(`/devices/${id}/deliveries?status=failed`).then(setFails).catch(e => setErr(e.message))
  }, [id])
  useEffect(() => {
    load()
  }, [load])
  const act = (f: () => Promise<unknown>, ok: string) => async () => {
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
  const rotate = async () => {
    if (!d || !window.confirm(`Rotate the key for "${d.name}"? The current key stops working immediately.`)) return
    await act(async () => setNewKey((await api<{ device_key: string }>(`/devices/${d.id}/key/rotate`, { method: 'POST' })).device_key), 'Key rotated; the old key no longer works')()
  }

  if (!d) return err ? <p role="alert" className="text-red-700">{err}</p> : <p>Loading…</p>
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{d.name}</h1>
        <span className="rounded bg-amber-100 px-2 text-xs text-amber-800">simulated</span>
        <span className={d.online ? 'text-green-700' : 'text-red-700'}>{d.online ? 'online' : 'offline'}</span>
        <span className="text-sm text-slate-500">last seen {d.last_seen ?? 'never'}</span>
        <button
          className={`${ui.ghost} ml-auto`}
          onClick={act(() => api(`/devices/${d.id}/${d.online ? 'disconnect' : 'reconnect'}`, { method: 'POST' }), d.online ? 'Disconnected (simulated)' : 'Reconnected (simulated)')}
        >
          {d.online ? 'Disconnect' : 'Reconnect'}
        </button>
      </div>
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      {msg && <p role="status" className="text-sm text-green-700">{msg}</p>}
      {newKey && <KeyReveal deviceName={d.name} deviceKey={newKey} onDone={() => setNewKey(null)} />}

      <section className={ui.card} aria-label="Device credential">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-medium">Device credential</h2>
            <p className="text-sm text-slate-600">
              {d.key_prefix ? (
                <>Key <code>{d.key_prefix}…</code>{d.key_rotated_at && <> · issued {new Date(d.key_rotated_at + (d.key_rotated_at.endsWith('Z') || d.key_rotated_at.includes('+') ? '' : 'Z')).toLocaleString()}</>}</>
              ) : 'No key issued yet. Rotate to issue one.'}
            </p>
            <p className="text-xs text-slate-500">The device sends events to <code>POST /api/v1/ingest/events</code> with an <code>X-Device-Key</code> header. It never uses a user password or token.</p>
          </div>
          <button className={ui.ghost} onClick={rotate}>{d.key_prefix ? 'Rotate key' : 'Issue key'}</button>
        </div>
      </section>

      <section className={ui.card}>
        <div className="flex items-center justify-between">
          <h2 className="font-medium">Failed deliveries ({fails.length})</h2>
          <button className={ui.btn} disabled={!d.online || fails.length === 0} onClick={act(async () => { await api(`/devices/${d.id}/deliveries/retry`, { method: 'POST' }) }, 'Retried failed deliveries')}>
            Retry failed deliveries
          </button>
        </div>
        {!d.online && fails.length > 0 && <p className="text-xs text-slate-500">Reconnect the device to enable retry.</p>}
        {fails.length === 0 ? <p className="mt-2 text-sm text-slate-500">No failed deliveries.</p> : (
          <ul className="mt-2 text-sm">{fails.map(f => <li key={f.id}><code>{f.type}</code> · {f.reason} · {f.created_at}</li>)}</ul>
        )}
      </section>

      <section className={ui.card}>
        <h2 className="font-medium">Recent events</h2>
        {d.recent_events.length === 0 ? <p className="mt-2 text-sm text-slate-500">No events delivered yet.</p> : (
          <div className="overflow-x-auto">
            <table className="mt-2 w-full text-sm">
              <thead><tr className="text-left"><th>Type</th><th>t (ms)</th><th>Correlation</th></tr></thead>
              <tbody>
                {d.recent_events.map((e, i) => (
                  <tr key={i} className="border-t"><td>{e.type}</td><td>{e.ts_ms}</td><td><code className="text-xs">{e.correlation_id.slice(0, 18)}</code></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
