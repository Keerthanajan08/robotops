import { Navigate, NavLink, Route, Routes, Outlet, useNavigate } from 'react-router-dom'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Projects from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import Devices from './pages/Devices'
import RunDetail from './pages/RunDetail'
import DeviceDetail from './pages/DeviceDetail'
import Simulations from './pages/Simulations'
import Settings from './pages/Settings'
import Compare from './pages/Compare'

function Shell() {
  const nav = useNavigate()
  if (!localStorage.getItem('token')) return <Navigate to="/login" replace />
  const link = ({ isActive }: { isActive: boolean }) => `px-3 py-1 rounded text-sm ${isActive ? 'bg-slate-900 text-white' : 'hover:bg-slate-200'}`
  return (
    <div>
      <header className="flex flex-wrap items-center gap-2 border-b bg-white px-4 py-2">
        <strong className="mr-4">RobotOps</strong>
        <nav aria-label="Main" className="flex gap-1">
          <NavLink to="/" end className={link}>Dashboard</NavLink>
          <NavLink to="/projects" className={link}>Projects</NavLink>
          <NavLink to="/devices" className={link}>Devices</NavLink>
          <NavLink to="/simulations" className={link}>Simulations</NavLink>
          <NavLink to="/compare" className={link}>Compare</NavLink>
          <NavLink to="/settings" className={link}>Settings</NavLink>
        </nav>
        <span className="ml-auto rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Simulated devices only</span>
        <button className="text-sm underline" onClick={() => { localStorage.removeItem('token'); nav('/login') }}>Log out</button>
      </header>
      <main className="mx-auto max-w-5xl p-4"><Outlet /></main>
    </div>
  )
}
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="projects" element={<Projects />} />
        <Route path="projects/:id" element={<ProjectDetail />} />
        <Route path="devices" element={<Devices />} />
        <Route path="devices/:id" element={<DeviceDetail />} />
        <Route path="simulations" element={<Simulations />} />
        <Route path="settings" element={<Settings />} />
        <Route path="compare" element={<Compare />} />
        <Route path="runs/:id" element={<RunDetail />} />
      </Route>
    </Routes>
  )
}
