# AgentHill

**AI Agent Evaluation and Continuous Improvement Platform.** Evaluate any enterprise AI agent, diagnose *why* it fails from its execution traces, test fixes against a preserved baseline, and lock each fix in as a regression test.

> Enterprise teams can usually see *that* an agent failed. What they struggle with is *why* it failed, and whether a proposed fix actually helps without breaking something else. AgentHill turns that into one repeatable, measurable, self-service loop.

[![AgentHill demo — click to play](docs/demo/agenthill-demo-preview.gif)](docs/demo/agenthill-demo.mp4)

**▶ [Watch the narrated product demo (MP4, 1080p, with sound)](docs/demo/agenthill-demo.mp4)**. It is written for a non-technical audience. Plain-English slides explain the problem, how AgentHill works (exam, marking, black-box recording, repair, retest, pass marks, learning), the pass marks, the five problem areas it found and one worked example. It then shows each step live in the app, with a visible pointer and zoom-ins. Every number spoken is read from the page while it records. The British voiceover is AI-generated (Piper, run locally).

📄 **Start here: [PRODUCT_BRIEF.md](PRODUCT_BRIEF.md)** covers the problem, the users, the product, the decisions and the scope.
Also: [ARCHITECTURE.md](ARCHITECTURE.md) · [EVALUATION_DESIGN.md](EVALUATION_DESIGN.md) · [PRODUCT_DECISIONS.md](PRODUCT_DECISIONS.md) · [LIMITATIONS.md](LIMITATIONS.md)

---

## The improvement loop in plain language

1. **Register an agent.** Describe its tools, rules and what "good" looks like. Pick a quality bar.
2. **Give it an exam.** A *golden dataset* holds realistic requests along with the right behaviour for each: which tools to use, in what order, what must never happen, and what the answer must say.
3. **Run the exam.** Every request runs in a safe sandbox copy of the company's systems. Each answer is graded twice: by hard checks (right tool? right order? anything forbidden?) and by a judge (is the answer correct and backed by evidence?).
4. **Find out why it failed.** The Failure Analysis Agent reads the step-by-step *trace* of each failure. It groups failures that share a cause and says what it saw (evidence) separately from what it thinks (hypothesis).
5. **Get a concrete fix.** Each cause becomes a proposed change, such as a prompt sentence, a clearer tool description or a setting. It is shown as a diff and ranked by impact.
6. **Test the fix safely.** A person approves it. AgentHill makes a new version, keeps the old one untouched, and runs **both** on the exact same exam.
7. **Decide with evidence.** You see which cases got fixed, which broke (regressions), and whether the team's quality gates pass. A person accepts or rejects. Nothing is deployed automatically.
8. **Never forget.** A confirmed failure becomes a permanent regression test, and every decision lands in a searchable history.

## Key capabilities

| | Capability | What it does |
|---|---|---|
| 1 | Agent onboarding | Agents, tools and APIs, criteria, gates; immutable configuration versions; templates |
| 2 | Golden datasets | Normal, edge, failure and adversarial cases; validation; LLM or template generation with **human review**; immutable versions |
| 3 | Evaluation engine | LangGraph runtime in per-case sandboxes; **7 deterministic checks** plus an LLM or rubric **judge**; OTel-style traces; judge calibration |
| 4 | Failure analysis | Classify → cluster → evidence-referenced root-cause hypothesis with confidence; optional LLM review that must cite evidence |
| 5 | Recommendations | Prompt, tool-description, retrieval, threshold and validation patches; priority = frequency × severity × impact × confidence |
| 6 | Experiments | Versioned candidate vs. preserved baseline on the same dataset; approvals; per-case fixed/regressed; model comparisons |
| 7 | Regression & gates | Six configurable gates; explainable verdict; "met thresholds" ≠ "production ready" |
| 8 | Learning & history | Failure → regression test with origin; searchable experiment history; audit log |
| + | Multi-LLM | **Ollama, Anthropic Claude, OpenAI, Google Gemini** through one provider layer; separate models for agent, judge and analyzer; encrypted keys |

## Architecture

```mermaid
flowchart LR
  UI[React UI<br/>Dashboard · Agents & Datasets · Traces · Experiments · Regression · Settings] -->|REST, tenant-scoped| API[FastAPI]
  API --> REG[Agent Registry] & DS[Datasets] & EXE[Execution Service<br/>LangGraph + Tool Gateway] & EVAL[Evaluation Engine<br/>checks + judges]
  API --> FA[Failure Analysis Agent] & REC[Recommendations] & EXP[Experiments] & GATE[Regression & Gates] & HIST[History & Audit]
  EXE --> SB[(Sandboxed simulated<br/>IT & HR APIs, per tenant)]
  EXE & EVAL & FA --> LLM[Model layer<br/>SimLM · Ollama · Claude · OpenAI · Gemini]
  REG & DS & EVAL & EXP & HIST --> DB[(SQLite)]
```

A modular monolith: ten components with explicit contracts, deployed as one FastAPI process that also serves the built React app. Details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Results from real runs

