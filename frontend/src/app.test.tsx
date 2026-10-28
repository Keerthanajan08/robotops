import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Login from './pages/Login'
import { buildEvents } from './pages/ProjectDetail'
import { buildQuery } from './pages/Dashboard'
import ProjectDetail from './pages/ProjectDetail'
import Devices from './pages/Devices'
import { Route, Routes } from 'react-router-dom'

test('login form shows inline validation error without calling the API', () => {
  const spy = vi.spyOn(globalThis, 'fetch')
  render(<MemoryRouter><Login /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@example.com' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(screen.getByRole('alert')).toHaveTextContent('at least 10 characters')
  expect(spy).not.toHaveBeenCalled()
})
test('login surfaces API error messages', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { message: 'Invalid credentials' } }), { status: 401 }))
  render(<MemoryRouter><Login /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@example.com' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough-pw' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('Invalid credentials')).toBeInTheDocument()
})
const steps = [{ type: 'person_detected', within_ms: null, required: true, match: {} }, { type: 'voice_prompt_issued', within_ms: 1000, required: true, match: {} }]
test('failing simulation delays last step beyond its timeout', () => {
  expect(buildEvents(steps, false)[1].ts_ms).toBe(500)
  expect(buildEvents(steps, true)[1].ts_ms).toBeGreaterThan(1000)
})
test('buildQuery drops empty filters and encodes values', () => {
  expect(buildQuery({ status: '', project_id: 'p1', date_from: '2026-01-01' })).toBe('?project_id=p1&date_from=2026-01-01')
  expect(buildQuery({})).toBe('')
})
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status })
test('scenario editor: add, reorder and remove steps', () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json([]))
  render(<MemoryRouter initialEntries={['/projects/p1']}><Routes><Route path="/projects/:id" element={<ProjectDetail />} /></Routes></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Add step' }))
  expect(screen.getByLabelText('Step 2 type')).toHaveValue('voice_prompt_issued')
  fireEvent.click(screen.getByRole('button', { name: 'Move step 2 up' }))
  expect(screen.getByLabelText('Step 1 type')).toHaveValue('voice_prompt_issued')
  fireEvent.click(screen.getByRole('button', { name: 'Remove step 1' }))
  expect(screen.queryByLabelText('Step 2 type')).toBeNull()
  expect(screen.getByLabelText('Step 1 type')).toHaveValue('person_detected')
})
test('devices page shows API error state', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ error: { message: 'Database unavailable' } }, 500))
  render(<MemoryRouter><Devices /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable')
})
test('devices page shows empty state', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json([]))
  render(<MemoryRouter><Devices /></MemoryRouter>)
  expect(await screen.findByText('No devices registered.')).toBeInTheDocument()
})
import { parseMatch } from './pages/ProjectDetail'
import Projects from './pages/Projects'
import { Pager } from './components'

test('payload conditions: parsed as JSON objects, empty means none, bad input explained', () => {
  expect(parseMatch('', 1)).toEqual({})
  expect(parseMatch('{"lang": "ja"}', 2)).toEqual({ lang: 'ja' })
  expect(() => parseMatch('{lang: ja}', 3)).toThrow('Step 3 payload condition is not valid JSON')
  expect(() => parseMatch('["ja"]', 4)).toThrow('must be a JSON object')
})
test('simulated passing events satisfy payload conditions', () => {
  const s = [{ type: 'person_detected', within_ms: null, required: true, match: { zone: 'lobby' } }]
  expect(buildEvents(s, false)[0].payload).toEqual({ zone: 'lobby' })
})
test('scenario editor blocks saving an invalid payload condition', async () => {
  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json([]))
  render(<MemoryRouter initialEntries={['/projects/p1']}><Routes><Route path="/projects/:id" element={<ProjectDetail />} /></Routes></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Scenario name'), { target: { value: 'S' } })
  fireEvent.change(screen.getByLabelText('Step 1 payload condition'), { target: { value: 'nope' } })
  spy.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'Save scenario' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Step 1 payload condition is not valid JSON')
  expect(spy).not.toHaveBeenCalled()
})
test('registering a device reveals its key once', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) =>
    init?.method === 'POST' ? json({ id: 'd1', name: 'bot', online: true, last_seen: null, key_prefix: 'rok_abcdefgh', device_key: 'rok_abcdefghSECRET' }, 201) : json([]))
  render(<MemoryRouter><Devices /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('Device name'), { target: { value: 'bot' } })
  fireEvent.click(screen.getByRole('button', { name: 'Register' }))
  expect(await screen.findByTestId('device-key')).toHaveTextContent('rok_abcdefghSECRET')
  fireEvent.click(screen.getByRole('button', { name: 'I have saved the key' }))
  expect(screen.queryByTestId('device-key')).toBeNull()
})
test('project rename sends PATCH with the new name', async () => {
  const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json([{ id: 'p1', name: 'Old' }]))
  render(<MemoryRouter><Projects /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: 'Rename Old' }))
  fireEvent.change(screen.getByLabelText('New name for Old'), { target: { value: 'New' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await vi.waitFor(() => expect(spy).toHaveBeenCalledWith('/api/v1/projects/p1', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ name: 'New' }) })))
})
test('pager shows range and disables at the ends', () => {
  const onChange = vi.fn()
  render(<Pager label="Run" total={45} offset={40} limit={20} onChange={onChange} />)
  expect(screen.getByText('41–45 of 45')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
  expect(onChange).toHaveBeenCalledWith(20)
})
