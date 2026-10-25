import React, { useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorBox, ModelTag, Progress, Tabs, ms, pct, useApi, useInterval, when } from '../components'

export default function Regression({ go, agentId }) {
  const [tab, setTab] = useState('regression')
  return (
    <div className="page">
      <div className="page-head"><div><h1>Regression Testing & Learning</h1>
        <p className="lede">Confirmed failures become permanent regression tests, every experiment decision is recorded with its evidence, and the evaluation itself is measured against human judgement.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'regression', label: 'Regression suite & gates' }, { id: 'history', label: 'Experiment history' },
        { id: 'quality', label: 'Evaluation quality' }, { id: 'audit', label: 'Audit log' }]} />
      {tab === 'regression' && <Suite agentId={agentId} go={go} />}
      {tab === 'history' && <History agentId={agentId} go={go} />}
      {tab === 'quality' && <Quality />}
      {tab === 'audit' && <Audit />}
    </div>
  )
}

function Suite({ agentId, go }) {
  const { data, reload } = useApi(agentId ? `/agents/${agentId}/regression` : null, [agentId])
  const [run, setRun] = useState(null)
  const [err, setErr] = useState(null)
  useInterval(async () => {
    const r = await api.get(`/runs/${run.id}`)
    setRun(r)
    if (r.status === 'completed' || r.status === 'failed') reload()
  }, run && !['completed', 'failed'].includes(run.status) ? 700 : null)
  if (!data) return <div className="muted">Loading…</div>
  const start = async () => { setErr(null); try { setRun(await api.post(`/agents/${agentId}/regression/run`)) } catch (e) { setErr(e) } }
  const confirmed = data.experiments.filter((e) => e.decision === 'accept').flatMap((e) => e.fixed.map((k) => ({ k, e })))
  return (
    <>
      <div className="grid-2">
        <Card title="Regression suite" subtitle={`${data.cases.length} test(s) created from confirmed failures`}
          actions={<Button onClick={start} disabled={!data.cases.length} data-testid="run-regression">Run regression suite on current config</Button>}>
          <ErrorBox error={err} />
          {run && <div className="run-status">Run #{run.id} <Badge>{run.status}</Badge> <Progress done={run.progress?.done || 0} total={run.progress?.total || 0} />
            {run.status === 'completed' && <span> {pct(run.metrics.task_success_rate)} pass · <a onClick={() => go('traces', { runId: run.id })}>traces</a></span>}</div>}
          {!data.cases.length ? <Empty>No regression tests yet. Open a failed case in Traces and choose “Confirm failure → regression test”.</Empty> : (
            <table className="table" data-testid="regression-cases">
              <thead><tr><th>Test</th><th>Origin</th><th>Diagnosis</th><th>Fixed by</th><th>Confirmed by</th></tr></thead>
              <tbody>{data.cases.map((c) => (
                <tr key={c.id}><td><code>{c.case_key}</code><div className="small msg">{c.input.message}</div></td>
                  <td className="small">run #{c.origin.run_id} · config v{c.origin.config_version}<br />failed: {(c.origin.failed_checks || []).join(', ')}</td>
                  <td>{c.origin.diagnosis ? <Badge tone="red">{c.origin.diagnosis.replaceAll('_', ' ')}</Badge> : '—'}</td>
                  <td>{c.origin.fixed_by_experiment ? `experiment #${c.origin.fixed_by_experiment}` : '—'}</td><td>{c.origin.confirmed_by}</td></tr>))}</tbody>
            </table>)}
          {data.runs.length > 0 && (<><h4>Regression runs</h4>
            <table className="mini"><tbody>{data.runs.map((r) => <tr key={r.id}><td>#{r.id}</td><td>config v{r.config_version}</td><td>{r.status === 'completed' ? `${r.metrics.passed}/${r.metrics.total_cases} still fixed` : r.status}</td><td>{when(r.finished_at)}</td></tr>)}</tbody></table></>)}
        </Card>
        <Card title="Confirmed fixes" subtitle="Cases fixed by accepted experiments">
          {!confirmed.length ? <Empty>No accepted experiments yet.</Empty> : (
            <table className="mini"><tbody>{confirmed.map(({ k, e }) => <tr key={`${e.id}-${k}`}><td><code>{k}</code></td><td>{e.name}</td><td><Badge>{e.verdict}</Badge></td></tr>)}</tbody></table>)}
        </Card>
      </div>
      <Card title="Regression results and quality-gate decisions per experiment">
        {!data.experiments.length ? <Empty>No completed experiments.</Empty> : (
          <table className="table">
            <thead><tr><th>Experiment</th><th>Kind</th><th>Fixed</th><th>Regressions</th><th>Gate verdict</th><th>Failed gates</th><th>Decision</th></tr></thead>
            <tbody>{data.experiments.map((e) => (
              <tr key={e.id} className="clickable" onClick={() => go('improve', { experimentId: e.id })}>
                <td>#{e.id} {e.name}</td><td>{e.kind.replace('_', ' ')}</td><td className="pos">{e.fixed.length}</td>
                <td className={e.regressed.length ? 'neg' : ''}>{e.regressed.join(', ') || '0'}</td><td><Badge>{e.verdict}</Badge></td>
                <td className="small">{(e.gate_verdict?.gates || []).filter((g) => g.status === 'fail').map((g) => g.label).join(', ') || '—'}</td>
                <td>{e.decision ? <Badge>{e.decision}</Badge> : 'pending'}</td></tr>))}</tbody>
          </table>)}
        <p className="disclaimer">Passing quality gates means the configuration met this team's thresholds on this dataset. It is not proof of production readiness.</p>
      </Card>
    </>
  )
}

function History({ agentId }) {
  const [q, setQ] = useState('')
  const { data } = useApi(`/history?q=${encodeURIComponent(q)}${agentId ? `&agent_id=${agentId}` : ''}`, [q, agentId])
  const [open, setOpen] = useState(null)
  return (
    <Card title="Experiment history" subtitle="Searchable, auditable record of every experiment" actions={<input placeholder="Search name, hypothesis, decision note…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="history-search" />}>
      {!data ? 'Loading…' : !data.length ? <Empty>No matching experiments.</Empty> : data.map((h) => (
        <div key={h.id} className="history-item">
          <div className="history-head clickable" onClick={() => setOpen(open === h.id ? null : h.id)}>
            <b>#{h.id} {h.name}</b> <span className="muted">{h.agent_name} · config v{h.baseline_version} → v{h.candidate_version} · dataset v{h.dataset_version}</span>
            <span>{pct(h.baseline_task_success)} → {pct(h.candidate_task_success)}</span>
            <Badge>{h.gate_verdict?.verdict || h.status}</Badge>{h.decision && <Badge>{h.decision}</Badge>}
          </div>
          {open === h.id && (
            <div className="history-body grid-2">
              <div>
                <div className="kv"><span>Original failures</span><div>{h.original_failures.join(', ') || '— (model comparison)'}</div></div>
                <div className="kv"><span>Root-cause hypotheses</span><ul>{h.root_cause_hypotheses.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
                <div className="kv"><span>Recommended changes</span><ul>{h.recommended_changes.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
                <div className="kv"><span>Applied changes</span><ul>{h.applied_changes.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
              </div>
              <div>
                <div className="kv"><span>Results</span><div>Fixed: {h.fixed.join(', ') || 'none'}<br />Regressions: {h.regressions.join(', ') || 'none'}</div></div>
                <div className="kv"><span>Gate summary</span><div>{h.gate_verdict?.summary}</div></div>
                <div className="kv"><span>Approvals</span><div className="small">
                  {h.approvals.recommendations.map((r) => <div key={r.id}>Recommendation #{r.id}: {r.by} {when(r.at)}</div>)}
                  <div>Execution: {h.approvals.execution.by || '—'} {when(h.approvals.execution.at)}</div>
                  <div>Decision: {h.approvals.decision.by || '—'} {when(h.approvals.decision.at)} {h.approvals.decision.note && `— "${h.approvals.decision.note}"`}</div></div></div>
                <div className="kv"><span>Time to verdict</span><div>{ms(h.time_to_verdict_ms)}</div></div>
              </div>
            </div>)}
        </div>))}
    </Card>
  )
}

function Quality() {
  const { data, reload } = useApi('/meta/evaluation-quality')
  const { data: platform } = useApi('/meta/platform')
  const [err, setErr] = useState(null)
  if (!data) return <div className="muted">Loading…</div>
  const cal = data.judge_calibration
  const calibrate = async () => { setErr(null); try { await api.post('/meta/judge-calibration'); reload() } catch (e) { setErr(e) } }
  return (
    <>
      <div className="grid-2">
        <Card title="Judge calibration (human-labelled set)" subtitle="16 human-reviewed answers; the configured judge must agree with the human pass/fail label"
          actions={<Button kind="secondary" onClick={calibrate}>Run calibration with current judge</Button>}>
          <ErrorBox error={err} />
          {!cal ? <Empty>Not run yet.</Empty> : (
            <>
              <div className="stats">
                <div className="stat"><div className="stat-label">Agreement</div><div className="stat-value">{pct(cal.agreement_rate)}</div></div>
                <div className="stat"><div className="stat-label">Cohen's κ</div><div className="stat-value">{cal.cohens_kappa ?? '—'}</div></div>
                <div className="stat"><div className="stat-label">False pass / fail</div><div className="stat-value">{cal.false_pass} / {cal.false_fail}</div></div>
              </div>
              <div className="small muted">Judge <ModelTag m={cal.judge} /> · {cal.scored}/{cal.examples} scored · {cal.errors} errors · {when(cal.computed_at)}</div>
            </>)}
        </Card>
        <Card title="Judge vs. human review on live results">
          <div className="stats">
            <div className="stat"><div className="stat-label">Human reviews</div><div className="stat-value">{data.judge_human_agreement.reviews}</div></div>
            <div className="stat"><div className="stat-label">Agreement</div><div className="stat-value">{pct(data.judge_human_agreement.agreement_rate)}</div></div>
            <div className="stat"><div className="stat-label">Awaiting review</div><div className="stat-value">{data.judge_human_agreement.pending_review}</div><div className="stat-hint">uncertain or disputed verdicts</div></div>
          </div>
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Failure classification accuracy" subtitle="Analyzer's primary category vs. human labels on the flawed v1 baselines">
          {!data.failure_classification.length ? <Empty>No labelled analyses yet.</Empty> : (
            <table className="mini"><thead><tr><th>Analysis</th><th>Run</th><th>Labelled cases</th><th>Primary accuracy</th></tr></thead>
              <tbody>{data.failure_classification.map((x) => <tr key={x.analysis_id}><td>#{x.analysis_id}</td><td>#{x.run_id}</td><td>{x.labeled_cases}</td><td>{pct(x.primary_accuracy)}</td></tr>)}</tbody></table>)}
          <div className="kv"><span>Diagnosis validation (human confirm/reject)</span><div>{data.diagnosis_validation.reviews} reviews · {pct(data.diagnosis_validation.confirmed_rate)} confirmed</div></div>
        </Card>
        <Card title="Platform effectiveness" subtitle="Measured from timestamps in the audit trail">
          {!platform ? 'Loading…' : (
            <>
              <h4>Time to onboard an agent (registration → first completed evaluation)</h4>
              <table className="mini"><tbody>{platform.onboarding.map((o) => <tr key={o.agent}><td>{o.agent}</td><td>{o.minutes === null ? 'no run yet' : `${o.minutes} min`}</td></tr>)}</tbody></table>
              <h4>Failure identified → validated improvement</h4>
              <table className="mini"><tbody>{platform.failure_to_validated_fix.length ? platform.failure_to_validated_fix.map((o) => <tr key={o.experiment}><td>{o.experiment}</td><td>{o.minutes} min</td></tr>) : <tr><td className="muted">No accepted experiment yet</td></tr>}</tbody></table>
              <h4>Time to actionable verdict (experiment created → gates evaluated)</h4>
              <table className="mini"><tbody>{platform.time_to_verdict.map((o) => <tr key={o.experiment}><td>{o.experiment}</td><td>{ms(o.ms)}</td></tr>)}</tbody></table>
              <div className="warn-box small">Not measurable in this prototype: {platform.not_measurable.join('; ')}</div>
            </>)}
        </Card>
      </div>
    </>
  )
}

function Audit() {
  const { data } = useApi('/audit?limit=150')
  if (!data) return <div className="muted">Loading…</div>
  return (
    <Card title="Audit log" subtitle="Every registration, approval, run, decision and settings change (credentials are never logged)">
      <table className="table compact"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
        <tbody>{data.map((a) => <tr key={a.id}><td className="small">{when(a.created_at)}</td><td>{a.actor}</td><td><code>{a.action}</code></td><td>{a.entity_type} {a.entity_id}</td>
          <td className="small msg">{JSON.stringify(a.details).slice(0, 160)}</td></tr>)}</tbody></table>
    </Card>
  )
}
