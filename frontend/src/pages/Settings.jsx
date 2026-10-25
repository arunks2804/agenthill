import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card, ErrorBox, ModelTag, Tabs, num, useApi } from '../components'

export default function Settings({ agents, refreshAgents, go }) {
  const { data, reload, error } = useApi('/settings/llm')
  const [tab, setTab] = useState('providers')
  if (error) return <ErrorBox error={error} />
  if (!data) return <div className="muted">Loading…</div>
  return (
    <div className="page">
      <div className="page-head"><div><h1>Settings · LLM Providers</h1>
        <p className="lede">Connect Ollama, Anthropic Claude, OpenAI and Google Gemini through one model layer, and choose models independently for agent execution, evaluation judging and failure analysis. Changes apply immediately without a restart.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'providers', label: 'Providers & credentials' }, { id: 'roles', label: 'Model roles' }, { id: 'pricing', label: 'Pricing' }]} />
      {tab === 'providers' && <div className="grid-2">{data.providers.map((p) => <ProviderCard key={p.name} p={p} onSaved={reload} />)}</div>}
      {tab === 'roles' && <Roles data={data} agents={agents} reload={reload} refreshAgents={refreshAgents} go={go} />}
      {tab === 'pricing' && <Pricing data={data} reload={reload} />}
    </div>
  )
}

function ProviderCard({ p, onSaved }) {
  const [baseUrl, setBaseUrl] = useState(p.settings.base_url || '')
  const [key, setKey] = useState('')
  const [model, setModel] = useState('')
  const [test, setTest] = useState(null)
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const save = async () => {
    setErr(null)
    try {
      await api.put(`/settings/llm/providers/${p.name}`, { base_url: p.local ? baseUrl : undefined, api_key: key || undefined })
      setKey(''); setMsg(key ? 'API key stored encrypted on the server. It is never shown again.' : 'Saved.'); onSaved()
    } catch (e) { setErr(e) }
  }
  const remove = async () => { await api.del(`/settings/llm/providers/${p.name}/key`); setMsg('Stored key removed.'); onSaved() }
  const runTest = async () => {
    setTest({ running: true }); setErr(null)
    try { setTest(await api.post(`/settings/llm/providers/${p.name}/test`, { model: model || null, base_url: p.local ? baseUrl : null, api_key: key || null })) } catch (e) { setErr(e); setTest(null) }
  }
  return (
    <Card title={p.label} subtitle={p.local ? 'Local inference · no API key required' : 'Cloud API'}
      actions={<Badge tone={p.credentials.configured ? 'green' : 'gray'}>{p.credentials.configured ? `ready · ${p.credentials.source}` : 'not configured'}</Badge>}>
      {p.local && <label className="field">Server URL<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} data-testid={`${p.name}-url`} />
        <span className="small muted">Default http://localhost:11434. From Docker use http://host.docker.internal:11434.</span></label>}
      {p.requires_api_key && <label className="field">API key<input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={p.credentials.configured ? '•••••••• (stored — enter a new key to replace)' : 'Paste API key'} data-testid={`${p.name}-key`} />
        <span className="small muted">Sent once to the backend, encrypted at rest, never returned to the browser, never written to traces or run records.</span></label>}
      <label className="field">Model to test (optional)<input value={model} onChange={(e) => setModel(e.target.value)} list={`models-${p.name}`} placeholder={p.local ? 'e.g. llama3.1:8b' : 'model ID'} data-testid={`${p.name}-model`} />
        <datalist id={`models-${p.name}`}>{(test?.models || []).map((m) => <option key={m} value={m} />)}</datalist></label>
      <div className="row-actions">
        <Button kind="secondary" onClick={runTest} busy={test?.running} data-testid={`${p.name}-test`}>Test connection</Button>
        <Button onClick={save} data-testid={`${p.name}-save`}>Save</Button>
        {p.requires_api_key && p.credentials.source === 'encrypted store' && <Button kind="ghost" onClick={remove}>Remove stored key</Button>}
      </div>
      {msg && <div className="ok-box">{msg}</div>}<ErrorBox error={err} />
      {test && !test.running && (
        test.ok ? <div className="ok-box" data-testid={`${p.name}-test-ok`}>Connected. {test.checks.map((c) => `${c.check.replace('_', ' ')}: ${c.detail}`).join(' · ')}
          {test.models?.length > 0 && <div className="small">Available models: {test.models.slice(0, 12).join(', ')}{test.models.length > 12 ? ` (+${test.models.length - 12})` : ''}</div>}</div>
          : <div className="error-box" data-testid={`${p.name}-test-error`}><b>{test.error.code}</b>: {test.error.message}<div className="small">{test.error.hint}</div></div>
      )}
    </Card>
  )
}

