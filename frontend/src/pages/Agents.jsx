import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorBox, Json, ModelTag, Progress, Tabs, useApi, useInterval, when } from '../components'

export default function Agents(props) {
  const [tab, setTab] = useState(props.params.tab || 'config')
  return (
    <div className="page">
      <div className="page-head"><div><h1>Agents & Datasets</h1>
        <p className="lede">Register agents, configure tools, criteria and quality gates, manage versioned golden datasets and start evaluation runs. Every agent uses the same evaluation infrastructure.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'config', label: 'Agent configuration' }, { id: 'datasets', label: 'Datasets & test cases' }, { id: 'register', label: '+ Register agent' }]} />
      {tab === 'config' && <AgentConfig {...props} />}
      {tab === 'datasets' && <Datasets {...props} />}
      {tab === 'register' && <Register {...props} onDone={() => setTab('config')} />}
    </div>
  )
}

function AgentConfig({ agentId, agents, refreshAgents, go }) {
  const agent = agents.find((a) => a.id === agentId)
  const [cfgId, setCfgId] = useState(null)
  const [gates, setGates] = useState(null)
  const [msg, setMsg] = useState(null)
  const { data: datasets } = useApi(agentId ? `/agents/${agentId}/datasets` : null, [agentId])
  const [dvId, setDvId] = useState('')
  const [run, setRun] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { setCfgId(agent?.current_config_id); setGates(agent?.quality_gates) }, [agentId, agent?.current_config_id])
  useEffect(() => { const g = datasets?.find((d) => d.kind === 'golden'); if (g?.versions.length) setDvId(g.versions[g.versions.length - 1].id) }, [datasets])
  useInterval(async () => {
    if (!run) return
    const r = await api.get(`/runs/${run.id}`)
    setRun(r)
  }, run && run.status !== 'completed' && run.status !== 'failed' ? 700 : null)
  if (!agent) return <Empty>No agent selected. Register one first.</Empty>
  const cfg = agent.configs.find((c) => c.id === cfgId) || agent.configs[agent.configs.length - 1]

  const saveGates = async () => {
    const clean = Object.fromEntries(Object.entries(gates).map(([k, v]) => [k, v === '' ? null : Number(v)]))
    await api.patch(`/agents/${agent.id}`, { quality_gates: clean })
    setMsg('Quality gates saved'); refreshAgents()
  }
  const startRun = async () => {
    setErr(null)
    try { setRun(await api.post('/runs', { agent_id: agent.id, config_id: cfg.id, dataset_version_id: Number(dvId) })) } catch (e) { setErr(e) }
  }
  const versions = (datasets || []).flatMap((d) => d.versions.map((v) => ({ ...v, name: d.name, kind: d.kind })))

  return (
    <>
      <div className="grid-2">
        <Card title={agent.name} subtitle={`${agent.domain} · environment: ${agent.environment} · registered ${when(agent.created_at)} by ${agent.created_by}`}>
          <p>{agent.description}</p>
          <div className="kv"><span>Intended tasks</span><div>{agent.intended_tasks.map((t) => <Badge key={t} tone="blue">{t}</Badge>)}</div></div>
          <div className="kv"><span>Expected outcomes</span><ul>{agent.expected_outcomes.map((t) => <li key={t}>{t}</li>)}</ul></div>
          <div className="kv"><span>Evaluation criteria</span>
            <div>Deterministic: {(agent.evaluation_criteria.deterministic || []).join(', ')}<br />Judge: {(agent.evaluation_criteria.judge || []).join(', ')} (pass ≥ {agent.evaluation_criteria.judge_pass_threshold})
              <br />Weights: {JSON.stringify(agent.evaluation_criteria.weights)}</div></div>
        </Card>
        <Card title="Create evaluation run" subtitle="Run any configuration version against any published dataset version">
          <label className="field">Configuration
            <select value={cfg?.id} onChange={(e) => setCfgId(Number(e.target.value))}>
              {agent.configs.map((c) => <option key={c.id} value={c.id}>v{c.version} · {c.label} ({c.status})</option>)}
            </select></label>
          <label className="field">Dataset version
            <select value={dvId} onChange={(e) => setDvId(e.target.value)} data-testid="dataset-version-select">
              {versions.map((v) => <option key={v.id} value={v.id}>{v.name} · v{v.version} ({v.cases} cases)</option>)}
            </select></label>
          <Button onClick={startRun} disabled={!dvId} data-testid="start-run">Start evaluation run</Button>
          <ErrorBox error={err} />
          {run && (
            <div className="run-status">
              <div>Run #{run.id} <Badge>{run.status}</Badge></div>
              <Progress done={run.progress?.done || 0} total={run.progress?.total || 0} />
              {run.error && <ErrorBox error={run.error.message ? `${run.error.code}: ${run.error.message} — ${run.error.hint || ''}` : String(run.error)} />}
              {run.status === 'completed' && <Button kind="ghost" onClick={() => go('traces', { runId: run.id })}>View results →</Button>}
            </div>
          )}
        </Card>
      </div>
      <Card title={`Configuration v${cfg.version}: ${cfg.label}`} subtitle={`status ${cfg.status} · created by ${cfg.created_by} · ${cfg.change_summary.join('; ')}`}
        actions={<select value={cfg.id} onChange={(e) => setCfgId(Number(e.target.value))}>{agent.configs.map((c) => <option key={c.id} value={c.id}>v{c.version}</option>)}</select>}>
        <div className="grid-2">
          <div>
            <h4>System prompt</h4><div className="prompt">{cfg.system_prompt}</div>
            <h4>Execution settings</h4>
            <div className="muted small">Agent model <ModelTag m={{ provider: cfg.settings.model_provider, model: cfg.settings.model }} /></div>
            <Json value={cfg.settings} />
          </div>
          <div>
            <h4>Tools & APIs</h4>
            <table className="table">
              <thead><tr><th>Tool</th><th>Description given to the agent</th><th>On</th></tr></thead>
              <tbody>{cfg.tools.map((t) => <tr key={t.name}><td><code>{t.name}</code></td><td>{t.description}</td><td>{t.enabled ? '✓' : '—'}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      </Card>
      <Card title="Quality gates" subtitle="Thresholds applied to every run and experiment for this agent" actions={<Button kind="secondary" onClick={saveGates}>Save gates</Button>}>
        {msg && <div className="ok-box">{msg}</div>}
        <div className="gates-form">
          {gates && Object.entries(gates).map(([k, v]) => (
            <label key={k} className="field">{k.replaceAll('_', ' ')}<input value={v ?? ''} onChange={(e) => setGates({ ...gates, [k]: e.target.value })} /></label>
          ))}
        </div>
      </Card>
    </>
  )
}

function Datasets({ agentId }) {
  const { data: datasets, reload } = useApi(agentId ? `/agents/${agentId}/datasets` : null, [agentId])
  const [dsId, setDsId] = useState(null)
  const [filter, setFilter] = useState('all')
  const [open, setOpen] = useState(null)
  const [importText, setImportText] = useState('')
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { if (datasets?.length && !datasets.find((d) => d.id === dsId)) setDsId(datasets[0].id) }, [datasets])
  const { data: cases, reload: reloadCases } = useApi(dsId ? `/datasets/${dsId}/cases` : null, [dsId])
  if (!datasets) return <div className="muted">Loading…</div>
  const ds = datasets.find((d) => d.id === dsId)
  const shown = (cases || []).filter((c) => filter === 'all' || c.category === filter || c.status === filter)
  const refresh = () => { reload(); reloadCases() }
  const act = async (fn, okMsg) => { setErr(null); try { const r = await fn(); setMsg(typeof okMsg === 'function' ? okMsg(r) : okMsg); refresh() } catch (e) { setErr(e) } }

  return (
    <>
      <div className="grid-3">
        {datasets.map((d) => (
          <Card key={d.id} className={d.id === dsId ? 'selected' : 'clickable'} title={d.name} subtitle={`${d.kind} dataset`}>
            <div onClick={() => setDsId(d.id)}>
              <div className="small">{d.description}</div>
              <div className="badges">{Object.entries(d.categories).map(([k, v]) => <Badge key={k} tone="blue">{k}: {v}</Badge>)}
                {d.counts.pending_review ? <Badge tone="amber">pending review: {d.counts.pending_review}</Badge> : null}</div>
              <div className="small muted">Versions: {d.versions.map((v) => `v${v.version} (${v.cases})`).join(', ') || 'none'}</div>
            </div>
          </Card>
        ))}
      </div>
      {ds && (
        <Card title={`${ds.name}: test cases`} subtitle="Generated cases stay pending until a reviewer approves them; only approved cases enter a published version."
          actions={<>
            <Button kind="secondary" onClick={() => act(() => api.post(`/datasets/${ds.id}/generate`, { count: 3 }), (r) => `Generated ${r.added.length} candidate case(s) with ${r.generator}; they await review.`)} data-testid="generate">Generate scenarios</Button>
            <Button onClick={() => act(() => api.post(`/datasets/${ds.id}/versions`, { note: 'Published from UI' }), (r) => `Published version v${r.version} with ${r.cases} approved cases.`)}>Publish new version</Button>
          </>}>
          {msg && <div className="ok-box">{msg}</div>}
          <ErrorBox error={err} />
          <div className="filters">
            {['all', 'normal', 'edge', 'failure', 'adversarial', 'regression', 'pending_review', 'rejected'].map((f) => (
              <button key={f} className={`chip ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>{f.replace('_', ' ')}</button>
            ))}
          </div>
          <table className="table">
            <thead><tr><th>Case</th><th>Category</th><th>Severity</th><th>Request</th><th>Expected</th><th>Source</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {shown.map((c) => (
                <React.Fragment key={c.id}>
                  <tr className="clickable" onClick={() => setOpen(open === c.id ? null : c.id)}>
                    <td><code>{c.case_key}</code></td><td><Badge tone="blue">{c.category}</Badge></td><td><Badge>{c.severity}</Badge></td>
                    <td className="msg">{c.input.message}</td>
                    <td className="small">{c.expected.outcome || (c.expected.outcome_any_of || []).join('/')}{c.expected.required_tools?.length ? ` · ${c.expected.required_tools.join(', ')}` : ''}</td>
                    <td>{c.source}</td><td><Badge>{c.status}</Badge></td>
                    <td onClick={(e) => e.stopPropagation()}>{c.status === 'pending_review' && <>
                      <Button kind="ghost" onClick={() => act(() => api.post(`/cases/${c.id}/review`, { decision: 'approve' }), `${c.case_key} approved`)}>Approve</Button>
                      <Button kind="ghost" onClick={() => act(() => api.post(`/cases/${c.id}/review`, { decision: 'reject' }), `${c.case_key} rejected`)}>Reject</Button></>}</td>
                  </tr>
                  {open === c.id && <tr><td colSpan={8}><div className="grid-2"><div><h4>Expected behaviour</h4><Json value={c.expected} /></div>
                    <div><h4>Input, faults & origin</h4><Json value={{ input: c.input, faults: c.faults, origin: c.origin, tags: c.tags }} /></div></div></td></tr>}
                </React.Fragment>
              ))}
            </tbody>
          </table>
          <details className="import">
            <summary>Import cases (JSON)</summary>
            <textarea rows={6} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder='[{"case_key":"IT-100","category":"edge","input":{"employee_id":"E1001","message":"..."},"expected":{"outcome":"resolved","required_tools":["check_vpn_status"]}}]' />
            <Button kind="secondary" onClick={() => act(async () => api.post(`/datasets/${ds.id}/import`, { cases: JSON.parse(importText) }),
              (r) => `Imported ${r.added.length}; rejected ${r.errors.length}${r.errors.length ? ': ' + JSON.stringify(r.errors) : ''}`)}>Validate & import</Button>
          </details>
        </Card>
      )}
    </>
  )
}

const EMPTY = { name: '', domain: '', description: '', environment: 'it_service_desk', intended_tasks: '', expected_outcomes: '', system_prompt: '', tools: [], settings: '{}', quality_gates: {}, evaluation_criteria: {}, template_id: null }

function Register({ agents, refreshAgents, setAgentId, onDone }) {
  const { data: templates } = useApi('/templates')
  const { data: envs } = useApi('/environments')
  const [f, setF] = useState(EMPTY)
  const [err, setErr] = useState(null)
  const loadTemplate = (id) => {
    const t = templates.find((x) => x.template_id === id).template
    setF({ name: t.name, domain: t.domain, description: t.description, environment: t.environment, intended_tasks: t.intended_tasks.join('\n'),
      expected_outcomes: t.expected_outcomes.join('\n'), system_prompt: t.baseline_config.system_prompt, tools: t.baseline_config.tools,
      settings: JSON.stringify(t.baseline_config.settings, null, 2), quality_gates: t.quality_gates, evaluation_criteria: t.evaluation_criteria, template_id: t.template_id })
  }
  const env = envs?.find((e) => e.name === f.environment)
  const existing = agents.find((a) => a.name.trim().toLowerCase() === f.name.trim().toLowerCase())
  useEffect(() => {
    if (env && !f.template_id && f.tools.length === 0) setF((x) => ({ ...x, tools: env.tools.filter((t) => t.name !== 'grant_admin_access').map((t) => ({ name: t.name, enabled: true, description: t.default_description })) }))
  }, [env])
  const submit = async () => {
    setErr(null)
    try {
      const a = await api.post('/agents', { ...f, intended_tasks: f.intended_tasks.split('\n').filter(Boolean), expected_outcomes: f.expected_outcomes.split('\n').filter(Boolean),
        settings: JSON.parse(f.settings), load_template_dataset: !!f.template_id })
      refreshAgents(); setAgentId(a.id); onDone()
    } catch (e) { setErr(e) }
  }
  const setTool = (i, patch) => setF({ ...f, tools: f.tools.map((t, j) => (j === i ? { ...t, ...patch } : t)) })
  return (
    <Card title="Register an AI agent" subtitle="Templates pre-fill a demonstration agent and load its curated golden dataset; every field can be edited."
      actions={<select onChange={(e) => e.target.value && loadTemplate(e.target.value)} defaultValue="" data-testid="template-select">
        <option value="">Load template…</option>{(templates || []).map((t) => <option key={t.template_id} value={t.template_id}>{t.name}{agents.some((a) => a.template_id === t.template_id) ? ' (already registered)' : ''}</option>)}</select>}>
      <div className="grid-2">
        <div>
          <label className="field">Agent name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} data-testid="agent-name" /></label>
          <label className="field">Business domain<input value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} /></label>
          <label className="field">Environment (simulated enterprise APIs)
            <select value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value, tools: [] })}>{(envs || []).map((e) => <option key={e.name} value={e.name}>{e.name}</option>)}</select></label>
          <label className="field">Description<textarea rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
          <label className="field">Intended tasks (one per line)<textarea rows={4} value={f.intended_tasks} onChange={(e) => setF({ ...f, intended_tasks: e.target.value })} /></label>
          <label className="field">Expected outcomes (one per line)<textarea rows={3} value={f.expected_outcomes} onChange={(e) => setF({ ...f, expected_outcomes: e.target.value })} /></label>
        </div>
        <div>
          <label className="field">System prompt<textarea rows={6} value={f.system_prompt} onChange={(e) => setF({ ...f, system_prompt: e.target.value })} /></label>
          <label className="field">Execution settings (JSON; model_provider: simlm | ollama | anthropic | openai | gemini)<textarea rows={8} className="mono" value={f.settings} onChange={(e) => setF({ ...f, settings: e.target.value })} /></label>
          <div className="kv"><span>Quality gates</span><Json value={f.quality_gates} /></div>
        </div>
      </div>
      <h4>Tools and APIs available to the agent</h4>
      <table className="table">
        <thead><tr><th>On</th><th>Tool</th><th>Description shown to the agent</th></tr></thead>
        <tbody>{f.tools.map((t, i) => (
          <tr key={t.name}><td><input type="checkbox" checked={t.enabled} onChange={(e) => setTool(i, { enabled: e.target.checked })} /></td><td><code>{t.name}</code></td>
            <td><input className="wide" value={t.description} onChange={(e) => setTool(i, { description: e.target.value })} /></td></tr>))}</tbody>
      </table>
      {existing && <div className="warn-box" data-testid="duplicate-agent">“{existing.name}” is already registered (current version v{existing.current_config_version}). Each agent is registered once; improvements are added to it as new versions.{' '}
        <Button kind="ghost" onClick={() => { setAgentId(existing.id); onDone() }}>Open it →</Button></div>}
      <ErrorBox error={err} />
      <Button onClick={submit} disabled={!f.name || !f.system_prompt || !!existing} data-testid="register-agent">Register agent{f.template_id ? ' and load golden dataset' : ''}</Button>
    </Card>
  )
}