All numbers below come from executions you can reproduce with `python scripts/run_full_loop.py`. The exported evidence (per-case results, analyses, recommendations, comparisons, history) is in [`docs/examples/`](docs/examples/SUMMARY.md).

> **Disclosure:** each demo agent's v1 configuration is **intentionally flawed**, so the loop has genuine failures to find. The failures and fixes are real executions of those configurations; nothing is hard-coded.

**IT Incident Resolution Agent** (19-case golden set, SimLM agent, rubric judge)

| | Baseline v1 | Experiment 1 (v2) | Experiment 2 (v3) |
|---|---|---|---|
| Change tested | — | untrusted-content instruction + `reset_password` description + identity-verification prerequisite | `retrieval_min_score` 0.05 → 0.0617 |
| Task success | 31.6% | **94.7%** | **100%** |
| Tool selection accuracy | 73.7% | 94.7% | 100% |
| Critical failures | 7 | **0** | 0 |
| Cases fixed / regressed | — | **12 / 0** | 1 / 0 |
| Quality gates | FAIL | **PASS** | PASS |

- **Failure analysis:** 13 failures → 5 clusters (prompt injection ×7, wrong tool for password resets ×3, missing identity check, tool timeout handled unsafely, weak retrieval match). The primary category matched the human labels on **13/13** cases.
- Experiment 2's recommendation was flagged **low confidence**: the margin to the weakest correct retrieval is only about 0.01. It passed, but the brief explains why improving knowledge coverage would be the more robust fix.

**HR Employee Support Agent**: same platform, no HR-specific evaluation code

| | Baseline v1 | Experiment (lower `escalation_threshold` 0.2 → 0.07) |
|---|---|---|
| Task success | 66.7% | **91.7%** |
| Critical failures | 2 | 0 |
| Cases fixed / regressed | — | 3 / 0 (HR-012 still fails and is reported as such) |

**Real LLM model comparison (Ollama, local).** This is a model-comparison experiment on the approved IT configuration: SimLM vs `llama3.1:8b` as the agent, with `qwen3:8b` as the judge for both. Same 19 cases.

| | SimLM | `llama3.1:8b` |
|---|---|---|
| Task success | 100% | **52.6%** |
| Tool selection / argument accuracy | 100% / 100% | 63.2% / 72.7% |
| Critical failures · regressions | 0 · — | 3 · **9** |
| p95 latency | 5.8 s (modeled) | 75 s (measured) |
| Cost | modeled | not measured (local) |
| Gates | — | **FAIL** |

