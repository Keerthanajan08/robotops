import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ui } from '../api'

type P = { id: string; name: string }

export default function Projects() {
  const [items, setItems] = useState<P[] | null>(null)
  const [name, setName] = useState('')
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => api<P[]>('/projects').then(setItems).catch(e => setErr(e.message)), [])
  useEffect(() => {
    load()
  }, [load])

  const act = async (f: () => Promise<unknown>) => {
    setErr('')
    try {
      await f()
      load()
    } catch (x) {
      setErr((x as Error).message)
    }
  }
  const add = (e: React.FormEvent) => {
    e.preventDefault()
    act(async () => {
      await api('/projects', { method: 'POST', body: { name } })
      setName('')
    })
  }
  const rename = (e: React.FormEvent) => {
    e.preventDefault()
    if (!renaming) return
    act(async () => {
      await api(`/projects/${renaming.id}`, { method: 'PATCH', body: { name: renaming.name } })
      setRenaming(null)
    })
  }
  const del = (p: P) => {
    if (window.confirm(`Delete project "${p.name}" and its scenarios?`)) act(() => api(`/projects/${p.id}`, { method: 'DELETE' }))
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Projects</h1>
      <form onSubmit={add} className="flex gap-2">
        <input aria-label="Project name" placeholder="New project name" className={ui.input} value={name} onChange={e => setName(e.target.value)} />
        <button className={ui.btn}>Create</button>
      </form>
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      {items === null ? <p>Loading…</p> : items.length === 0 ? <p className="text-slate-500">No projects yet. Create one above.</p> : (
        <ul className={`${ui.card} divide-y`}>
          {items.map(p => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              {renaming?.id === p.id ? (
                <form onSubmit={rename} className="flex flex-wrap gap-2">
                  <input aria-label={`New name for ${p.name}`} className={ui.input} value={renaming.name} autoFocus onChange={e => setRenaming({ id: p.id, name: e.target.value })} />
                  <button className={ui.btn} disabled={!renaming.name.trim()}>Save</button>
                  <button type="button" className={ui.ghost} onClick={() => setRenaming(null)}>Cancel</button>
                </form>
              ) : (
                <Link className="font-medium underline" to={`/projects/${p.id}`}>{p.name}</Link>
              )}
              <div className="flex gap-2">
                {renaming?.id !== p.id && (
                  <button className={ui.ghost} aria-label={`Rename ${p.name}`} onClick={() => setRenaming({ id: p.id, name: p.name })}>Rename</button>
                )}
                <button className={ui.ghost} aria-label={`Delete ${p.name}`} onClick={() => del(p)}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
