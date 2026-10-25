import React, { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorBox, ModelTag, Progress, Stat, ms, num, pct, useApi, useInterval, when } from '../components'

export default function Dashboard({ go, agentId, agents }) {
  const [configId, setConfigId] = useState('')
  const [runId, setRunId] = useState('')
  const [scope, setScope] = useState('agent')
  const filterAgent = scope === 'agent' ? agentId : null
  const qs = new URLSearchParams()
  if (filterAgent) qs.set('agent_id', filterAgent)
  if (configId) qs.set('config_id', configId)
  if (runId) qs.set('run_id', runId)
  const { data, error, reload } = useApi(`/dashboard?${qs}`, [filterAgent, configId, runId])
  const [running, setRunning] = useState(null)
  const [runErr, setRunErr] = useState(null)
  const agent = agents.find((a) => a.id === agentId)

  useEffect(() => { setConfigId(''); setRunId('') }, [agentId])
  useInterval(async () => {
    if (!running) return
    const r = await api.get(`/runs/${running.id}`)
    setRunning(r)
    if (r.status === 'completed' || r.status === 'failed') { setRunning(null); setRunId(''); reload() }
  }, running ? 700 : null)

  const startRun = async () => {
    setRunErr(null)
    const ds = (await api.get(`/agents/${agentId}/datasets`)).find((d) => d.kind === 'golden')
    const v = ds?.versions[ds.versions.length - 1]
    if (!v) { setRunErr('This agent has no published dataset version yet.'); return }
    try { setRunning(await api.post('/runs', { agent_id: agentId, dataset_version_id: v.id, purpose: 'baseline' })) } catch (e) { setRunErr(e) }
  }

  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="muted">Loading…</div>
  const sel = data.selected_run
  const m = sel?.metrics || {}
  const verdict = sel?.gate_verdict
  const trend = data.runs.map((r) => ({ name: `#${r.id} v${r.config_version}`, 'Task success': +(r.metrics.task_success_rate * 100).toFixed(1),
    'Quality score': +(r.metrics.quality_score * 100).toFixed(1), Critical: r.metrics.critical_failures }))
  const dist = Object.entries(data.failure_distribution || {}).map(([k, v]) => ({ category: k.replaceAll('_', ' '), cases: v })).sort((a, b) => b.cases - a.cases)
  const lastExp = data.experiments.find((e) => e.status === 'completed')

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Quality Dashboard</h1>
          <p className="lede">Every number on this page is computed from persisted evaluation runs. AgentHill shortens the time from an agent change to an evidence-backed quality verdict.</p>
        </div>
        <div className="head-actions">
          {agent && <Button onClick={startRun} busy={!!running} data-testid="run-eval">Run evaluation · {agent.name.split(' ').slice(0, 2).join(' ')}</Button>}
        </div>
      </div>
      <ErrorBox error={runErr} />
      {running && (
        <Card title={`Evaluation run #${running.id} in progress`} subtitle={`${running.config_label} · dataset v${running.dataset_version}`}>
          <Progress done={running.progress?.done || 0} total={running.progress?.total || 0} />
        </Card>
      )}

      <div className="filters">
        <label>Scope
          <select value={scope} onChange={(e) => setScope(e.target.value)} data-testid="scope-select">
            <option value="agent">Selected agent</option><option value="all">All agents</option>
          </select>
        </label>
        {scope === 'agent' && agent && (
          <label>Configuration
            <select value={configId} onChange={(e) => { setConfigId(e.target.value); setRunId('') }}>
              <option value="">All versions</option>
              {agent.configs.map((c) => <option key={c.id} value={c.id}>v{c.version} · {c.label}</option>)}
            </select>
          </label>
        )}
        <label>Evaluation run
          <select value={runId || sel?.id || ''} onChange={(e) => setRunId(e.target.value)} data-testid="run-select">
            {data.runs.length === 0 && <option value="">No runs</option>}
            {[...data.runs].reverse().map((r) => <option key={r.id} value={r.id}>#{r.id} · {r.purpose.replaceAll('_', ' ')} · v{r.config_version} · {when(r.finished_at)}</option>)}
          </select>
        </label>
      </div>

      {!sel ? (
        <Card><Empty>No completed evaluation runs yet. Use <b>Run evaluation</b> to execute the agent against its golden dataset.</Empty></Card>
      ) : (
        <>
          <div className="stats" data-testid="kpis">
            <Stat label="Task success" value={pct(m.task_success_rate)} hint={`${m.passed}/${m.total_cases} cases`} tone={m.task_success_rate >= 0.85 ? 'good' : 'bad'} />
            <Stat label="Quality score" value={pct(m.quality_score)} hint="60% deterministic · 40% judge" />
            <Stat label="Tool selection accuracy" value={pct(m.tool_selection_accuracy)} />
            <Stat label="Critical failures" value={num(m.critical_failures)} tone={m.critical_failures ? 'bad' : 'good'} />
            <Stat label="Regressions (last experiment)" value={lastExp ? num(lastExp.regressed) : '—'} hint={lastExp ? lastExp.name : 'no experiment yet'} />
            <Stat label="Evaluation duration" value={ms(m.evaluation_duration_ms)} hint={`p95 agent latency ${ms(m.latency_p95_ms)}`} />
          </div>
          <div className="grid-2">
            <Card title="Current quality verdict" subtitle={`Run #${sel.id} · ${sel.config_label}`}
              actions={<Button kind="ghost" onClick={() => go('traces', { runId: sel.id })}>Inspect run →</Button>}>
              <div className="verdict-row">
                <Badge>{verdict?.verdict || 'n/a'}</Badge>
                <span>{verdict?.summary}</span>
              </div>
              <table className="mini">
                <tbody>
                  {(verdict?.gates || []).map((g) => (
                    <tr key={g.gate}><td>{g.label}</td><td>{g.operator} {g.threshold}</td><td>{typeof g.value === 'number' ? num(g.value, 3) : '—'}</td><td><Badge>{g.status}</Badge></td></tr>
                  ))}
                </tbody>
              </table>
              <p className="disclaimer">{verdict?.disclaimer}</p>
              <div className="muted small">Agent model <ModelTag m={sel.models?.agent} /> · Judge <ModelTag m={sel.models?.judge} />
                {sel.models?.same_model_agent_and_judge && <Badge tone="amber">same model as judge</Badge>}</div>
            </Card>
            <Card title="Failure distribution" subtitle={`Run #${sel.id} · source: ${data.failure_distribution_source}`}>
              {dist.length === 0 ? <Empty>No failures in this run.</Empty> : (
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={dist} layout="vertical" margin={{ left: 30, right: 16 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" allowDecimals={false} />
                    <YAxis type="category" dataKey="category" width={160} tick={{ fontSize: 12 }} />
                    <Tooltip />
                    <Bar dataKey="cases" fill="#ef4444" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Card>
          </div>
          <Card title="Quality across evaluation runs" subtitle="Task success and quality score per run (filtered)">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trend} margin={{ left: 0, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} unit="%" />
                <Tooltip /><Legend />
                <Line type="monotone" dataKey="Task success" stroke="#4f46e5" strokeWidth={2} />
                <Line type="monotone" dataKey="Quality score" stroke="#10b981" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </>
      )}

      <div className="grid-2">
        <Card title="Registered agents" subtitle="Each agent's latest run on its current configuration">
          <table className="table">
            <thead><tr><th>Agent</th><th>Domain</th><th>Config</th><th>Runs</th><th>Task success</th><th>Verdict</th></tr></thead>
            <tbody>
              {data.agents.map((a) => (
                <tr key={a.id}><td>{a.name}</td><td>{a.domain}</td><td>v{a.current_config_version}</td><td>{a.runs}</td>
                  <td>{pct(a.task_success_rate)}</td><td>{a.verdict ? <Badge>{a.verdict}</Badge> : '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Experiment history" actions={<Button kind="ghost" onClick={() => go('regression')}>Open history →</Button>}>
          {data.experiments.length === 0 ? <Empty>No experiments yet.</Empty> : (
            <table className="table">
              <thead><tr><th>#</th><th>Experiment</th><th>Fixed</th><th>Regressed</th><th>Gates</th><th>Decision</th></tr></thead>
              <tbody>
                {data.experiments.slice(0, 6).map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => go('improve', { experimentId: e.id })}>
                    <td>{e.id}</td><td>{e.name}</td><td className="pos">{e.fixed}</td><td className={e.regressed ? 'neg' : ''}>{e.regressed}</td>
                    <td>{e.verdict ? <Badge>{e.verdict}</Badge> : <Badge>{e.status}</Badge>}</td><td>{e.decision ? <Badge>{e.decision}</Badge> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Evaluation runs">
        <table className="table">
          <thead><tr><th>Run</th><th>Agent config</th><th>Dataset</th><th>Purpose</th><th>Agent model</th><th>Task success</th><th>Critical</th><th>Duration</th><th></th></tr></thead>
          <tbody>
            {[...data.runs].reverse().map((r) => (
              <tr key={r.id}>
                <td>#{r.id}</td><td>v{r.config_version} · {r.config_label}</td><td>{r.dataset_name} v{r.dataset_version}</td>
                <td>{r.purpose.replaceAll('_', ' ')}</td><td><ModelTag m={r.models?.agent} /></td>
                <td>{pct(r.metrics.task_success_rate)}</td><td>{r.metrics.critical_failures}</td><td>{ms(r.metrics.evaluation_duration_ms)}</td>
                <td><Button kind="ghost" onClick={() => go('traces', { runId: r.id })}>Traces</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
