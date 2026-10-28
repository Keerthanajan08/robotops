import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiPage, api, ui } from '../api'
import { Pager } from '../components'

const TYPES = ['person_detected', 'voice_prompt_issued', 'acknowledgement_received', 'notification_sent', 'device_offline', 'api_request_failed']
const PAGE = 10
export type Step = { type: string; within_ms: number | null; required: boolean; match: Record<string, unknown> }
/** Editor-only shape: the payload condition is edited as raw JSON text and parsed on save. */
type EditStep = Omit<Step, 'match'> & { matchText: string }
type Sc = { id: string; name: string; severity: string; description: string; tags: string[]; steps: Step[]; version: number }

const toEdit = (s: Step): EditStep => ({
  type: s.type,
  within_ms: s.within_ms ?? null,
  required: s.required ?? true,
  matchText: s.match && Object.keys(s.match).length ? JSON.stringify(s.match) : '',
})
const blank = (): EditStep[] => [{ type: 'person_detected', within_ms: null, required: true, matchText: '' }]

/** Parses the editor's payload-condition text. Empty means "no condition". */
export function parseMatch(text: string, stepNo: number): Record<string, unknown> {
  if (!text.trim()) return {}
  let v: unknown
  try {
    v = JSON.parse(text)
  } catch {
    throw new Error(`Step ${stepNo} payload condition is not valid JSON`)
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new Error(`Step ${stepNo} payload condition must be a JSON object, e.g. {"lang": "ja"}`)
  return v as Record<string, unknown>
}

/** Client-side quick simulation: the passing variant also satisfies each step's payload condition. */
export function buildEvents(steps: Step[], fail: boolean) {
  let t = 0
  const ev: { event_id: string; type: string; ts_ms: number; payload: object }[] = []
  steps.forEach((s, i) => {
    const last = i === steps.length - 1
    if (fail && last) {
      if (s.within_ms == null) return
      t += s.within_ms + 1000
    } else if (i > 0) t += Math.max(1, Math.floor((s.within_ms ?? 500) / 2))
    ev.push({ event_id: `sim-${i}`, type: s.type, ts_ms: t, payload: { ...(s.match ?? {}) } })
  })
  return ev
}

const conditionText = (m: Record<string, unknown>) =>
  Object.entries(m).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')

export default function ProjectDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const [list, setList] = useState<Sc[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [sort, setSort] = useState('name')
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [tags, setTags] = useState('')
  const [sev, setSev] = useState('medium')
  const [steps, setSteps] = useState<EditStep[]>(blank())
  const [err, setErr] = useState('')

  const load = useCallback(
    () =>
      apiPage<Sc>(`/projects/${id}/scenarios?q=${encodeURIComponent(q)}&sort=${sort}&limit=${PAGE}&offset=${offset}`)
        .then(p => {
          setList(p.items)
          setTotal(p.total)
        })
        .catch(e => setErr(e.message)),
    [id, q, sort, offset],
  )
  useEffect(() => {
    load()
  }, [load])

  const upd = (i: number, p: Partial<EditStep>) => setSteps(s => s.map((x, j) => (j === i ? { ...x, ...p } : x)))
  const move = (i: number, d: number) =>
    setSteps(s => {
      const n = [...s]
      const j = i + d
      if (j < 0 || j >= n.length) return s
      ;[n[i], n[j]] = [n[j], n[i]]
      return n
    })
  const reset = () => {
    setEditing(null)
    setName('')
    setDesc('')
    setTags('')
    setSev('medium')
    setSteps(blank())
  }
  const edit = (sc: Sc) => {
    setEditing(sc.id)
    setName(sc.name)
    setDesc(sc.description)
    setTags(sc.tags.join(', '))
    setSev(sc.severity)
    setSteps(sc.steps.map(toEdit))
  }
  const run = async (f: () => Promise<unknown>) => {
    setErr('')
    try {
      await f()
      load()
    } catch (x) {
      setErr((x as Error).message)
    }
  }
  const save = (e: React.FormEvent) => {
    e.preventDefault()
    run(async () => {
      const parsed: Step[] = steps.map((s, i) => ({ type: s.type, within_ms: s.within_ms, required: s.required, match: parseMatch(s.matchText, i + 1) }))
      const body = { name, severity: sev, description: desc, tags: tags.split(',').map(t => t.trim()).filter(Boolean), steps: parsed }
      if (editing) await api(`/scenarios/${editing}`, { method: 'PUT', body })
      else await api(`/projects/${id}/scenarios`, { method: 'POST', body })
      reset()
    })
  }
  async function simulate(sc: Sc, fail: boolean) {
    try {
      const r = await api<{ id: string }>(`/scenarios/${sc.id}/runs`, { method: 'POST', body: { events: buildEvents(sc.steps, fail) } })
      nav(`/runs/${r.id}`)
    } catch (x) {
      setErr((x as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Scenarios</h1>
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      <form onSubmit={save} className={`${ui.card} space-y-3`} aria-label="Scenario editor">
        <h2 className="font-medium">{editing ? 'Edit scenario (saving creates a new version)' : 'New scenario'}</h2>
        <div className="flex flex-wrap gap-2">
          <input aria-label="Scenario name" placeholder="Scenario name" className={ui.input} value={name} onChange={e => setName(e.target.value)} />
          <select aria-label="Severity" className={ui.input} value={sev} onChange={e => setSev(e.target.value)}>
            {['low', 'medium', 'high', 'critical'].map(s => <option key={s}>{s}</option>)}
          </select>
          <input aria-label="Tags" placeholder="tags, comma separated" className={ui.input} value={tags} onChange={e => setTags(e.target.value)} />
          <input aria-label="Description" placeholder="Description" className={`${ui.input} min-w-[12rem] flex-1`} value={desc} onChange={e => setDesc(e.target.value)} />
        </div>
        <ol className="space-y-2">
          {steps.map((s, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 rounded border p-2">
              <span className="w-5 text-sm text-slate-500">{i + 1}</span>
              <select aria-label={`Step ${i + 1} type`} className={ui.input} value={s.type} onChange={e => upd(i, { type: e.target.value })}>
                {TYPES.map(t => <option key={t}>{t}</option>)}
              </select>
              <input
                aria-label={`Step ${i + 1} timeout ms`}
                type="number"
                min={0}
                placeholder="within ms"
                className={`${ui.input} w-28`}
                value={s.within_ms ?? ''}
                onChange={e => upd(i, { within_ms: e.target.value === '' ? null : Math.max(0, Number(e.target.value)) })}
              />
              <input
                aria-label={`Step ${i + 1} payload condition`}
                placeholder='payload condition, e.g. {"lang": "ja"}'
                className={`${ui.input} min-w-[14rem] flex-1 font-mono`}
                value={s.matchText}
                onChange={e => upd(i, { matchText: e.target.value })}
              />
              <label className="text-sm">
                <input type="checkbox" checked={s.required} onChange={e => upd(i, { required: e.target.checked })} /> required
              </label>
              <button type="button" aria-label={`Move step ${i + 1} up`} className={ui.ghost} onClick={() => move(i, -1)}>↑</button>
              <button type="button" aria-label={`Move step ${i + 1} down`} className={ui.ghost} onClick={() => move(i, 1)}>↓</button>
              <button type="button" aria-label={`Remove step ${i + 1}`} className={ui.ghost} disabled={steps.length === 1} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>
                Remove
              </button>
            </li>
          ))}
        </ol>
        <p className="text-xs text-slate-500">
          A step matches only an event of its type whose payload contains every key/value in its condition. Timeouts are measured from the previous matched step.
        </p>
        <div className="flex gap-2">
          <button type="button" className={ui.ghost} onClick={() => setSteps([...steps, { type: 'voice_prompt_issued', within_ms: 1000, required: true, matchText: '' }])}>
            Add step
          </button>
          <button className={ui.btn} disabled={!name.trim()}>{editing ? 'Save changes' : 'Save scenario'}</button>
          {editing && <button type="button" className={ui.ghost} onClick={reset}>Cancel edit</button>}
        </div>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="Search scenarios" placeholder="Search scenarios" className={ui.input} value={q} onChange={e => { setQ(e.target.value); setOffset(0) }} />
        <label className="text-sm">
          Sort{' '}
          <select aria-label="Sort scenarios" className={ui.input} value={sort} onChange={e => { setSort(e.target.value); setOffset(0) }}>
            <option value="name">Name A–Z</option>
            <option value="-name">Name Z–A</option>
            <option value="severity">Severity</option>
            <option value="-version">Most revised</option>
          </select>
        </label>
        <div className="ml-auto"><Pager label="Scenario" total={total} offset={offset} limit={PAGE} onChange={setOffset} /></div>
      </div>
      {list === null ? <p>Loading…</p> : list.length === 0 ? <p className="text-slate-500">No scenarios match.</p> : (
        <ul className="space-y-2">
          {list.map(sc => (
            <li key={sc.id} className={`${ui.card} flex flex-wrap items-center justify-between gap-2`}>
              <div>
                <strong>{sc.name}</strong>{' '}
                <span className="text-xs text-slate-500">{sc.severity} · v{sc.version} {sc.tags.map(t => `#${t}`).join(' ')}</span>
                <p className="text-xs text-slate-500">{sc.steps.map(s => s.type).join(' → ')}</p>
                {sc.steps.some(s => s.match && Object.keys(s.match).length) && (
                  <p className="text-xs text-slate-500">
                    conditions:{' '}
                    {sc.steps.map((s, i) => (s.match && Object.keys(s.match).length ? `step ${i + 1} ${conditionText(s.match)}` : null)).filter(Boolean).join('; ')}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <button className={ui.ghost} onClick={() => edit(sc)}>Edit</button>
                <button className={ui.ghost} onClick={() => run(() => api(`/scenarios/${sc.id}/duplicate`, { method: 'POST' }))}>Duplicate</button>
                <button className={ui.ghost} onClick={() => { if (window.confirm(`Delete scenario "${sc.name}"?`)) run(() => api(`/scenarios/${sc.id}`, { method: 'DELETE' })) }}>Delete</button>
                <button className={ui.ghost} onClick={() => simulate(sc, false)}>Simulate passing</button>
                <button className={ui.btn} onClick={() => simulate(sc, true)}>Simulate failing</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
