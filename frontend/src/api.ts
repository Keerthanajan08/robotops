export class ApiError extends Error {
  constructor(public status: number, msg: string) {
    super(msg)
  }
}

type Opts = { method?: string; body?: unknown }

function authHeader(): Record<string, string> {
  const t = localStorage.getItem('token')
  return t ? { Authorization: `Bearer ${t}` } : {}
}

/** Tokens expire server-side; on 401 with a stored token, drop it and send the user to sign in again. */
function handleUnauthorized(status: number) {
  if (status === 401 && localStorage.getItem('token')) {
    localStorage.removeItem('token')
    window.location.href = '/login?expired=1'
  }
}

async function request(path: string, opts: Opts = {}) {
  const r = await fetch('/api/v1' + path, {
    method: opts.method ?? 'GET',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  if (r.status === 204) return { r, j: undefined }
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    handleUnauthorized(r.status)
    const d = j as { detail?: { msg: string }[]; error?: { message: string } }
    const msg = d.error?.message ?? (Array.isArray(d.detail) ? d.detail.map(x => x.msg).join('; ') : 'Request failed')
    throw new ApiError(r.status, msg)
  }
  return { r, j }
}

export async function api<T>(path: string, opts: Opts = {}): Promise<T> {
  return (await request(path, opts)).j as T
}

/** List endpoints return the total row count in X-Total-Count. */
export async function apiPage<T>(path: string): Promise<{ items: T[]; total: number }> {
  const { r, j } = await request(path)
  const items = j as T[]
  return { items, total: Number(r.headers.get('X-Total-Count') ?? items.length) }
}

export const ui = {
  btn: 'rounded bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-500',
  ghost: 'rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-500',
  input: 'rounded border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500',
  card: 'rounded-lg border border-slate-200 bg-white p-4',
}

export const statusColor = (s: string) =>
  s === 'passed' ? 'bg-green-100 text-green-800' : s === 'failed' ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'

export async function download(path: string, filename: string) {
  const r = await fetch('/api/v1' + path, { headers: authHeader() })
  if (!r.ok) {
    handleUnauthorized(r.status)
    throw new ApiError(r.status, 'Export failed')
  }
  const a = document.createElement('a')
  a.href = URL.createObjectURL(await r.blob())
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
