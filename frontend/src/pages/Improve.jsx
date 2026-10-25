import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorBox, ModelTag, Progress, Tabs, delta, ms, num, pct, useApi, useInterval, usd, when } from '../components'

export default function Improve({ go, params, agentId, agents, refreshAgents, me }) {
  const [tab, setTab] = useState(params.experimentId ? 'experiments' : params.tab || 'recs')
  const [expId, setExpId] = useState(params.experimentId || null)
  const openExp = (id) => { setExpId(id); setTab('experiments') }
  return (
    <div className="page">
      <div className="page-head"><div><h1>Recommendations & Experiments</h1>
        <p className="lede">Recommendations are hypotheses. Approve a change, test it against the preserved baseline on the same dataset, and decide with measured evidence. Nothing is deployed to production from here.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'recs', label: 'Prioritized recommendations' }, { id: 'experiments', label: 'Experiments' }, { id: 'models', label: 'Compare models' }]} />
      {tab === 'recs' && <Recommendations agentId={agentId} onExperiment={openExp} me={me} />}
      {tab === 'experiments' && <Experiments agentId={agentId} expId={expId} setExpId={setExpId} refreshAgents={refreshAgents} go={go} />}
      {tab === 'models' && <CompareModels agentId={agentId} agents={agents} onExperiment={openExp} />}
    </div>
  )
}

function PatchView({ p }) {
  if (p.type === 'prompt_append') {
    const added = p.after.slice(p.before.length).trim()
    return <div className="patch"><div className="small muted">System prompt — append</div><div className="diff-add">+ {added}</div></div>
  }
  if (p.type === 'tool_description') {
    return <div className="patch"><div className="small muted">Tool description · <code>{p.target}</code></div>
      <div className="diff-del">− {p.before}</div><div className="diff-add">+ {p.after}</div></div>
  }
  return <div className="patch"><div className="small muted">Setting · <code>{p.target}</code></div>
    <div className="diff-del">− {JSON.stringify(p.before)}</div><div className="diff-add">+ {JSON.stringify(p.after)}</div></div>
}

function Recommendations({ agentId, onExperiment }) {
  const { data: recs, reload } = useApi(agentId ? `/recommendations?agent_id=${agentId}` : null, [agentId])
  const [selected, setSelected] = useState([])
  const [name, setName] = useState('')
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)
  if (!recs) return <div className="muted">Loading…</div>
  if (!recs.length) return <Card><Empty>No recommendations yet. Run a failure analysis on an evaluation run first.</Empty></Card>
  const latestAnalysis = Math.max(...recs.map((r) => r.analysis_id))
  const current = recs.filter((r) => r.analysis_id === latestAnalysis)
  const review = async (r, decision) => { setErr(null); try { await api.post(`/recommendations/${r.id}/review`, { decision, note: decision === 'approve' ? 'Evidence reviewed; approve for experimentation' : 'Not pursuing' }); reload() } catch (e) { setErr(e) } }
  const create = async () => {
    setErr(null)
    try {
      const e = await api.post('/experiments', { agent_id: agentId, name: name || current.filter((r) => selected.includes(r.id)).map((r) => r.title).join(' + ').slice(0, 120), recommendation_ids: selected })
      setSelected([]); onExperiment(e.id)
    } catch (e) { setErr(e) }
  }
  return (
    <>
      <div className="muted small">From failure analysis of run #{current[0]?.run_id} · priority = frequency × severity × business impact × confidence</div>
      <ErrorBox error={err} />
      {current.map((r) => (
        <Card key={r.id} className={selected.includes(r.id) ? 'selected' : ''} title={<><Badge>{r.priority}</Badge> {r.title}</>}
          subtitle={`${r.failure_description} · priority score ${r.priority_score}`}
          actions={<>
            <Badge title="confidence">{`confidence ${r.confidence}`}</Badge><Badge>{r.status}</Badge>
            {r.status === 'proposed' && <><Button kind="secondary" onClick={() => review(r, 'approve')} data-testid={`approve-rec-${r.id}`}>Approve change</Button>
              <Button kind="ghost" onClick={() => review(r, 'reject')}>Reject</Button></>}
            {r.status === 'approved' && r.proposed_change.patches.length > 0 && (
              <label className="check"><input type="checkbox" checked={selected.includes(r.id)} data-testid={`select-rec-${r.id}`}
                onChange={(e) => setSelected(e.target.checked ? [...selected, r.id] : selected.filter((x) => x !== r.id))} /> include in experiment</label>)}
          </>}>
          <div className="grid-2">
            <div>
              <h4>Probable root cause</h4><div className="hyp">{r.root_cause}</div>
              <h4>Expected impact</h4><div>{r.expected_impact}</div>
              <div className="small muted">Affected cases: {r.affected_cases.join(', ')}{r.reviewed_by && ` · reviewed by ${r.reviewed_by} ${when(r.reviewed_at)}`}</div>
            </div>
            <div>
              <h4>Proposed change</h4>
              {r.proposed_change.patches.length ? r.proposed_change.patches.map((p, i) => <PatchView key={i} p={p} />) : <div className="muted">No automatic change: investigate manually.</div>}
            </div>
          </div>
          <details open={open === r.id} onToggle={(e) => e.target.open && setOpen(r.id)}>
            <summary>Supporting evidence ({r.evidence.length})</summary>
            <ul className="evidence">{r.evidence.map((e, i) => <li key={i}><code>{e.case_key}{e.span_seq ? ` #${e.span_seq}` : ''}</code> {e.text}</li>)}</ul>
          </details>
        </Card>
      ))}
      <div className="sticky-bar">
        <input placeholder="Experiment name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <Button onClick={create} disabled={!selected.length} data-testid="create-experiment">Create experiment from {selected.length} approved change(s)</Button>
      </div>
    </>
  )
}

