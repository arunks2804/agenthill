# AgentHill: Limitations and Prototype Boundaries

This document says plainly what is real, what is simulated and what is not covered.

## Implemented and real

- All eight capabilities as one integrated application: FastAPI backend, React UI, SQLite persistence.
- Two demonstration agents, in two domains, running on the same evaluation, diagnosis, experimentation and regression infrastructure.
- Deterministic checks, rubric and LLM judges, judge calibration, trace capture with OTLP export, redaction, failure clustering, patch recommendations, paired experiments, regression detection, quality gates, regression suites, experiment history and the audit log.
- Multi-provider model layer: Ollama (verified with real runs), and Anthropic, OpenAI and Gemini through their official SDKs (verified only for authentication and error paths; see below).
- Encrypted credential storage, tenant isolation, role-checked approvals.
- 37 automated tests, a Playwright end-to-end flow and recorded demo, a Docker image.

## Simulated

| Area | What is simulated | Effect |
|---|---|---|
| Enterprise systems | IT and HR APIs are Python functions over synthetic JSON, copied per case | No network or integration failures beyond injected faults |
| Knowledge bases | 9 IT and 8 HR synthetic articles with structured runbook steps | Retrieval is TF-IDF, not embeddings |
| Default agent model | **SimLM** is a deterministic decision procedure, *not* an LLM | It follows instructions only if they match its documented grammar (ARCHITECTURE §3.2), and it selects tools by lexical similarity. It makes configuration effects causal and reproducible, but it does not represent LLM behaviour in general |
| Latency and cost for SimLM | modeled from token estimates and fixed tool latencies | Labelled *modeled* in the UI and data |
| Tool latency | fixed per tool (plus 5 s for injected timeouts) | Real-model runs measure only model latency |
| Identity and auth | user and tenant are chosen in the UI and sent as headers | Role checks are real, but nobody authenticates; production would use SSO/OIDC |

## Intentionally constructed

- **IT v1:** vague `reset_password` description; no identity-verification instruction; no untrusted-content instruction; `retrieval_min_score` 0.05.
- **HR v1:** `escalation_threshold` 0.2.
- The knowledge base contains one community article with an embedded injection instruction.

The failures these produce are genuine executions of those configurations. Before/after results are computed, not written.

## Known limitations

1. **Calibration and labelled samples are small and come from the same author.** The 16-example judge set and the 16 failure labels were written by the dataset author, the same person who wrote the rubric judge and analyzer rules. Perfect scores on them are optimistic. The independent `qwen3:8b` judge scored 93.8%.
2. **Analyzer coverage.** The evidence rules recognise the categories listed in the brief. New failure patterns are reported as *cause unclear (low confidence)*. The richest evidence (candidate tool scores) is only available for SimLM; for real LLMs the analyzer uses the model's stated reason and observed calls, at lower confidence.
3. **Recommendation rules are heuristic.** Threshold patches are derived from observed score gaps and can overfit the dataset; the retrieval recommendation flags its own thin margin. Tool-description patches append runbook phrasing; a human should edit them before they reach production.
4. **Real LLM agents use a JSON action protocol, not native tool calling.** Strong cloud models might do better with native tool APIs. 8B local models make protocol slips: one repair per turn is applied and recorded.
5. **Cloud providers are unverified end to end.** No Anthropic, OpenAI or Gemini keys were available while building. Invalid-key, missing-key and error mapping were tested against the real endpoints; successful model calls were not. `tests/test_live_providers.py` reports these as *skipped/unverified* until keys are supplied.
6. **Context windows** for cloud models use a conservative built-in table. For Ollama, the configured `num_ctx` (8192) is used rather than the model's maximum.
7. **Cost** is reported only from user-configured prices. The prototype ships no price list. Local inference cost is not measured.
8. **Execution scale.** Runs execute sequentially in in-process background threads. There is no queue, concurrency control or rate limiting for large datasets or many teams. A 19-case run with a local 8B agent takes about 13 minutes, and about 28 minutes when an 8B judge also runs (measured on a laptop).
9. **Credential encryption key.** Unless `AGENTHILL_SECRET_KEY` is set, the Fernet key is generated next to the encrypted store under `var/` (chmod 600). That protects against accidental disclosure, not against host compromise. Production would use a KMS or a vault.
10. **Redaction** is pattern-based (national ID, email, phone, secret-like tokens, named sensitive fields). It is not a general PII detector.
11. **Single-process SQLite.** The schema is PostgreSQL-compatible, but only SQLite has been exercised.
12. **"Teams able to self-serve"** is not measurable without multi-team usage. The UI reports it as not measured.
13. **Accepting an experiment does not deploy anything.** It records the approved configuration. Integrating with a real deployment pipeline is out of scope.
14. **Demo voiceover** is AI-generated (Piper `en_GB-cori-high`, run locally), and the video discloses this. The narration is synchronized scene by scene but not lip-synced to the pointer frame by frame.
15. **Demo video hosting.** GitHub's file view does not play MP4s, so the demo is served from GitHub Pages (https://arunks2804.github.io/agenthill/). The MP4 is also in the repository for download.
