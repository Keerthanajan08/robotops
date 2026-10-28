import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { z } from 'zod'
import { api, ui } from '../api'
const schema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(10, 'Password must be at least 10 characters'),
})
export default function Login() {
  const nav = useNavigate()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [params] = useSearchParams()
  const expired = params.get('expired') === '1' && !err
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const p = schema.safeParse({ email: f.get('email'), password: f.get('password') })
    if (!p.success) { setErr(p.error.issues[0].message); return }
    setBusy(true); setErr('')
    try { const r = await api<{ access_token: string }>(`/auth/${mode}`, { method: 'POST', body: p.data }); localStorage.setItem('token', r.access_token); nav('/') }
    catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className={`${ui.card} mx-auto mt-24 flex max-w-sm flex-col gap-3`}>
      <h1 className="text-xl font-semibold">RobotOps {mode === 'login' ? 'sign in' : 'registration'}</h1>
      <label className="text-sm">Email<input name="email" type="email" className={`${ui.input} w-full`} /></label>
      <label className="text-sm">Password<input name="password" type="password" className={`${ui.input} w-full`} /></label>
      {expired && <p role="status" className="text-sm text-amber-800">Your session expired. Please sign in again.</p>}
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      <button className={ui.btn} disabled={busy}>{mode === 'login' ? 'Sign in' : 'Create account'}</button>
      <button type="button" className="text-sm underline" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setErr('') }}>
        {mode === 'login' ? 'Need an account? Register' : 'Have an account? Sign in'}
      </button>
    </form>
  )
}
