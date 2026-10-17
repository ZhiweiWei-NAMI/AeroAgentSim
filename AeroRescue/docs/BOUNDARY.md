# Platform/application boundary

Platform: src/aeroagentsim/adapters and containers/px4-gazebo/service provide native sensor/estimate streams, cancellation, wireless events and physical payload actuation. No new application import is allowed in platform code. Existing main lowalt rescue expansion, planner, services and tests remain unchanged.

AeroRescue: src/aerorescue/companion contains rescue execution, perception, radio protocol, energy ledger and assignment solver; skills/<name> contains the 21 independently discoverable skill manifests/manuals; harness contains the Cordis decision owner; deploy, containers, scenarios, tools and docs are application delivery assets.

Source mapping from pre-split commit 56e1bc9b0aec286337b7b94aa3a7b1436cc54288:

- src/aeroagentsim/companion → AeroRescue/src/aerorescue/companion
- native changes in packs/lowalt/rescue_planner.py → companion/assignment_solver.py (legacy platform planner unchanged)
- skills/rescue → AeroRescue/skills
- containers/companion → AeroRescue/containers/companion
- deploy/rescue → AeroRescue/deploy
- tools/companion → AeroRescue/tools
- scenarios/rescue/rooftop-flood-companion.yaml → AeroRescue/scenarios/rooftop-flood-companion.yaml
- docs/rescue-companion → AeroRescue/docs
- docs/guides/rescue-companion.md → AeroRescue/docs/guide.md

Canonical skill/scenario source folders are copied into wheel resources at build time. Adapter resources carry their source SHA in resources/SOURCE.md. Platform primitive settings (RGB-D mount/intrinsics and payload/receiver geometry/topics) are explicit application scenario configuration; the platform provides no rescue defaults.

Cordis composition and the official DSH agent loop own role/skill/plan selection and tool dispatch. The Node launcher starts Python with `--execution-only`. Python may reject unsafe, stale or unsupported inputs, execute bounded requested skills, and report pending/completion; its pure rule proposals do not independently dispatch the next action. Both rule and model modes use the DSH tool loop and the same discovered skill manifests.

Local evidence covers a real queued `read-evidence` completion and official DSH resume without repeated notification or execution. It does not establish autonomous native rescue behavior. Docker/image construction and native flight/radio acceptance remain blocked or not run; see [harness verification](../harness/README.md#verification-status).
