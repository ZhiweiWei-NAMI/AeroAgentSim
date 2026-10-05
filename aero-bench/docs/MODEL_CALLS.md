# Actual model and CLI calls — 2026-08-28 through 2026-08-31

This record distinguishes a process that was truly started from code that was actually accepted. Session IDs and aggregate usage came from local CLI metadata; API keys, request bodies, raw model transcripts, hidden reasoning and provider metadata are intentionally excluded.

## Claude Code final Inspection repair and release (2026-08-31)

- Interface: host-managed Claude Code session.
- Model: Claude Opus 5.
- Accepted scope: PX4 paused-barrier/deferred-command lifecycle, benchmark-owned
  patched MAVSDK server, real camera target/observation path, scoped Harness
  runtime validation, canonical exact-nine evidence, production Inspection
  Verifier, public metric selector binding, coherent same-revision image set,
  bounded formal run, exact Public Trace browser load, release lock, and r5
  documentation/evidence packaging.
- Runtime-code commits through the locked image baseline include `7a11241`,
  `aeee9ad`, `951bcaf`, `98925ef`, `f6d3a1f`, and `6694f83`.
- The externally built validation participant is recorded as a submission, not
  as benchmark-authored policy. No remote push was performed.

## Codex

- Primary integrator: this host-managed Codex session, sole writer of `main`.
- Locally discovered CLI: `codex-cli 0.150.1` (local install path omitted in this export).
- Invocation: managed by the host application rather than a child `codex` shell command. Collaboration workers delivered isolated reviews/commits; Codex reviewed and cherry-picked or rejected them.
- Accepted ownership: core Schema/Resolver, Gateway, runtime ledger, Docker/Kubernetes executors, Inspection integration hardening, frontend final integration, all fact-finding fixes, validation and packaging.

## DeepSeek through dsh

- CLI: `dsh 0.1.0-rc.6`.
- Discovered configuration at call time: provider `token0`, model `deepseek-v4-flash` (credential values are not recorded).
- Command shape: `dsh --profile headless "<contents of prompts/dsh/01_SCHEMA_AND_INSPECTION.md plus repository context>"`, executed in a dedicated local worktree (path omitted in this export).
- Recorded sessions:
  - `41b7e6a0-1177-4fec-8de1-7972674d61cc`: about 2 seconds; no usable delivery.
  - `9ed99385-a6a6-4bcf-bb78-01ebcb74a0e9`: about 349 seconds; read/glob/bash activity but no independently attributable write patch.
- Accepted delivery attribution: none directly from dsh. An isolated collaboration worker produced Inspection patch commit `8005b07`, integrated by Codex as `55a3796` and then materially corrected in later main commits; it must not be labelled a DeepSeek implementation.

## Grok through grok

- CLI: `grok 1.0.5 (5115b46bc9)`.
- Local configured default: `grok-4.6`, reasoning effort `high`.
- Recorded prompt sessions in the isolated Grok worktree:
  - `01a04794-18fb-78c0-a0a2-f82ea13f9154`: PX4/Gazebo/MAVSDK prompt, about 334 seconds.
  - `01a04799-67c2-76d2-af31-489811b0e47a`: ns-3 prompt, about 348 seconds.
  - `01a0479e-ce77-7502-98ae-2408f3a21ee7`: SUMO/Airspace prompt, about 65 seconds.
- Prompt sources: `prompts/grok/01_PX4_GAZEBO_PROVIDER.md`, `02_NS3_PROVIDER.md`, and `03_SUMO_AIRSPACE_PROVIDER.md`.
- The outer process argv was not retained; the session store proves CLI, cwd, prompt, session ID and timing but does not justify inventing an exact flag order.
- Delivery: isolated commit `7178906` (`2867` inserted lines). Review found internal/fake transports rather than real PX4, MAVSDK, ns-3 and SUMO execution identities. The entire delivery was rejected and none of it was merged.

## GLM through zcode

- CLI: `zcode 0.16.5`.
- Requested model: `glm-5.3-flash`.
- The current configured route is the official provider `bigmodel` on
  `glm-5.3-flash`; fallback is disabled. Credentials, base URLs and raw session
  text are not recorded in this repository or package.
- GLM produced isolated Business, Trace and Viewer candidates. Only accepted
  commits present in current main are described here:
  - Business: `c37efd1`, `5dbd41a`, `4803403`.
  - Trace: `7e1eaf2`, `c961dee`, `b38747d`.
  - Viewer: `43fd567`, `e2ead6c`, `4a56585`.
- Unaccepted candidates and any fallback/relay output are not implementation
  evidence. The list records accepted repository boundaries, not hidden model
  transcripts or credentials.

## Sol final backup read-only review

- Reviewer model: exactly `gpt-5.6-sol`.
- Reasoning effort: `max`.
- Target: current main commit `d973f3c`.
- Invocation: host-managed read-only collaboration agent; no persona substitution and no production-file write permission.
- Result: P0=0 and P1=0.
- Scope: repository/runtime **GO** is limited to the audited Docker/Business/Trace/Verifier paths. Research/benchmark **NO-GO** remains because the formal Provider, Agent, Observation, production Inspection Verifier and PX4 chain is incomplete.
- This record does not treat any earlier stalled or unfinished Sol process as a result. Sol did not modify files; no credentials, raw session text or hidden reasoning is retained.

Codex accepted and tested the final P1 fixes `26cebc8` (non-finite verifier
metric rejection), `bbc279b` (partial aborted trace projection), and `d973f3c`
(one simulation time per tick).

## Design reference images

- The repository contains two design-only empty-state references committed in
  `f2ea574`: `frontend/design/operations_empty_state_reference.png` and
  `frontend/design/replay_empty_state_reference.png`.
- No reproducible built-in image-generation invocation is retained for this
  handoff. Their origin is therefore not asserted as a model call.
- They guided the panel hierarchy and honest empty-state wording in `9b13581`.
  They are not runtime assets and no mockup value is read by the Viewer.

## Gemini

Gemini was not called for development, review or acceptance. The packaged Gemini material is a redacted, explicitly untrusted historical record retained only so a later reviewer can understand why the old demonstrations were rejected.
