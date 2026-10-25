import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card, Empty, ErrorBox, Json, ModelTag, Tabs, ms, num, pct, useApi } from '../components'

export default function Traces({ go, params, agentId }) {
  const { data: runs } = useApi(agentId ? `/runs?agent_id=${agentId}` : null, [agentId])
  const [runId, setRunId] = useState(params.runId || null)
  const [resultId, setResultId] = useState(params.resultId || null)
  const [tab, setTab] = useState(params.tab || 'results')
  useEffect(() => { if (!runId && runs?.length) setRunId(runs.find((r) => r.status === 'completed')?.id) }, [runs])
  const { data: run, reload: reloadRun } = useApi(runId ? `/runs/${runId}` : null, [runId])
  const { data: analysis, setData: setAnalysis, error: aErr } = useApi(runId ? `/runs/${runId}/analysis` : null, [runId])
  const [err, setErr] = useState(null)

  const analyze = async () => {
    setErr(null)
    try { setAnalysis(await api.post(`/runs/${runId}/analyze`)); setTab('analysis'); reloadRun() } catch (e) { setErr(e) }
  }
  if (!runs) return <div className="muted">Loading…</div>
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Execution Traces & Failure Analysis</h1>
          <p className="lede">Inspect what the agent actually did, then let the Failure Analysis Agent classify and cluster failures with evidence taken from the traces.</p></div>
        <div className="head-actions">
          <select value={runId || ''} onChange={(e) => { setRunId(Number(e.target.value)); setResultId(null) }} data-testid="trace-run-select">
            {runs.filter((r) => r.status === 'completed').map((r) => <option key={r.id} value={r.id}>Run #{r.id} · v{r.config_version} · {r.purpose.replaceAll('_', ' ')} · {pct(r.metrics.task_success_rate)}</option>)}
          </select>
          <Button onClick={analyze} disabled={!runId} data-testid="analyze">{analysis ? 'Re-run failure analysis' : 'Run failure analysis'}</Button>
        </div>
      </div>
      <ErrorBox error={err} />
      {run && (
        <div className="run-banner">
          <span>Run #{run.id}</span><span>{run.config_label}</span><span>{run.dataset_name} v{run.dataset_version}</span>
          <span>Agent <ModelTag m={run.models?.agent} /></span><span>Judge <ModelTag m={run.models?.judge} /></span>
          <span><b>{pct(run.metrics.task_success_rate)}</b> task success</span><span>{run.metrics.critical_failures} critical</span>
          {run.models?.bias_note && <Badge tone="amber" title={run.models.bias_note}>same model is agent and judge</Badge>}
        </div>
      )}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'results', label: 'Case results & traces' }, { id: 'analysis', label: `Failure analysis${analysis ? ` (${analysis.clusters.length} clusters)` : ''}` }]} />
      {tab === 'results' && run && (
        <div className="split">
          <Card title="Case results" className="list-pane">
            <table className="table compact">
              <thead><tr><th>Case</th><th>Result</th><th>Outcome</th><th>Failed checks</th></tr></thead>
              <tbody>
                {run.results.map((r) => (
                  <tr key={r.id} className={`clickable ${resultId === r.id ? 'selected-row' : ''}`} onClick={() => setResultId(r.id)} data-case={r.case_key}>
                    <td><code>{r.case_key}</code><div className="small muted">{r.category}</div></td>
                    <td><Badge>{r.status}</Badge>{r.critical && <Badge tone="red">critical</Badge>}{r.needs_review && <Badge tone="amber" title="Judge verdict uncertain or disputed">review</Badge>}</td>
                    <td><Badge>{r.outcome}</Badge></td>
                    <td className="small">{r.failed_checks.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <div className="detail-pane">{resultId ? <ResultDetail id={resultId} go={go} analysis={analysis} /> : <Card><Empty>Select a case to inspect its execution trace.</Empty></Card>}</div>
        </div>
      )}
      {tab === 'analysis' && (
        analysis ? <Analysis analysis={analysis} onOpen={(rid) => { setResultId(rid); setTab('results') }} go={go} setAnalysis={setAnalysis} />
          : <Card><Empty>{aErr?.status === 404 ? 'No failure analysis for this run yet. Click "Run failure analysis".' : 'Loading…'}</Empty></Card>
      )}
    </div>
  )
}

const KIND_LABEL = { input: 'Input', instructions: 'Instructions interpreted', retrieval: 'Retrieval', decision: 'Decision', llm_call: 'Model call',
  tool_call: 'Tool call', guardrail: 'Guardrail', state_change: 'State change', response: 'Response' }

function Span({ s, missed = null }) {
  const [open, setOpen] = useState(['tool_call', 'guardrail', 'retrieval'].includes(s.kind) || (s.kind === 'decision' && s.name === 'instruction_in_untrusted_content'))
  const d = s.data
  let summary = s.name
  if (s.kind === 'tool_call') summary = `${d.tool}(${Object.entries(d.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')})`
  if (s.kind === 'decision' && s.name === 'tool_selection') summary = `step "${d.step || d.reason || ''}" → ${d.selected || 'no tool (respond)'}`
  if (s.kind === 'decision' && s.name === 'plan') summary = `plan: ${d.plan}${d.article ? ' · ' + d.article : ''}`
  if (s.kind === 'retrieval') summary = `${(d.accepted || []).map((a) => `${a.id} (${a.score})`).join(', ') || 'no article accepted'}`
  if (s.kind === 'response') summary = `outcome: ${d.outcome}`
  if (s.kind === 'guardrail') summary = `blocked ${d.tool}: ${d.reason}`
  if (s.kind === 'decision' && s.name === 'instruction_in_untrusted_content') summary = `instruction in ${d.source} asks for ${d.requested_tool} → ${d.obeyed ? 'OBEYED' : 'ignored'}`
  return (
    <div className={`span ${s.kind} ${s.status}`}>
      <div className="span-head" onClick={() => setOpen(!open)}>
        <span className="seq">#{s.seq}</span><span className="kind">{KIND_LABEL[s.kind] || s.kind}</span>
        <span className="span-sum">{summary}</span>
        {s.status !== 'ok' && <Badge>{s.status}</Badge>}
        <span className="muted small">{s.modeled_duration_ms ? ms(s.modeled_duration_ms) : ''}</span>
      </div>
      {open && (
        <div className="span-body">
          {s.kind === 'decision' && s.name === 'tool_selection' && d.candidates ? (
            <table className="mini"><thead><tr><th>Candidate tool</th><th>Score</th><th>Matched terms</th></tr></thead>
              <tbody>{d.candidates.filter((c, i) => i < 5 || c.tool === missed).map((c) => (
                <tr key={c.tool} className={c.tool === d.selected ? 'hl' : c.tool === missed ? 'miss' : ''}>
                  <td>{c.tool}{c.tool === d.selected && <span className="tag">chosen</span>}{c.tool === missed && <span className="tag miss">should have been chosen</span>}</td>
                  <td>{c.score}</td><td className="small">{c.matched_terms.join(', ') || '— no words in common'}</td></tr>))}</tbody></table>
          ) : <Json value={d} max={1500} />}
        </div>
      )}
    </div>
  )
}

function ResultDetail({ id, go, analysis }) {
  const { data: r, reload } = useApi(`/results/${id}`, [id])
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  if (!r) return <Card>Loading…</Card>
  const diag = r.diagnosis || analysis?.case_diagnoses.find((d) => d.result_id === r.id)
  // The analyzer names the trace step where the expected tool lost; mark it there.
  const missedAt = Object.fromEntries((diag?.diagnoses || []).filter((x) => x.category === 'incorrect_tool_selection' && x.details?.expected_tool)
    .flatMap((x) => x.evidence.filter((e) => e.span_seq).map((e) => [e.span_seq, x.details.expected_tool])))
  const review = async (verdict) => { await api.post('/reviews', { target_type: 'case_result', target_id: String(r.id), verdict, labels: { judge_passed: r.judge.passed } }); setMsg(`Recorded: you ${verdict} with the judge`); reload() }
  const toRegression = async () => { setErr(null); try { const x = await api.post(`/results/${r.id}/to-regression`, { note: 'Confirmed failure' }); setMsg(`Added ${x.case_key} to the regression suite (dataset v${x.dataset_version}).`) } catch (e) { setErr(e) } }
  return (
    <>
      <Card title={`${r.case_key}: ${r.passed ? 'passed' : r.status}`} subtitle={r.message}
        actions={!r.passed && <Button kind="secondary" onClick={toRegression} data-testid="to-regression">Confirm failure → regression test</Button>}>
        {msg && <div className="ok-box">{msg}</div>}<ErrorBox error={err} />
        <div className="grid-2">
          <div>
            <h4>Agent response <Badge>{r.outcome}</Badge></h4>
            <div className="response">{r.response || <span className="muted">(no response)</span>}</div>
            <div className="small muted">Latency {ms(r.latency_ms)} · {num(r.tokens)} tokens · cost {r.cost_usd === null ? 'not measured' : `$${r.cost_usd}`}</div>
          </div>
          <div>
            <h4>Deterministic checks</h4>
            <table className="mini"><tbody>{r.checks.map((c) => (
              <tr key={c.name}><td>{c.name.replaceAll('_', ' ')}</td><td><Badge>{c.status}</Badge></td><td className="small">{c.detail}</td></tr>))}</tbody></table>
          </div>
        </div>
        <div className="grid-2">
          <div>
            <h4>Judge <ModelTag m={r.judge.judge} /> {r.judge.overall !== null && r.judge.overall !== undefined && <Badge tone={r.judge.passed ? 'green' : 'red'}>{pct(r.judge.overall)}</Badge>}</h4>
            {r.judge.error ? <ErrorBox error={`${r.judge.error.code}: ${r.judge.error.message}`} /> :
              <table className="mini"><tbody>{Object.entries(r.judge.criteria || {}).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.score}</td><td className="small">{v.rationale}</td></tr>)}</tbody></table>}
            <div className="review-row">Human review: <Button kind="ghost" onClick={() => review('agree')}>Agree with judge</Button><Button kind="ghost" onClick={() => review('disagree')}>Disagree</Button>
              {r.reviews.map((x, i) => <Badge key={i}>{`${x.reviewer}: ${x.verdict}`}</Badge>)}</div>
          </div>
          <div>
            <h4>Diagnosis</h4>
            {!diag ? <div className="muted small">{r.passed ? 'Case passed.' : 'Run failure analysis to diagnose this case.'}</div> : diag.diagnoses.map((d, i) => (
              <div key={i} className="diag">
                <div><Badge tone="red">{d.category.replaceAll('_', ' ')}</Badge> <Badge>{d.confidence}</Badge></div>
                <div className="hyp"><b>Hypothesis:</b> {d.hypothesis}</div>
                <ul className="evidence">{d.evidence.map((e, j) => <li key={j}>{e.span_seq ? <code>#{e.span_seq}</code> : null} {e.text}</li>)}</ul>
              </div>))}
          </div>
        </div>
      </Card>
      <Card title="Execution trace" subtitle={`${r.trace.spans?.length || 0} spans · OpenTelemetry-compatible export available`}
        actions={<a className="btn ghost" href={`/api/results/${r.id}/otlp`} target="_blank" rel="noreferrer" onClick={(e) => { e.preventDefault(); api.get(`/results/${r.id}/otlp`).then((d) => { const w = window.open(); w.document.write(`<pre>${JSON.stringify(d, null, 2)}</pre>`) }) }}>OTLP JSON</a>}>
        <div className="trace" data-testid="trace">{(r.trace.spans || []).map((s) => <Span key={s.seq} s={s} missed={missedAt[s.seq] || null} />)}</div>
        {r.trace.error && <ErrorBox error={`${r.trace.error.code}: ${r.trace.error.message}`} />}
      </Card>
    </>
  )
}

function Analysis({ analysis, onOpen, go, setAnalysis }) {
  const acc = analysis.summary.labeled_accuracy
  const reviewCluster = async (key, verdict) => {
    await api.post('/reviews', { target_type: 'cluster', target_id: `${analysis.id}:${key}`, verdict })
    setAnalysis(await api.get(`/runs/${analysis.run_id}/analysis`))
  }
  return (
    <>
      <div className="stats">
        <div className="stat"><div className="stat-label">Failed cases</div><div className="stat-value">{analysis.summary.failed_cases}</div></div>
        <div className="stat"><div className="stat-label">Failure clusters</div><div className="stat-value">{analysis.clusters.length}</div></div>
        <div className="stat"><div className="stat-label">Recommendations</div><div className="stat-value">{analysis.recommendations.length}</div></div>
        <div className="stat"><div className="stat-label">Classification vs. human labels</div><div className="stat-value">{acc ? pct(acc.primary_accuracy) : '—'}</div>
          <div className="stat-hint">{acc ? `${acc.labeled_cases} labelled cases` : 'no labelled sample for this config'}</div></div>
      </div>
      <div className="muted small">Evidence layer: {analysis.analyzer.evidence_layer}{analysis.analyzer.narrative_model ? <> · Narrative review: <ModelTag m={analysis.analyzer.narrative_model} /></> : ' · LLM narrative review: off'}</div>
      {analysis.analyzer.error && <ErrorBox error={`Narrative model failed (${analysis.analyzer.error.code}): ${analysis.analyzer.error.message}. Showing evidence-layer results only; no other provider was substituted.`} />}
      {analysis.clusters.map((c) => (
        <Card key={c.key} title={c.category_label} subtitle={`${c.frequency} case(s): ${c.cases.join(', ')} · signature ${c.key}`}
          actions={<><Badge>{c.severity}</Badge><Badge title="confidence">{`confidence ${c.confidence}`}</Badge>
            {c.review ? <Badge>{`${c.review.reviewer}: ${c.review.verdict}`}</Badge> : <>
              <Button kind="ghost" onClick={() => reviewCluster(c.key, 'confirm')}>Confirm diagnosis</Button>
              <Button kind="ghost" onClick={() => reviewCluster(c.key, 'reject')}>Reject</Button></>}</>}>
          <div className="grid-2">
            <div>
              <h4>Evidence (from traces)</h4>
              <ul className="evidence">{c.evidence.slice(0, 8).map((e) => (
                <li key={e.id}><code>{e.id}</code> <a onClick={() => onOpen(e.result_id)}>{e.case_key}{e.span_seq ? ` #${e.span_seq}` : ''}</a> {e.text}</li>))}</ul>
              {c.evidence.length > 8 && <div className="small muted">+{c.evidence.length - 8} more evidence items</div>}
            </div>
            <div>
              <h4>Root-cause hypothesis</h4>
              <div className="hyp">{c.hypothesis}</div>
              {c.investigate && <div className="warn-box">Further investigation: {c.investigate}</div>}
              {c.llm_review && (
                <div className="llm-review"><b>LLM review ({c.llm_review.model?.provider}:{c.llm_review.model?.model}) — {c.llm_review.status}</b>
                  {c.llm_review.hypothesis && <div>{c.llm_review.hypothesis} <span className="muted">cites {c.llm_review.supporting_evidence_ids?.join(', ')}</span></div>}
                  {c.llm_review.reason && <div className="muted">{c.llm_review.reason}</div>}</div>)}
            </div>
          </div>
        </Card>
      ))}
      {analysis.clusters.length > 0 && <Button onClick={() => go('improve', { analysisRunId: analysis.run_id })} data-testid="to-recommendations">View prioritized recommendations →</Button>}
    </>
  )
}