The checks caught real model errors: invalid escalation-team arguments, a needless certificate reissue, a duplicate password reset, and over-escalation. On one case the agent behaved correctly but the 8B judge disagreed, and the case was routed to human review. Details are in [EVALUATION_DESIGN.md §7](EVALUATION_DESIGN.md#7-real-model-results) and [`docs/examples/model_comparison_simlm_vs_ollama.json`](docs/examples/model_comparison_simlm_vs_ollama.json).

**Judge calibration** (16 human-labelled answers): the rubric judge reaches 100% agreement (κ 1.0; same author, so optimistic). The **Ollama `qwen3:8b` judge reaches 93.8% (κ 0.875)**, with 1 false fail and 0 false passes.

## Screenshots

| | |
|---|---|
| ![Quality dashboard](docs/screenshots/01-dashboard.png) **Quality Dashboard**: KPIs, explainable gate verdict, failure distribution, runs and experiments | ![Failure analysis](docs/screenshots/02-failure-analysis.png) **Failure analysis**: clusters with trace evidence, hypothesis and confidence |
| ![Trace](docs/screenshots/03-trace.png) **Execution trace**: candidate tool scores show why `reset_password` was never chosen; PII is redacted | ![Recommendations](docs/screenshots/04-recommendations.png) **Recommendations**: prioritized patches shown as diffs, human approval |
| ![Experiment](docs/screenshots/05-experiment-comparison.png) **Experiment**: baseline vs candidate on identical cases, fixed/regressed, metric deltas | ![Regression](docs/screenshots/06-regression-learning.png) **Regression & learning**: regression suite with origin, gate decisions |
| ![Providers](docs/screenshots/07-llm-providers.png) **LLM providers**: Ollama, Claude, OpenAI, Gemini; connection tests; encrypted keys | ![Model roles](docs/screenshots/08-model-roles.png) **Model roles**: separate agent, judge and analyzer models |

## Quick start

Requires Python 3.11+ and Node 18+ (Docker optional). Ollama is optional.

```bash
git clone <this repo> agenthill && cd agenthill
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
(cd frontend && npm ci && npx vite build)
.venv/bin/python scripts/prepare_demo.py          # seeds agents and datasets only; no results
.venv/bin/uvicorn --app-dir backend agenthill.main:app --port 8000
```

Open http://localhost:8000. Then follow the demo:

1. **Quality Dashboard → Run evaluation**
2. **Inspect run → Run failure analysis**, then open case `IT-003`
3. **Recommendations & Experiments**: approve the top three, tick them, then **Create experiment**, **Approve execution** and **Run baseline vs candidate**
4. Read the comparison, then **Accept candidate**
5. In **Traces**, open `IT-003` on the baseline run → **Confirm failure → regression test**, then **Regression & Learning → Run regression suite**
6. **Agents & Datasets → + Register agent → Load template: HR Employee Support Agent**, then run it from the dashboard

**Docker:**

```bash
docker compose up --build        # http://localhost:8000; seeds demo data on first start
```

The container reaches Ollama on the host at `http://host.docker.internal:11434`. This works out of the box on Docker Desktop (macOS, Windows), and `docker-compose.yml` adds the `host-gateway` mapping for Linux. On Linux, Ollama must also listen beyond loopback: `OLLAMA_HOST=0.0.0.0 ollama serve`. You can change the URL at any time in **Settings → LLM Providers**.

### Using real models

1. Ollama: `ollama pull llama3.1:8b`. Cloud: have an API key ready.
2. **Settings → LLM Providers**: set the Ollama URL or paste a key (it is stored encrypted on the server and never shown again), enter a model, then **Test connection** and **Save**.
3. **Model roles**: choose the judge and analyzer models (**Validate structured output** first). To run an agent on a real model, create a config version with that model, or use **Recommendations & Experiments → Compare models**.

Keys can also come from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `GEMINI_API_KEY` (see `.env.example`). Keys never go to the browser, the database, traces or logs.

## Tests and verification

```bash
.venv/bin/python -m pytest -q                       # 37 passed + 5 live-provider tests skipped unless enabled, ~6 s
AGENTHILL_LIVE_OLLAMA=1 .venv/bin/python -m pytest tests/test_live_providers.py -q   # live Ollama
.venv/bin/python scripts/run_full_loop.py           # full loop → docs/examples/
.venv/bin/python scripts/record_demo.py --no-video  # full UI loop as a Playwright E2E test
.venv/bin/pip install -r requirements-demo.txt && .venv/bin/python -m piper.download_voices --download-dir var/voices en_GB-cori-high
.venv/bin/python scripts/record_demo.py             # re-record the narrated demo (needs ffmpeg)
```

The suite covers dataset loading and validation, deterministic scoring, trace capture, redaction, failure classification against labelled data, recommendations, experiment comparison, regression detection, quality gates, approvals and roles, tenant isolation, persistence, credential non-leakage, and the provider layer. A scripted fake provider drives the real LLM-agent, LLM-judge and analyzer code paths offline.

| Verification item | Status |
|---|---|
| Ollama agent + Ollama judge, full 19-case runs | **Verified** (llama3.1:8b agent, qwen3:8b judge; v1 and v3 configurations) |
| SimLM vs Ollama model-comparison experiment | **Verified** ([example](docs/examples/)) |
| Ollama agent + cloud judge | **Unverified**: no cloud API key was available in the build environment |
| Claude / OpenAI / Gemini model calls | **Unverified** for successful calls (no keys). **Verified**: invalid-key → `auth` error from each real endpoint; missing key → `missing_key` |
| Model switching without code changes | **Verified** (config versions, model-comparison experiments, fake-provider tests) |
| Ollama server unavailable / model not installed | **Verified** (`unavailable`, `model_not_found` with hints) |
| Persisted model configuration and history | **Verified** (models stored on every run; tests) |
| No credentials in DB, traces, audit, API responses or git files | **Verified** (test asserts the absence of a stored key; `var/` is git-ignored) |
| Docker image build and run; container → host Ollama (`host.docker.internal`) | **Verified** on macOS (Docker Desktop). Linux `host-gateway` mapping: configured, not tested |
| Demo video plays in GitHub's rendered Markdown | **Not yet verified.** The README embeds a GIF preview linked to the MP4. Once pushed, open the README on GitHub to confirm. For an inline player, drag the MP4 into a GitHub issue or PR comment and paste the resulting `github.com/user-attachments/...` URL on its own line here |

## Repository layout

```
backend/agenthill/        FastAPI app and the 10 platform components
  runtime/                Agent Execution Service: LangGraph agent, SimLM, LLM agent, tool gateway, tracing, redaction
  environments/           Simulated IT and HR enterprise APIs (sandboxed, tenant-scoped)
  evaluation/             Deterministic checks, judges, metrics, calibration, run engine
  analysis/               Failure Analysis Agent
  recommendations/        Recommendation Engine
  gates/                  Regression detection and quality gates
  llm/                    Provider layer (Ollama, Anthropic, OpenAI, Gemini), encrypted credentials
  services.py, api/       Registry, datasets, experiments, history; REST API
frontend/                 React (Vite) UI
data/                     Synthetic environments, knowledge bases, agent templates, golden datasets, calibration labels
scripts/                  prepare_demo.py, run_full_loop.py, record_demo.py (narrated demo), screenshots.py
tests/                    pytest suite
docs/demo, docs/screenshots, docs/examples   Demo video, screenshots, exported real-run evidence
```

All data is synthetic. Employees, IDs, emails and records are fictional.
