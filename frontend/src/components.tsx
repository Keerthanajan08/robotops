import { useState } from 'react'
import { ui } from './api'

/** Previous/next pager driven by the API's X-Total-Count header. */
export function Pager({ total, offset, limit, onChange, label }: {
  total: number
  offset: number
  limit: number
  onChange: (offset: number) => void
  label: string
}) {
  if (total <= limit) return null
  const from = offset + 1
  const to = Math.min(offset + limit, total)
  return (
    <nav aria-label={`${label} pages`} className="flex items-center gap-2 text-sm">
      <button className={ui.ghost} disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </button>
      <span aria-live="polite">
        {from}–{to} of {total}
      </span>
      <button className={ui.ghost} disabled={to >= total} onClick={() => onChange(offset + limit)}>
        Next
      </button>
    </nav>
  )
}

/** Sortable column header. `sort` is the API value: "field" ascending, "-field" descending. */
export function SortHeader({ field, label, sort, onSort }: {
  field: string
  label: string
  sort: string
  onSort: (s: string) => void
}) {
  const active = sort.replace(/^-/, '') === field
  const desc = sort.startsWith('-')
  const next = active && !desc ? `-${field}` : field
  return (
    <th aria-sort={active ? (desc ? 'descending' : 'ascending') : 'none'}>
      <button className="font-semibold hover:underline" onClick={() => onSort(next)}>
        {label} {active ? (desc ? '↓' : '↑') : ''}
      </button>
    </th>
  )
}

/** Shows a freshly issued device key exactly once, with the command to use it. */
export function KeyReveal({ deviceName, deviceKey, onDone }: { deviceName: string; deviceKey: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(deviceKey)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <section role="region" aria-label="New device key" className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm">
      <h2 className="font-medium">Device key for {deviceName}</h2>
      <p>
        Copy it now. Only a hash is stored, so it <strong>cannot be shown again</strong>. Rotating the key invalidates
        this one.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code data-testid="device-key" className="break-all rounded bg-white px-2 py-1">
          {deviceKey}
        </code>
        <button className={ui.ghost} onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <p className="text-xs text-slate-600">Send events from the device:</p>
      <pre className="overflow-x-auto rounded bg-slate-900 p-2 text-xs text-slate-100">
        {`ROBOTOPS_DEVICE_KEY=${deviceKey} \\\n  python agent/device_agent.py --url ${window.location.origin} demo`}
      </pre>
      <button className={ui.btn} onClick={onDone}>
        I have saved the key
      </button>
    </section>
  )
}
