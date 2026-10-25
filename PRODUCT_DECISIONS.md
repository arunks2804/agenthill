# AgentHill: Product Decisions and Trade-offs

Each decision lists the problem, the options considered, the choice made, and what it costs.

---

### D1. Hybrid evaluation: deterministic checks first, judge second
**Problem:** "Did the agent do the right thing?" mixes objective facts (which tool, which arguments, which order, what changed, what was forbidden) with semantic quality (is the answer correct, complete and grounded).
**Options:** (a) LLM-as-judge for everything; (b) deterministic only; (c) hybrid.
**Choice:** (c). There are seven deterministic checks over tool calls, sandbox state and policy. A judge scores only the semantic criteria. A case passes only if both agree.
**Why:** facts should not depend on a model's opinion. In the IT baseline, 10 of the 13 failures scored above 0.8 with the judge, because the answer text read well. They still violated policy, by attempting `grant_admin_access`, or skipped required actions, such as resetting a password. A judge-only system would have scored them as passes.
**Cost:** expected behaviour must be written down per case (tools, order, state). That is more work for dataset authors, which is why templates and generation-with-review exist.

### D2. A deterministic reference model (SimLM) as the default agent engine
**Problem:** the demo must show that a *specific change caused* an improvement. LLM variance makes before/after comparisons noisy, and a reviewer cannot reproduce them without keys or GPUs.
**Options:** (a) real LLM only; (b) mocked agent outputs; (c) a transparent deterministic model driven by the configuration, plus optional real LLMs.
**Choice:** (c). SimLM's behaviour is a function of the configuration under test (tool descriptions, prompt instructions, thresholds), so improving a tool description *really* changes tool selection.
**Why not (b):** mocked outputs would be fabricated results. **Why not only (a):** reproducibility for anyone cloning the repository, and causality for the demo.
**Cost:** SimLM is not an LLM. It understands a small instruction grammar and lexical similarity. Real-model behaviour is shown separately (Ollama runs, model comparisons), and the limitation is disclosed.

### D3. Evidence first, LLM narrative optional
**Problem:** LLM-generated "root causes" often sound plausible but are generic and uncited.
**Choice:** deterministic evidence rules always produce span-referenced evidence and a hypothesis with a confidence level. An optional LLM review must cite evidence IDs, or it is rejected.
**Why:** a diagnosis that does not point to a trace step cannot be verified or audited. Keeping evidence and hypothesis separate lets a reviewer disagree with the interpretation without discarding the facts.
**Cost:** the rules are maintained by the platform team. Novel failure types show up as *cause unclear (low confidence)* until a rule exists. That outcome is honest, but less magical.

### D4. Recommendations as concrete patches that require an experiment
**Options:** free-text advice; auto-apply fixes; reviewable patches plus an experiment.
**Choice:** patches (prompt append, tool description replace, setting change), each shown as a diff, approved by a human, and validated by an experiment.
**Why:** a patch is testable and reversible. Auto-apply would be uncontrolled self-modification, and free text gets lost.
**Cost:** a patch is only as good as its rule. Experiment 2 (the retrieval threshold) shows the engine flagging its own low confidence when the evidence margin is thin.

### D5. Immutable, versioned configurations and datasets
**Choice:** every configuration edit creates vN+1 with a parent link. Every dataset publish creates an immutable snapshot, and runs reference versions.
**Why:** an experiment is only meaningful if the baseline cannot change underneath it, and history must be reproducible months later.
**Cost:** more rows and more versions in the pickers. That is acceptable.

### D6. Generated test cases are never ground truth until reviewed
**Choice:** generated cases enter as `pending_review` and are excluded from publishing until a human approves them (with optional edits that are re-validated).
**Why:** an LLM writing both the questions and the "right answers" would grade itself. Domain experts own correctness.

### D7. Quality gates are explainable and explicitly not a release certificate
**Choice:** six team-configurable gates. Each reports its value, threshold and reason. Unmeasurable gates are labelled `not_measured` (verdict `PASS_WITH_UNMEASURED`). Every verdict carries a disclaimer that passing is not proof of production readiness.
**Why:** PMs need to know *which* bar failed. Silently passing an unmeasured cost gate, for example for a local model, would be misleading. The dataset covers known scenarios, not live traffic.
**Note:** the IT p95-latency gate is set to 6000 ms because a fault-injection case deliberately includes a 5000 ms tool timeout. A team would normally exclude fault cases from latency gates. We kept them in and documented it instead.

### D8. Provider-neutral JSON action protocol instead of native tool calling
**Options:** each provider's native tool-calling API; LangChain abstractions; a single JSON protocol.
**Choice:** a single JSON protocol (`call_tool` or `final`) over plain chat.
**Why:** one contract across Ollama 8B models, Claude, GPT and Gemini, with identical traces and checks, and models that lack native tool calling still work. Structured-output support differs by provider: native JSON for Ollama, OpenAI and Gemini; prompted JSON for Claude. That difference is detected and recorded.
**Cost:** it may under-represent models that are much stronger with native tool calling. Small-model protocol slips are repaired once and recorded (`protocol_repair`), never hidden.

### D9. Separate model roles: agent, judge, analyzer
**Choice:** the agent model is stored per config version (so it is reproducible and comparable). Judge and analyzer models are platform settings.
**Why:** teams want to compare agent models while holding the judge constant. Using the same model as both agent and judge introduces self-preference bias, so it is flagged on every run where it happens.

### D10. Never silently substitute a provider
**Choice:** a provider failure fails the run with a coded error and a hint, or marks the case `error` and sends it to review. There is never a fallback to another model.
**Why:** a comparison of "Claude vs. Ollama" that quietly ran on Ollama would be worse than no result at all.

### D11. Credentials stay on the server, encrypted
**Context:** the reference project (IAMSentinel) stored keys in browser `localStorage`. That is acceptable for a single-user tool, but not for a shared multi-team platform.
**Choice:** keys are sent once to the backend, encrypted with Fernet in `var/credentials.enc`, and exposed only as `{configured, source}`. They are scrubbed from logs and traces. Environment variables are supported.
**Cost:** in this prototype the encryption key lives next to the store unless `AGENTHILL_SECRET_KEY` is set. Production would use a KMS or a vault.

### D12. Modular monolith and SQLite
**Choice:** ten components as Python packages with explicit contracts, one process, SQLite.
**Why:** clear boundaries without network hops and deployment overhead. Anyone can run it in one command. The schema is PostgreSQL-compatible.
**Cost:** runs execute in in-process threads; there is no distributed queue (see LIMITATIONS).

### D13. Simulated enterprise systems with per-case sandboxes
**Choice:** synthetic, tenant-partitioned JSON environments, deep-copied per case, behind a gateway that refuses non-sandbox modes.
**Why:** safe, deterministic and repeatable. State-change checks are exact, and adversarial cases (injection, cross-tenant) can be tested without risk.

### D14. Human roles in the loop
**Choice:** PM or Evaluation Engineer approves experiment execution; only the PM accepts or rejects; anyone can review cases, diagnoses and judge verdicts. All of it is audited.
**Why:** the platform accelerates decisions; it does not make them.

### D15. Measure the evaluation itself
**Choice:**
- judge calibration against human labels (agreement and κ);
- live agree/disagree reviews;
- analyzer accuracy against labelled failures;
- diagnosis confirm/reject;
- time-to-verdict and time-to-onboard taken from audit timestamps.

Metrics that cannot be measured are shown as such.
**Why:** an evaluation platform that does not evaluate its own judgements just moves the uncertainty somewhere else.