const STAGES = ['pending_approval', 'approved', 'running', 'completed', 'decided']

function Experiments({ agentId, expId, setExpId, refreshAgents, go }) {
  const { data: list, reload: reloadList } = useApi(agentId ? `/experiments?agent_id=${agentId}` : null, [agentId])
  const { data: e, reload } = useApi(expId ? `/experiments/${expId}` : null, [expId])
  const [err, setErr] = useState(null)
  const [note, setNote] = useState('')
  useEffect(() => { if (!expId && list?.length) setExpId(list[0].id) }, [list])
  useInterval(() => reload(), e && e.status === 'running' ? 800 : null)
  const act = async (fn) => { setErr(null); try { await fn(); await reload(); reloadList(); refreshAgents() } catch (x) { setErr(x) } }
  if (!list) return <div className="muted">Loading…</div>
  if (!list.length) return <Card><Empty>No experiments yet. Approve recommendations, then create an experiment.</Empty></Card>
  const stage = e ? (e.decision ? 4 : STAGES.indexOf(e.status)) : 0
  const cmp = e?.comparison || {}
  return (
    <div className="split">
      <Card title="Experiments" className="list-pane">
        {list.map((x) => (
          <div key={x.id} className={`list-item ${x.id === expId ? 'active' : ''}`} onClick={() => setExpId(x.id)}>
            <div><b>#{x.id}</b> {x.name}</div>
            <div className="small">{x.kind.replace('_', ' ')} · <Badge>{x.decision || x.status}</Badge> {x.gate_verdict?.verdict && <Badge>{x.gate_verdict.verdict}</Badge>}</div>
          </div>
        ))}
      </Card>
      <div className="detail-pane">
        {e && (
          <>
            <Card title={`Experiment #${e.id}: ${e.name}`} subtitle={`${e.kind.replace('_', ' ')} · baseline v${e.baseline_config.version} vs candidate v${e.candidate_config.version} · created by ${e.created_by} ${when(e.created_at)}`}>
              <div className="stages">{['Created', 'Approved', 'Running', 'Compared', 'Decided'].map((s, i) => <div key={s} className={`stage ${i <= stage ? 'done' : ''} ${e.status === 'failed' && i === 2 ? 'fail' : ''}`}>{s}</div>)}</div>
              <ErrorBox error={err} />
              {e.hypothesis && <div className="hyp"><b>Hypothesis under test:</b> {e.hypothesis}</div>}
              <h4>Changes in the experimental configuration (baseline preserved as v{e.baseline_config.version})</h4>
              {e.patches.map((p, i) => <PatchView key={i} p={p} />)}
              <div className="row-actions">
                {e.status === 'pending_approval' && <Button onClick={() => act(() => api.post(`/experiments/${e.id}/approve`, { decision: 'approve', note: 'Approved for sandbox execution' }))} data-testid="approve-experiment">Approve execution (sandbox only)</Button>}
                {e.status === 'approved' && <Button onClick={() => act(() => api.post(`/experiments/${e.id}/run`))} data-testid="run-experiment">Run baseline vs candidate</Button>}
                {e.approved_by && <span className="small muted">Approved by {e.approved_by} {when(e.approved_at)}</span>}
              </div>
              {e.status === 'running' && e.live_runs.map((r) => <div key={r.id} className="small">Run #{r.id} ({r.purpose.replace('experiment_', '')}) <Progress done={r.progress?.done || 0} total={r.progress?.total || 0} /></div>)}
              {e.status === 'failed' && <ErrorBox error={JSON.stringify(cmp)} />}
            </Card>
            {e.status === 'completed' && (
              <>
                <Card title="Baseline vs candidate" subtitle={`Same dataset version, same judge, same criteria · baseline run #${e.baseline_run_id} · candidate run #${e.candidate_run_id}`}
                  actions={<Button kind="ghost" onClick={() => go('traces', { runId: e.candidate_run_id })}>Candidate traces →</Button>}>
                  <div className="stats">
                    <div className="stat good"><div className="stat-label">Fixed</div><div className="stat-value">{cmp.fixed.length}</div><div className="stat-hint">{cmp.fixed.join(', ')}</div></div>
                    <div className={`stat ${cmp.regressed.length ? 'bad' : ''}`}><div className="stat-label">Regressed</div><div className="stat-value">{cmp.regressed.length}</div><div className="stat-hint">{cmp.regressed.join(', ') || 'none'}</div></div>
                    <div className="stat"><div className="stat-label">Still failing</div><div className="stat-value">{cmp.still_failing.length}</div><div className="stat-hint">{cmp.still_failing.join(', ')}</div></div>
                    <div className="stat"><div className="stat-label">Targeted cases fixed</div><div className="stat-value">{cmp.targeted_fixed.length}/{cmp.targeted_cases.length}</div></div>
                  </div>
                  <table className="table" data-testid="comparison">
                    <thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Change</th></tr></thead>
                    <tbody>
                      {[['task_success_rate', 'Task success', 'pct'], ['tool_selection_accuracy', 'Tool selection accuracy', 'pct'], ['workflow_completion_rate', 'Workflow completion', 'pct'],
                        ['policy_compliance_rate', 'Policy compliance', 'pct'], ['answer_correctness', 'Answer correctness (judge)', 'pct'], ['grounding', 'Grounding (judge)', 'pct'],
                        ['quality_score', 'Quality score', 'pct'], ['critical_failures', 'Critical failures', 'n', true], ['latency_p95_ms', 'p95 latency', 'ms', true],
                        ['tokens_total', 'Tokens', 'n', true], ['cost_usd_total', 'Cost (USD)', 'usd', true]].map(([k, label, f, inv]) => {
                        const m = cmp.metrics[k] || {}
                        const fmt = f === 'pct' ? pct : f === 'ms' ? ms : f === 'usd' ? usd : num
                        return <tr key={k}><td>{label}</td><td>{fmt(m.baseline)}</td><td>{fmt(m.candidate)}</td><td>{delta(m.delta, f === 'pct' ? 1 : 0, f === 'pct', inv, f === 'ms' ? ms : f === 'usd' ? usd : null)}</td></tr>
                      })}
                    </tbody>
                  </table>
                  <div className="grid-2">
                    <div>
                      <h4>Failure categories (primary diagnosis)</h4>
                      <table className="mini"><thead><tr><th>Category</th><th>Baseline</th><th>Candidate</th></tr></thead>
                        <tbody>{cmp.failure_categories.map((c) => <tr key={c.category}><td>{c.category.replaceAll('_', ' ')}</td><td>{c.baseline}</td><td>{c.candidate}</td></tr>)}</tbody></table>
                    </div>
                    <div>
                      <h4>Models & fairness</h4>
                      <div className="small">Baseline agent <ModelTag m={cmp.models.baseline.agent} /> · Candidate agent <ModelTag m={cmp.models.candidate.agent} /> · Judge <ModelTag m={cmp.models.candidate.judge} /></div>
                      <ul className="small">{cmp.fairness.notes.map((n) => <li key={n}>{n}</li>)}</ul>
                    </div>
                  </div>
                  <h4>Per-case results</h4>
                  <table className="table compact">
                    <thead><tr><th>Case</th><th>Category</th><th>Change</th><th>Baseline</th><th>Candidate</th></tr></thead>
                    <tbody>{cmp.cases.filter((c) => c.change !== 'still_passing').concat(cmp.cases.filter((c) => c.change === 'still_passing')).map((c) => (
                      <tr key={c.case_key}><td><code>{c.case_key}</code></td><td>{c.category}</td><td><Badge>{c.change}</Badge></td>
                        <td>{c.baseline.passed ? 'pass' : `fail · ${c.baseline.failed_checks.join(', ')}`}</td>
                        <td>{c.candidate.passed ? 'pass' : `fail · ${c.candidate.failed_checks.join(', ')}`}</td></tr>))}</tbody>
                  </table>
                </Card>
                <Card title="Quality gates on the candidate" subtitle="Regression count is measured against the baseline run">
                  <div className="verdict-row"><Badge>{e.gate_verdict.verdict}</Badge><span>{e.gate_verdict.summary}</span></div>
                  <table className="mini"><tbody>{e.gate_verdict.gates.map((g) => <tr key={g.gate}><td>{g.label}</td><td>{g.operator} {g.threshold}</td><td>{typeof g.value === 'number' ? num(g.value, 3) : '—'}</td><td><Badge>{g.status}</Badge></td><td className="small">{g.reason}</td></tr>)}</tbody></table>
                  <p className="disclaimer">{e.gate_verdict.disclaimer}</p>
                  {!e.decision ? (
                    <div className="row-actions">
                      <input placeholder="Decision note" value={note} onChange={(x) => setNote(x.target.value)} />
                      <Button onClick={() => act(() => api.post(`/experiments/${e.id}/decision`, { decision: 'accept', note: note || 'Gates passed; accepting as approved configuration' }))} data-testid="accept-experiment">Accept candidate</Button>
                      <Button kind="danger" onClick={() => act(() => api.post(`/experiments/${e.id}/decision`, { decision: 'reject', note: note || 'Rejected' }))}>Reject</Button>
                    </div>
                  ) : <div className="ok-box">Decision: <b>{e.decision}</b> by {e.decision_by} {when(e.decided_at)} — {e.decision_note}. Recorded as the agent's approved configuration only; production deployment happens outside AgentHill.</div>}
                </Card>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function CompareModels({ agentId, agents, onExperiment }) {
  const agent = agents.find((a) => a.id === agentId)
  const { data: settings } = useApi('/settings/llm')
  const [baseId, setBaseId] = useState(null)
  const [provider, setProvider] = useState('ollama')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [prompt, setPrompt] = useState('')
  const [err, setErr] = useState(null)
  const [check, setCheck] = useState(null)
  useEffect(() => { setBaseId(agent?.current_config_id) }, [agentId])
  useEffect(() => {
    setModels([]); setCheck(null)
    if (provider === 'simlm') { setModels(['simlm-1']); setModel('simlm-1'); return }
    api.get(`/settings/llm/providers/${provider}/models`).then((d) => { setModels(d.models); setModel((m) => m || d.models[0] || '') }).catch((e) => setErr(e))
  }, [provider])
  if (!agent) return <Empty>Select an agent.</Empty>
  const base = agent.configs.find((c) => c.id === baseId) || agent.configs[0]
  const validate = async () => { setCheck({ running: true }); setCheck(await api.get(`/settings/llm/compatibility?provider=${provider}&model=${encodeURIComponent(model)}&role=agent`)) }
  const create = async () => {
    setErr(null)
    const patches = [{ type: 'setting', target: 'model_provider', before: base.settings.model_provider, after: provider, summary: `model_provider ${base.settings.model_provider} -> ${provider}` },
      { type: 'setting', target: 'model', before: base.settings.model, after: model, summary: `model ${base.settings.model} -> ${model}` }]
    if (prompt.trim()) patches.push({ type: 'prompt_replace', target: 'system_prompt', before: base.system_prompt, after: prompt, summary: 'system prompt replaced' })
    try {
      const e = await api.post('/experiments', { agent_id: agentId, name: `Model comparison: ${base.settings.model_provider}:${base.settings.model} vs ${provider}:${model}`,
        kind: 'model_comparison', patches, baseline_config_id: base.id, hypothesis: `Does ${provider}:${model} match or beat ${base.settings.model_provider}:${base.settings.model} on the same dataset, criteria and judge?` })
      onExperiment(e.id)
    } catch (x) { setErr(x) }
  }
  return (
    <Card title="Compare models on the same evaluation dataset" subtitle="Creates a model-comparison experiment: dataset version, criteria, judge and all other settings are held constant; only the selected fields change.">
      <div className="grid-2">
        <div>
          <label className="field">Baseline configuration
            <select value={base.id} onChange={(e) => setBaseId(Number(e.target.value))}>{agent.configs.map((c) => <option key={c.id} value={c.id}>v{c.version} · {c.settings.model_provider}:{c.settings.model} · {c.label}</option>)}</select></label>
          <label className="field">Candidate provider
            <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel('') }} data-testid="cmp-provider">
              <option value="simlm">SimLM (deterministic reference)</option>
              {(settings?.providers || []).map((p) => <option key={p.name} value={p.name} disabled={!p.credentials.configured}>{p.label}{p.credentials.configured ? '' : ' (no key configured)'}</option>)}
            </select></label>
          <label className="field">Candidate model
            <input list="cmp-models" value={model} onChange={(e) => setModel(e.target.value)} data-testid="cmp-model" />
            <datalist id="cmp-models">{models.map((m) => <option key={m} value={m} />)}</datalist></label>
          <label className="field">Candidate system prompt (optional — leave blank to keep the baseline prompt)
            <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={base.system_prompt} /></label>
          <div className="row-actions">
            <Button kind="secondary" onClick={validate} disabled={!model}>Check model compatibility</Button>
            <Button onClick={create} disabled={!model} data-testid="create-model-comparison">Create comparison experiment</Button>
          </div>
          {check && !check.running && (check.ok ? <div className="ok-box">{check.detail}{check.context_window ? ` · context window ${num(check.context_window)} tokens` : ''}</div>
            : <ErrorBox error={`${check.error.code}: ${check.error.message} — ${check.error.hint}`} />)}
          <ErrorBox error={err} />
        </div>
        <div>
          <h4>What is held constant</h4>
          <ul className="small">
            <li>Dataset version and every test case</li><li>Deterministic checks and judge criteria</li><li>Judge model (Settings → model roles)</li>
            <li>Retrieval, tool and step settings (unless you change the prompt)</li>
          </ul>
          <h4>Reported per model</h4>
          <ul className="small"><li>Task success, tool selection accuracy, judge scores</li><li>Fixed / regressed cases and failure categories</li>
            <li>Latency (measured for real models; modeled for SimLM)</li><li>Token usage; cost only where a price is configured — local Ollama inference cost is reported as not measured</li></ul>
          <div className="small muted">Current baseline: <ModelTag m={{ provider: base.settings.model_provider, model: base.settings.model }} /></div>
        </div>
      </div>
    </Card>
  )
}