function RoleEditor({ role, title, desc, value, providers, onSave }) {
  const [provider, setProvider] = useState(value.provider)
  const [model, setModel] = useState(value.model)
  const [models, setModels] = useState([])
  const [check, setCheck] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setModels([])
    if (provider !== 'deterministic') api.get(`/settings/llm/providers/${provider}/models`).then((d) => setModels(d.models)).catch(() => setModels([]))
  }, [provider])
  const validate = async () => { setCheck({ running: true }); setCheck(await api.get(`/settings/llm/compatibility?provider=${provider}&model=${encodeURIComponent(model)}&role=${role}`)) }
  const save = async () => {
    setErr(null)
    try { await onSave({ [role]: { provider, model: provider === 'deterministic' ? (role === 'judge' ? 'rubric-v1' : 'evidence-rules-v1') : model } }) } catch (e) { setErr(e) }
  }
  return (
    <Card title={title} subtitle={desc} actions={<ModelTag m={value} />}>
      <label className="field">Provider
        <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(''); setCheck(null) }} data-testid={`${role}-provider`}>
          <option value="deterministic">Built-in deterministic ({role === 'judge' ? 'rubric judge' : 'evidence rules only'})</option>
          {providers.map((p) => <option key={p.name} value={p.name} disabled={!p.credentials.configured}>{p.label}{p.credentials.configured ? '' : ' (not configured)'}</option>)}
        </select></label>
      {provider !== 'deterministic' && <label className="field">Model<input list={`${role}-models`} value={model} onChange={(e) => setModel(e.target.value)} data-testid={`${role}-model`} />
        <datalist id={`${role}-models`}>{models.map((m) => <option key={m} value={m} />)}</datalist></label>}
      <div className="row-actions">
        {provider !== 'deterministic' && <Button kind="secondary" onClick={validate} disabled={!model}>Validate structured output</Button>}
        <Button onClick={save} disabled={provider !== 'deterministic' && !model} data-testid={`${role}-save`}>Save {role} model</Button>
      </div>
      {check && !check.running && (check.ok ? <div className="ok-box">{check.detail}</div> : <ErrorBox error={`${check.error.code}: ${check.error.message} — ${check.error.hint}`} />)}
      <ErrorBox error={err} />
    </Card>
  )
}

function Roles({ data, agents, reload, refreshAgents, go }) {
  const saveRoles = async (body) => { await api.put('/settings/llm/roles', body); reload() }
  const judge = data.model_roles.judge
  const sameAs = data.agent_models.filter((a) => a.provider === judge.provider && a.model === judge.model)
  return (
    <>
      {sameAs.length > 0 && <div className="warn-box">The judge model is the same model that runs {sameAs.map((a) => a.agent).join(', ')}. Self-preference bias is possible; such runs are flagged. Deterministic checks are unaffected.</div>}
      <div className="grid-3">
        <Card title="Agent execution" subtitle="Stored per agent configuration version, so every run records exactly which model produced it">
          <table className="mini"><tbody>{data.agent_models.map((a) => <tr key={a.agent_id}><td>{a.agent}</td><td>v{a.config_version}</td><td><ModelTag m={a} /></td></tr>)}</tbody></table>
          <AgentModelForm agents={agents} providers={data.providers} onDone={() => { reload(); refreshAgents() }} />
          <Button kind="ghost" onClick={() => go('improve', { tab: 'models' })}>Compare models on a dataset →</Button>
        </Card>
        <RoleEditor role="judge" title="LLM evaluation judge" desc="Scores correctness, relevance, completeness, grounding and uncertainty handling. Deterministic checks always run regardless of this choice."
          value={judge} providers={data.providers} onSave={saveRoles} />
        <RoleEditor role="analyzer" title="Failure analysis & recommendations" desc="Evidence rules always run; an LLM adds a reviewed narrative that must cite trace evidence IDs. Also used for scenario generation."
          value={data.model_roles.analyzer} providers={data.providers} onSave={saveRoles} />
      </div>
    </>
  )
}

