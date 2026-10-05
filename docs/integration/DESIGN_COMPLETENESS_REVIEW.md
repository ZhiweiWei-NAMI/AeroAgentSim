# Original design and current integration: completeness review

Status: source-grounded review draft, 2026-10-05. This distinguishes concepts
already specified in the original project plan, decisions sharpened in the
current integration contract, and implementation gaps supported by code.

## Evidence boundary

The original project plan, *空地协同低空城市事件平台建设方案*, version
2026-10-04, was read in full for this review. Its section references below are
stable within that version. The current P02 release summary and graph page were
also read. Original Atlas catalog bytes were not available because archive
materialization was denied by the current access scope.

This supports a comparison against the **written design**. It does not support
assertions that a specific field or concept is absent from the full 1,686-rule
predicate catalog. Catalog omissions require the source-level audit described in
[MAPPING_AUDIT.md](MAPPING_AUDIT.md). No private source archive or raw project
record is copied into this repository by this change.

## Already in the original design: preserve, do not re-invent

| Concept | Evidence in original plan | Requirement for current integration |
| --- | --- | --- |
| Facts, constraints and objectives | §2.2 separates factual inputs from scoped constraints/targets | Keep descriptive facts separate from normative requirements and goals. A publication record can be a fact without making its content physical truth. |
| Three assessment axes | §2.3: physical feasibility, permission/compliance, task suitability | Preserve each result and supporting reasons; do not collapse them into a single undifferentiated admission flag. |
| Truth, measurement and uncertainty | §2.4; §4; §5.1 | Distinguish simulator truth, measurements and estimates. Keep channel, calibration, covariance/error and missing uncertainty; agents must not read hidden truth. |
| Entity/relationship identity and time | §3.5; §4 | Preserve entity/relation keys, sessions, task/resource generations, counter windows, sampling and coordinate/time semantics. PR10 adds concrete immutable Ref/availability machinery; this is not a newly discovered need. |
| Capabilities and reuse | §3.2; §7.2 | Share rules across applicable roles/capabilities instead of copying a rule per entity type. Capability does not itself authorize action. |
| Full task lifecycle and effect evidence | §3.4; §8; §9 | Retain preparation, reservation, execution, handover, acceptance, cancellation, retry and recovery. A controller receipt proves only its defined operation. |
| Regulatory source and applicability | §6 | Keep issuer/authority, jurisdiction, entity/activity scope, altitude, time, exceptions, version and source. Present unresolved authority conflicts; engineering thresholds are not law. |
| Hardware/energy/thermal and electromagnetic dependencies | §5 | Preserve explicit quantities and mechanisms. Correlated failures are not proven causes; unavailable causal definitions stay unresolved. |
| Network and compute activity | §8 | Keep exact device/session/flow/task identities, lifecycle, queues, results and recovery. Link state alone is not task success. |
| Coherent generation and replay | §10.1 | Preserve identity, motion, custody, energy/resource and time consistency. Selecting authored traces is not arbitrary scenario generation. |
| Coverage dimensions and business scope | §3; §13; Appendix B | Preserve six many-to-many coverage axes and the 35 explicitly listed business workflows; do not replace them with the eight module categories. |

## Decisions sharpened now

These are contract clarifications or additions at the integration boundary, not
claims that every associated predicate was absent from the original graph.

1. **State versus fact:** original §2.1 used “state” for a runtime value. The fixed
   vocabulary now uses state specification for the declared field and fact for
   its runtime record, including validity, availability and source. Preserve the
   meaning when migrating wording rather than duplicating physical values.
2. **Independent agent:** name the decision participant outside the entity.
   Declare one-to-many or many-to-one agent/entity relations and authority scopes.
   Overlap needs explicit arbitration; no algorithm or permission is invented.
3. **Strategy interface:** the agent calls a strategy with permitted observations,
   requests, objectives and constraints; the output is a decision. The original
   plan referred to task strategy (§2.3) but did not establish this complete
   independent-agent interface in the reviewed text.
4. **One selected computing authority per field:** original §4 allowed several
   producers to implement one contract. That remains valid across configurations
   or as distinct observations, while a configured run selects one authority for
   each exact state scope. It does not permit concurrent overwrites.
5. **Constraint enforcement binding:** retain declarative graph definitions and
   name which execution module enforces each physical/resource/regulatory
   constraint, with evidence. Declaration and rule evaluation alone do not prove
   execution enforcement.
6. **Explicit module coupling:** payload physics, motion, network and compute feed
   energy through named dependencies; goals, accepted targets and actual pose are
   separate. Eight categories organize implementation, not completeness.
7. **Behavior and command composition:** a reusable behavior has required
   capabilities, resources, modules and constraints. A command can request a
   sequence or parallel composition of behaviors; its receipts remain distinct
   from occurrence/effect evidence.
8. **Complete typed graph:** every concept participates through typed definition/
   class nodes, instances and edges. The state–predicate spine is a review path,
   not the full graph. Values, units and time can remain typed properties.

## Confirmed source-to-design gaps

These findings concern inspected code and integration boundaries, not whether the
original catalog contains a relevant rule.

- **Physical logistics effects:** the current logistics service refuses physical
  pickup/handoff/delivery/charge pending authoritative physical-evidence binding.
  Pad presence and declared order transitions do not close that gap.
- **Battery/energy breadth:** the current PX4 path emits only remaining fraction.
  Broader Wh/charging calculations use declared parameters; measured current,
  voltage, thermal state and coupled payload/network/compute power are not
  established by that path.
- **Compute runtime:** inspected `ResourceBudget` is deployment configuration.
  A general source for CPU/GPU load, queueing and task latency was not located in
  the bounded BENCH audit; no SimGrid connection is established.
- **P02 public evidence:** parcel samples, custody, native Atlas results,
  lifecycle generation and evidence-availability metadata are missing from the
  host's current consumed boundary. The host README labels authored evidence as
  demo-only.
- **Native end-to-end mapping:** PR10 supplies a fixture binding/authority
  prototype, not live source authentication, durable authority, physical control
  or a full current-catalog mapping. The root integration foundation likewise
  records native evaluator and event-occurrence boundaries as unexercised.
- **Full coverage evidence:** reported catalog size and offline rule examples do
  not establish source-backed, tested, real-host-connected coverage for all 35
  business workflows or their interruption/recovery branches.

Exact code evidence is in [MODULE_STATE_INVENTORY.md](MODULE_STATE_INVENTORY.md).
The original plan also explicitly anticipated device/controller field mapping,
identity/time alignment, source producers, regulatory versions, business
acceptance and calibrated disturbance/measurement inputs as remaining work
(Appendix C). The findings above are consistent with those caveats.

## Recommended next checks, not established omissions

After original source access is available, audit these across every relevant
rule and activity: uncertainty/calibration; exact role/relation identity;
valid/available time; capability versus permission; scoped agent control;
goal/task/attempt identity; normal/interrupted/recovery phases; resource claims
versus occupancy; constraint source/applicability/enforcement; causal mechanism
versus co-occurrence; truthful generation/replay; and complete coverage accounting.

Do not expand the ontology merely to give every concern another top-level layer.
Many of these are field metadata, typed relations, rule dependencies or execution
contracts. Use the existing engine and typed source boundary, and add only the
semantics a verified gap requires.
