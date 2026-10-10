# First three P08 provider adapters

The current code is identified by module SHA256 in first_provider_implementation.json, not the old9ac8d844 source design. The latest existing focused tests executed35/35 successfully:20nav/logistics and15network journal. All are typed fixtures; none certifies a native run.

| Slice | Actual server code | Native execution still needed |
|---|---|---|
|nav|aero_bench/integration/p08_observation_adapters.py::BenchFlightTelemetryAdapter|Actual closed-barrier FlightTelemetryObservation plus independent expectation and exact actor/run binding; preserve AMSL and source-clock uncertainty|
|network goodput|aero_bench/integration/p08_network_journal.py::ClosedFlowJournal/Ns3ClosedFlowWindowAdapter; p08_ns3_provider.py::JournaledNs3Provider|Actual durable closed deliveries plus application-consumer acceptance/watermark and independent demanded-goodput profile|
|logistics load|p08_observation_adapters.py::LogisticsOrderLoadAdapter|Actual released-order query, exact assignee AircraftUnit and independent payload-manifest relation; no custody claim|

JournaledNs3Provider records the complete raw closed stage before calling the parent's mailbox replacement. It adds no second RuntimeHook and does not await another advance inside a hook. Per-flow identity retains run/provider/reset/generation/source/destination/work-order/policy. Transport mailbox acceptance is not application acceptance.

The local p08_types evaluator preserves the seven operators needed by these three rule definitions. It does not implement the full62-op catalog. Private definitions and raw provider data are omitted from this public report. Missing six original observable_v2 runtime files block applying native v1.0.2; this does not block the first three adapters or legacy UI.
