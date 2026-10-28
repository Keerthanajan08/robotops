import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ui } from '../api'
export default function Settings() {
  const nav = useNavigate()
  const [me, setMe] = useState<{ email: string } | null>(null); const [audit, setAudit] = useState<{ action: string; target: string; created_at: string }[] | null>(null); const [err, setErr] = useState('')
  useEffect(() => { api<{ email: string }>('/me').then(setMe).catch(e => setErr(e.message)); api<typeof audit>('/audit').then(setAudit).catch(e => setErr(e.message)) }, [])
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Settings &amp; profile</h1>
      {err && <p role="alert" className="text-red-700">{err}</p>}
      <section className={ui.card}><p className="text-sm">Signed in as <b>{me?.email ?? '…'}</b></p><button className={`${ui.ghost} mt-2`} onClick={() => { localStorage.removeItem('token'); nav('/login') }}>Log out</button></section>
      <section className={ui.card}><h2 className="font-medium">Audit log</h2>{audit === null ? <p className="text-sm">Loading…</p> : audit.length === 0 ? <p className="text-sm text-slate-500">No audit events.</p> :
        <div className="overflow-x-auto"><table className="mt-2 w-full text-sm"><thead><tr className="text-left"><th>When</th><th>Action</th><th>Target</th></tr></thead><tbody>{audit.map((a, i) => <tr key={i} className="border-t"><td>{a.created_at.slice(0, 19)}</td><td>{a.action}</td><td><code className="text-xs">{a.target.slice(0, 12)}</code></td></tr>)}</tbody></table></div>}</section>
    </div>
  )
}
