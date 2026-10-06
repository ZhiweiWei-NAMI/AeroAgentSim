# Current210 source/state and P01 input facts

Current source/config/schema and bounded emitted examples. NOT whole210 observed coverage or hardware calibration.

The released legacy supplements use compute/communication1.6.0 and domain observations2.2.0. These are controlled, largely heuristic models. Native ns-3 named runs and persistent job queues are separate versioned improvements; no all210 replacement is claimed.

| P01 family | Executable predicates | Representative exact fields with units |
|---|---:|---|
|airspace_operations|12|`state.restricted_region_active` (bool); `geometry.minimum_restricted_boundary_distance_m` (m); `geometry.inside_protected_airspace` (bool); `plan.cooperative_flag` (bool)|
|ground_mobility|6|`truth.lane_id` (identifier); `truth.speed_mps` (m/s); `domain.signal_queue.queue_vehicle_count` (vehicle); `domain.road_closure.road_closed` (bool)|
|mobility_regulation|5|`truth.controlling_signal_id` (identifier); `truth.crossed_stop_line` (bool); `truth.controlling_signal_state` (state-code); `truth.speed_mps` (m/s)|
|agent_interaction_safety|6|`derived.nearest_aircraft_distance_m` (m); `derived.nearest_building_distance_m` (m); `derived.nearest_ground_vehicle_distance_m` (m); `derived.nearest_pedestrian_distance_m` (m)|
|facility_energy_service|5|`domain.payload_energy.state_of_charge_ratio` (ratio); `domain.pad_facility.availability` (state-code); `domain.pad_facility.requester_count` (aircraft); `domain.pad_facility.capacity` (aircraft)|
|communication_process|9|`bandwidth.dropped_mbps` (Mbps); `requirement.status` (state-code); `handover.active` (bool); `link_quality.latency_ms` (ms)|
|computation_process|7|`failure.requirement_status` (state-code); `node_availability.available` (nonmeasurement); `queue_depth` (count); `capacity.cpu_cores` (cores)|
|positioning_navigation|4|`domain.gnss_navigation.quality_level` (state-code); `domain.gnss_navigation.gnss_spoofed` (bool); `domain.gnss_navigation.position_error_m` (m); `navigation.path_deviation` (bool)|
|environment_hazard|6|`weather.rain` (ratio); `weather.fog_density` (ratio); `weather.visibility_m` (m); `weather.wind_speed` (m/s)|
|digital_security|2|`domain.security_command.auth_state` (state-code); `domain.security_command.jamming_indicator` (bool)|
|operational_constraint_emergency|5|`state.restricted_region_active` (bool); `scene.emergency_isolation_active` (bool); `plan.rth_mode_active` (bool); `control.mission_abort_active` (bool)|
|utm|5|`utm.authorization.status` (state-code); `utm.flight_plan.approval_status` (state-code); `utm.operational_intent.conflict_active` (bool); `utm.deconfliction.resolution_status` (state-code)|

The CSV contains the exact resolved P01 contract operands/types/owner roles, not a claim of observed coverage for all210 or all raw metadata. Four unresolved immediate dust receiver projections remain explicitly listed in JSON; no output truth is inserted as a feature.

Current Qwen dataset fit consumes only latency_mean_ms (ms) and packet_loss_ratio (1), plus ownership/time/masks and fixed-rule query structure. SNR/SINR/RSSI are not logged or admitted as forecast states by this ns-3 provider. Physics affects the two admitted inputs through packet reception, retransmission and queues.

Actual R1: ns-3.48, shared802.11n ad hoc,2412MHz/channel1,20MHz,HtMcs0 nominal6.5Mbps,16dBm Tx,0dB gains,7dB NF,-95dBm RxSensitivity,1m40.095329dB log-distance reference,n=3; no building/shadow/fast fading in the reviewed run. Derived20MHz receiver noise is-93.989700dBm. This is a source-backed simulation prior, not empirical city calibration.

P uses mature socket-accepted TTL cohorts, not all attempted transmissions or PHY drops. L is conditional on timely mature delivery; absent successful latency remains UNKNOWN. Gateway expected-sequence loss has a separate observable definition and causal authority.

Evidence: adjacent JSON/CSV; current run_config/summary paths and source pointers are recorded there. No new simulation, model call or whole-bank scan was needed.
