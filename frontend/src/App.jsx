import React, { useEffect, useState } from 'react'
import { api, getIdentity, setIdentity } from './api'
import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import Traces from './pages/Traces'
import Improve from './pages/Improve'
import Regression from './pages/Regression'
import Settings from './pages/Settings'

const NAV = [
  { id: 'dashboard', label: 'Quality Dashboard', icon: '◧' },
  { id: 'agents', label: 'Agents & Datasets', icon: '⚙' },
  { id: 'traces', label: 'Traces & Failure Analysis', icon: '⌕' },
  { id: 'improve', label: 'Recommendations & Experiments', icon: '⚗' },
  { id: 'regression', label: 'Regression & Learning', icon: '↻' },
  { id: 'settings', label: 'Settings · LLM Providers', icon: '⚿' },
]

function readPref(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value) } catch { /* storage unavailable */ }
}

export default function App() {
  const [nav, setNav] = useState({ page: 'dashboard', params: {} })
  const [me, setMe] = useState(null)
  const [identity, setId] = useState({ tenant: readPref('ah.tenant', 'acme'), user: readPref('ah.user', 'maya') })
  const [agentId, setAgentId] = useState(null)
  const [agents, setAgents] = useState([])
  const [version, setVersion] = useState(0)

  useEffect(() => { setIdentity(identity); writePref('ah.tenant', identity.tenant); writePref('ah.user', identity.user) }, [identity])
  useEffect(() => {
    setIdentity(identity)
    api.get('/me').then(setMe).catch(() => setMe(null))
    api.get('/agents').then((a) => {
      setAgents(a)
      if (!a.find((x) => x.id === agentId)) setAgentId(a[0]?.id ?? null)
    })
  }, [identity, version])

  const go = (page, params = {}) => { setNav({ page, params }); window.scrollTo(0, 0) }
  const refreshAgents = () => setVersion((v) => v + 1)
  const shared = { go, params: nav.params, agentId, setAgentId, agents, refreshAgents, me, identity }
  const Page = { dashboard: Dashboard, agents: Agents, traces: Traces, improve: Improve, regression: Regression, settings: Settings }[nav.page]

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand" onClick={() => go('dashboard')}>
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><path d="M2 28 L12 10 L18 20 L22 14 L30 28 Z" fill="#818cf8" /><path d="M12 10 L15 15 L9 15 Z" fill="#e0e7ff" /></svg>
          <div><div className="brand-name">AgentHill</div><div className="brand-tag">Agent evaluation & improvement</div></div>
        </div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} data-nav={n.id} className={nav.page === n.id ? 'active' : ''} onClick={() => go(n.id)}>
              <span className="icon">{n.icon}</span>{n.label}
            </button>
          ))}
        </nav>
        <div className="loop-hint">
          <div className="muted small">Improvement loop</div>
          <ol className="loop">
            <li>Evaluate</li><li>Diagnose</li><li>Recommend</li><li>Experiment</li><li>Gate</li><li>Learn</li>
          </ol>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="agent-picker">
            <label>Agent</label>
            <select value={agentId ?? ''} onChange={(e) => setAgentId(Number(e.target.value))} data-testid="agent-select">
              {agents.length === 0 && <option value="">No agents registered</option>}
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name} · v{a.current_config_version}</option>)}
            </select>
          </div>
          <div className="identity">
            <label>Customer</label>
            <select value={identity.tenant} onChange={(e) => setId({ ...identity, tenant: e.target.value })} data-testid="tenant-select">
              {(me?.tenants || [{ id: 'acme', name: 'Acme Corp' }]).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <label>Signed in as</label>
            <select value={identity.user} onChange={(e) => setId({ ...identity, user: e.target.value })} data-testid="user-select">
              {(me?.users || []).map((u) => <option key={u.id} value={u.id}>{u.name} — {u.title}</option>)}
            </select>
          </div>
        </header>
        <div className="content">
          <Page key={`${nav.page}-${identity.tenant}`} {...shared} />
        </div>
      </main>
    </div>
  )
}

export { getIdentity }