function AgentModelForm({ agents, providers, onDone }) {
  const [agentId, setAgentId] = useState(agents[0]?.id)
  const [provider, setProvider] = useState('ollama')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { if (provider === 'simlm') { setModels(['simlm-1']); return } api.get(`/settings/llm/providers/${provider}/models`).then((d) => setModels(d.models)).catch(() => setModels([])) }, [provider])
  const create = async () => {
    setErr(null)
    try {
      const c = await api.post(`/agents/${agentId}/configs`, { label: `${provider}:${model}`, settings: { model_provider: provider, model } })
      setMsg(`Created configuration v${c.version} (${provider}:${model}). Run it from Agents & Datasets or compare it in an experiment.`); onDone()
    } catch (e) { setErr(e) }
  }
  return (
    <div className="subform">
      <h4>New configuration with a different model</h4>
      <select value={agentId} onChange={(e) => setAgentId(Number(e.target.value))}>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
      <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel('') }} data-testid="agent-model-provider">
        <option value="simlm">SimLM (deterministic)</option>
        {providers.map((p) => <option key={p.name} value={p.name} disabled={!p.credentials.configured}>{p.label}</option>)}</select>
      <input list="agent-models" value={model} onChange={(e) => setModel(e.target.value)} placeholder="model" data-testid="agent-model-name" />
      <datalist id="agent-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
      <Button kind="secondary" onClick={create} disabled={!model}>Create config version</Button>
      {msg && <div className="ok-box">{msg}</div>}<ErrorBox error={err} />
    </div>
  )
}

function Pricing({ data, reload }) {
  const [rows, setRows] = useState(Object.entries(data.pricing).map(([k, v]) => ({ key: k, ...v })))
  const [err, setErr] = useState(null)
  const save = async () => {
    setErr(null)
    try { await api.put('/settings/llm/pricing', Object.fromEntries(rows.filter((r) => r.key).map((r) => [r.key, { input_per_mtok: Number(r.input_per_mtok), output_per_mtok: Number(r.output_per_mtok) }]))); reload() } catch (e) { setErr(e) }
  }
  return (
    <Card title="Model pricing used for cost estimates" subtitle="AgentHill does not ship provider prices. Enter the current price from the provider's pricing page; runs without a configured price report cost as “not measured”. Local Ollama inference is always reported as not measured."
      actions={<><Button kind="ghost" onClick={() => setRows([...rows, { key: '', input_per_mtok: 0, output_per_mtok: 0 }])}>Add row</Button><Button onClick={save}>Save pricing</Button></>}>
      <table className="table"><thead><tr><th>provider:model</th><th>USD per 1M input tokens</th><th>USD per 1M output tokens</th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>
          <td><input value={r.key} placeholder="anthropic:claude-…" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} /></td>
          <td><input value={r.input_per_mtok} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, input_per_mtok: e.target.value } : x)))} /></td>
          <td><input value={r.output_per_mtok} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, output_per_mtok: e.target.value } : x)))} /></td></tr>)}</tbody></table>
      <div className="small muted">{num(rows.length)} priced model(s). SimLM costs are modeled from token estimates for comparison only.</div>
      <ErrorBox error={err} />
    </Card>
  )
}
