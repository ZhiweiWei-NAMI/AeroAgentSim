# C1b: configuration and capture IDs

New runs no longer pin scenario, registry, behaviour, model-factory or asset content hashes. Authoring validation, actual capture metadata, causal records and deterministic journal replay remain available.

The feed contract stays `aeroagentsim.viewer-feed/v1`. Existing string fields `registryDigest`, `packageDigest` and `irDigest` remain readable for older clients. For new runs they contain plain IDs: `<scenario-id>/registry` and `<package-id>@<revision>`. Old run directories retain their recorded values. Treat these fields as opaque IDs; do not present them as integrity proofs.

Removed metadata fields:

- Run manifest: `scenario_digest`, `registry_digest`, `compiled_registry_digest`, `behaviour_digests`. Replacements: `registry_id`, `behaviour_packages`.
- Compiled registry: top-level `digest`, details `normalization_digest`, provenance `input_sha256` and `git`.
- Behaviour IR: `digest`, `ir_digest`, evaluator `sources`. Replacement: `package_id`; evaluator `version` remains.
- Authoring catalog/type inspection: `digest`; behaviour export: `sha256`, `semantic_digest`; behaviour validation: `input_digest`, `compiler_digest`, `compiled_digest`; full scenario validation: `digest`. Actual validity and authored-path error responses remain.
- LangGraph factory metadata: `source_sha256`. Factory name and library version remain descriptive metadata.
- Generated SUMO network metadata: `sha256`. Generation status, command and network dimensions remain.

Authored scenarios/packages now accept path references without hash pins. Old reference annotations are accepted for compatibility but are never checked. New package IDs preserve instance ordering and deterministic identity; journals produced before and after this identity migration can differ, and each replays its own recorded states without engine evaluation.

## Kept uses

- `agents.tools.ToolGrant.name` retains a bounded internal SHA-256 grant key to avoid punctuation/truncation collisions in model tool names. It is not a model-factory/source pin.
- `behaviours.bindings.stable_id` uses SHA-256 solely as a bounded, internal key for potentially large role/episode tuples. It does not attest to package, asset or journal content.
- Kernel-owned registry/manifest digests remain in kernel journal headers because the installed kernel replay format requires them. C1b neither edits the kernel nor adds duplicate platform pins. Removing those kernel checks requires a separate kernel format migration.
- Kernel time seals, causal references, journal codec versions and ownership/authoring validation are correctness features.
- Digest-named compatibility fields and old-file readers do not calculate or check integrity hashes.
- Container ownership verification prevents operating on other jobs' containers. LangGraph `JournalClient.verify()` checks recorded-call consumption and token budgets; neither is artifact verification.
- Remaining verification names under legacy `manager/` and `visualization/` belong to the v1 modules being deleted by C1a; C1b does not edit those modules.

Capture compatibility:

- Artifact records retain `digest`, `camera_digest`, `asset_digest`, and traffic events/fields retain `png_sha256`, `source_digest`, `upload_verified` names. New values are request/asset/source IDs, never content hashes. Artifact filenames are reversible URL-safe request IDs. Unknown valid IDs return 404.
- Downloads no longer return `X-Content-SHA256`; `ETag` is an opaque artifact ID.
- Uploads require the actual capture request, PNG, timestamp and ingress source stamp. Old `digest`/`byte_count` annotations are optional and ignored; storage derives the ID and length from the request and received bytes. Actual PNG decoding, outstanding-request correlation and idempotency remain validated.
- Replay recording manifests drop `journal_sha256`, `camera_digest` and video `digest`; they retain source cuts, view IDs, media length and frame timing. Imported demo inputs use `source_ids`, `asset_id`, `scene_id` instead of hash pins.
- Frontend capture asset loading currently checks hashes in `frontend/src/observations/capture.ts`. C1b does not edit frontend; remove its hash checks when updating the asset descriptors. Capture request `asset_digest` is now a plain asset ID.
- Optional registry compiler `--report` and committed verification/gate reports were removed. Compiler diagnostics and type inspection remain.

HTTPS certificate checks and container label/ownership checks remain. PNG signature, chunk CRC/decompression and dimensions are image-format validation, not a sealed-artifact protocol. Archived artifacts with old hash-named files remain readable without hashing their content.

Upstream Git revisions in `tools/sync_assets.py` (`PX4_REVISION`, `STYLE_REVISION`) and container dependency `*_COMMIT` arguments remain version selectors for compatible geometry/build APIs. They do not verify downloaded content or pin runs/snapshots. Versioned dependency requirements remain; wheel/archive content hashes are removed.

## E3a/R5 merge follow-up

- `GET /v1/studio/demo-capture-assets` returns the same manifest format with a plain top-level `asset_id` (`traffic-city-assets/v1`). Each file has `url` and `asset_id`; `sha256` and `byte_count` descriptors are removed. The capture request compatibility field `asset_digest` contains this asset-set ID, never a manifest hash. City assets are allowlisted, constrained to the configured root and required to exist; editing their content does not require repinning.
- Traffic capture events (`traffic.capture.stored`, `traffic.capture.accepted`) now emit `asset_id` instead of `png_sha256`. The graph metadata field is `traffic.capture.asset_id` instead of `traffic.capture.png_sha256`; it contains the request-derived artifact ID. This supersedes the earlier `png_sha256` compatibility note above. Existing generic artifact `digest` fields remain opaque request-derived IDs.
- The unused recording metadata slot is now `traffic.recording.asset_id` instead of `traffic.recording.sha256`; it no longer advertises a content hash.
- Browser scenario normalization accepts and discards an old `registry.digest` annotation, using the actual snapshot schemas to restore numeric types.
- The frontend session must update manifest loading, capture labels and traffic-field lookups; this backend follow-up does not edit frontend files. Recorded older journals remain readable without content-hash verification.
