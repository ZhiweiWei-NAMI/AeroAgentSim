// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { ArtifactReference, PublicTrace } from "./generated/aero-bench-contracts";
import type {
  BusinessIdentityEnvelope,
  BusinessIdentityRecord,
  RunIndexInput,
} from "./p02-entity-overlays";
import {
  UNKNOWN_LABEL_ZH,
  buildBusinessIdentityIndex,
  buildRunIndex,
  businessIdentityView,
  businessDestinationMarkers,
  businessParcelAnchor,
  compactCargoLabel,
  entityCargoView,
  selectedFrameBusinessView,
  entityKindsOf,
  formatRunClock,
  p02OrderSelection,
  p02OverviewPose,
  stageChainBadges,
  stageLabel,
} from "./p02-entity-overlays";

function reference(artifactId: string, selector: string): ArtifactReference {
  return { artifact_id: artifactId, digest: `digest-${artifactId}`, selector, visibility: "public" };
}

interface MinimalRunShape {
  missionEvents: {
    event_id: string;
    state: string;
    entity_id: string | null;
    provider_id: string;
    sequence: number;
    evidence: ArtifactReference[];
    at: { tick: number; sim_time_ns: number };
  }[];
  requirements?: { requirement_id: string; kind: string; dependencies: string[]; target_id: string | null; launch_site_id: string | null }[];
  targets?: { target_id: string; parent_entity_id: string | null; east: number; north: number; up: number }[];
  verifier?: PublicTrace["verifier_public"];
}

/** Narrow a minimal literal into the PublicTrace fields the index reads. */
function traceInput(run: MinimalRunShape): RunIndexInput["trace"] {
  return {
    mission_events: run.missionEvents,
    scenario: {
      semantic_targets: (run.targets ?? []).map(target => ({
        target_id: target.target_id,
        parent_entity_id: target.parent_entity_id,
        pose: { position: { enu: { east_m: target.east, north_m: target.north, up_m: target.up } } },
      })) as RunIndexInput["trace"]["scenario"]["semantic_targets"],
      launch_sites: [],
      mission_requirements: (run.requirements ?? []).map(requirement => ({
        requirement_id: requirement.requirement_id,
        kind: requirement.kind,
        dependencies: requirement.dependencies,
        target_id: requirement.target_id,
        launch_site_id: requirement.launch_site_id,
        expected_public_asset_id: null,
      })) as RunIndexInput["trace"]["scenario"]["mission_requirements"],
      entities: [
        { entity_id: "uav.inspector", kind: "uav" },
        { entity_id: "vehicle.001", kind: "ugv" },
      ],
    } as unknown as PublicTrace["scenario"],
    verifier_public: run.verifier ?? null,
  } as unknown as PublicTrace;
}

function index(run: MinimalRunShape, kinds?: Record<string, string>) {
  return buildRunIndex({
    trace: traceInput(run),
    entityKinds: new Map(Object.entries(kinds ?? { "uav.inspector": "uav", "vehicle.001": "ugv" })),
  });
}

const INSPECTION_RUN: MinimalRunShape = {
  missionEvents: [
    {
      event_id: "event.158026", state: "delivered", entity_id: null, provider_id: "network",
      sequence: 158026, evidence: [reference("artifact.delivery", "0")],
      at: { tick: 290, sim_time_ns: 145_000_000_000 },
    },
    {
      event_id: "event.158027", state: "delivered", entity_id: null, provider_id: "network",
      sequence: 158027, evidence: [reference("artifact.delivery", "1")],
      at: { tick: 291, sim_time_ns: 145_500_000_000 },
    },
    {
      event_id: "event.0001", state: "dropped", entity_id: "vehicle.001", provider_id: "business",
      sequence: 10, evidence: [reference("artifact.drop", "x")],
      at: { tick: 100, sim_time_ns: 50_000_000_000 },
    },
  ],
  requirements: [
    { requirement_id: "mission.land", kind: "land", dependencies: ["mission.return"], target_id: null, launch_site_id: "launch.a" },
    { requirement_id: "mission.return", kind: "return_to_launch", dependencies: ["mission.upload"], target_id: null, launch_site_id: "launch.a" },
    { requirement_id: "mission.upload", kind: "upload_or_buffer", dependencies: ["mission.observe-2"], target_id: null, launch_site_id: null },
    { requirement_id: "mission.observe-2", kind: "observation", dependencies: ["mission.observe-1"], target_id: "target.lower", launch_site_id: null },
    { requirement_id: "mission.observe-1", kind: "observation", dependencies: ["mission.takeoff"], target_id: "target.lower", launch_site_id: null },
    { requirement_id: "mission.takeoff", kind: "takeoff", dependencies: [], target_id: null, launch_site_id: "launch.a" },
  ],
  targets: [
    { target_id: "target.lower", parent_entity_id: "static.building.a", east: -500, north: -437, up: 4 },
  ],
  verifier: {
    schema_version: "aero-bench.verification-public/v3",
    run_id: "run-1",
    coverage_complete: true,
    status: "passed",
    goals: [
      {
        goal_id: "goal.report_outcome", passed: true, failure_class: null,
        metrics: [{
          metric_id: "inspection.formal.report_outcome", unit: "boolean", value: 1,
          evidence: [reference("artifact.delivery", "deliveries"), reference("artifact.report", "reports")],
        }],
      },
      {
        goal_id: "goal.landing", passed: true, failure_class: null,
        metrics: [{
          metric_id: "inspection.formal.landing", unit: "boolean", value: 1,
          evidence: [reference("artifact.scene-states", "ticks/279/entities/uav.inspector")],
        }],
      },
    ],
  },
};

describe("buildRunIndex", () => {
  it("keeps unbound mission events visible under the explicit UNKNOWN bucket", () => {
    const run = index(INSPECTION_RUN);
    // All mission events are business-unbound: entity_id alone never binds.
    const unbound = run.unboundEventsUpTo(300);
    expect(unbound).toHaveLength(3);
    expect(unbound[0]).toMatchObject({ state: "delivered", tick: 290, providerId: "network" });
    expect(run.unboundEventsUpTo(289)).toHaveLength(1);
  });

  it("keeps events whose entity_id alone is not an order binding in the UNKNOWN bucket", () => {
    // The public contract's event.entity_id names a scene entity; it does not
    // carry order_id/parcel_id/carrier identity. Without an explicit business
    // record, event entity ids must never be promoted to order ids.
    const run = index({
      missionEvents: [
        {
          event_id: "event.1", state: "delivered", entity_id: "uav.inspector", provider_id: "network",
          sequence: 1, evidence: [reference("artifact.delivery", "0")],
          at: { tick: 290, sim_time_ns: 145_000_000_000 },
        },
      ],
    });
    expect(run.orders()).toEqual([]);
    expect(run.unboundEventsUpTo(300)).toHaveLength(1);
  });

  it("does not treat an event entity_id as an order id (legacy event binding)", () => {
    const run = index(INSPECTION_RUN);
    const orders = run.orders();
    expect(orders).toEqual([]);
    expect(run.stagesUpTo("vehicle.001", 300)).toEqual([]);
    expect(run.latestStageAt("vehicle.001", 100)).toBeUndefined();
  });

  it("topologically orders the declared requirement chain", () => {
    expect(index(INSPECTION_RUN).stageChain()).toEqual([
      "mission.takeoff", "mission.observe-1", "mission.observe-2",
      "mission.upload", "mission.return", "mission.land",
    ]);
  });

  it("returns an empty chain and no orders when the trace declares none", () => {
    const run = index({ missionEvents: [] });
    expect(run.stageChain()).toEqual([]);
    expect(run.orders()).toEqual([]);
    expect(run.checkpoints()).toEqual([]);
  });

  it("joins verifier goals by artifact identity, not by guesswork", () => {
    const run = index(INSPECTION_RUN);
    // The delivery artifact matches goal.report_outcome through artifact_id.
    const synthetic = {
      orderId: "o", sourceLabel: "o", stageIds: [], stageFlips: new Map(),
      evidence: [reference("artifact.delivery", "0")],
    };
    expect(run.verifierGoalsFor(synthetic).map(goal => goal.goalId)).toEqual(["goal.report_outcome"]);
    const sceneStateOrder = {
      orderId: "s", sourceLabel: "s", stageIds: [], stageFlips: new Map(),
      evidence: [reference("artifact.scene-states", "ticks/279/entities/uav.inspector")],
    };
    expect(run.verifierGoalsFor(sceneStateOrder).map(goal => goal.goalId)).toEqual(["goal.landing"]);
  });

  it("reads checkpoints only from declared poses", () => {
    const run = index(INSPECTION_RUN);
    const checkpoints = run.checkpoints();
    expect(checkpoints).toHaveLength(1);
    expect(checkpoints[0]).toMatchObject({
      id: "target.lower", kind: "target", eastM: -500, northM: -437, upM: 4,
      parentEntityId: "static.building.a",
    });
  });
});

describe("entityCargoView", () => {
  it("reports orders bound to the entity and unbound events separately", () => {
    const run = index(INSPECTION_RUN);
    const view = entityCargoView(run, traceInput(INSPECTION_RUN), "vehicle.001", 300, {
      mode: "active",
      attributes: [{ name: "speed_mps", value: 3.2 }],
    } as never);
    // vehicle.001 appears in a mission event, but no business record binds
    // it: the cargo panel stays UNKNOWN for orders.
    expect(view.orders).toEqual([]);
    expect(view.unboundEvents.length).toBe(3);
    expect(view.phase).toBe("active");
    expect(view.attributes).toEqual([{ name: "speed_mps", value: 3.2 }]);
  });

  it("keeps an entity with no declared business records fully UNKNOWN", () => {
    const run = index(INSPECTION_RUN);
    const view = entityCargoView(run, traceInput(INSPECTION_RUN), "uav.inspector", 300);
    expect(view.orders).toEqual([]);
    expect(view.phase).toBeUndefined();
    expect(view.attributes).toEqual([]);
  });
});

/** One authored business record in the interface.json record shape. */
function record(overrides: Partial<Omit<BusinessIdentityRecord, "order_id">> & Pick<BusinessIdentityRecord, "order_id">): BusinessIdentityRecord {
  return {
    record_id: `rec.${overrides.order_id}`,
    at: { tick: 120, sim_time_ns: 60_000_000_000 },
    parcel_id: "parcel.001",
    carrier_entity_id: "uav.inspector",
    custody_holder: { kind: "entity", id: "uav.inspector" },
    destination_id: "target.lower",
    attempt: 1,
    status: "in_delivery",
    source_ref: "business/demo.json",
    availability_reason: null,
    ...overrides,
  };
}

function identityEnvelope(records: BusinessIdentityRecord[], runId = "run-1"): BusinessIdentityEnvelope {
  return {
    schema_version: "p02.business-identities/v1",
    scene_run_id: runId,
    source_kind: "authored_business_fixture",
    source_ref: "p02-business-identities/demo.json",
    records,
  };
}

describe("buildBusinessIdentityIndex", () => {
  it("binds orders only for the exact loaded run id", () => {
    const accepted = buildBusinessIdentityIndex(
      identityEnvelope([record({ order_id: "order.001" })]), "run-1");
    expect(accepted?.orderIds()).toEqual(["order.001"]);
    expect(accepted?.source.source_kind).toBe("authored_business_fixture");
    // A sidecar produced for another run binds nothing.
    expect(buildBusinessIdentityIndex(identityEnvelope([record({ order_id: "o" })]), "other-run")).toBeNull();
  });

  it("rejects a foreign-run envelope as a whole instead of importing its records", () => {
    // The envelope's scene_run_id (default "run-1") is compared against the
    // loaded trace run id ("other-run"): the whole envelope binds nothing.
    const rejected = buildBusinessIdentityIndex(identityEnvelope([
      record({ order_id: "order.001" }),
      record({ order_id: "order.002" }),
    ]), "other-run");
    expect(rejected).toBeNull();
  });

  it("reads custody, carrier and destination strictly at or before the cursor", () => {
    const identity = buildBusinessIdentityIndex(identityEnvelope([
      record({ order_id: "order.001", at: { tick: 10, sim_time_ns: 5_000_000_000 }, status: "created" }),
      record({
        order_id: "order.001", at: { tick: 20, sim_time_ns: 10_000_000_000 },
        status: "in_delivery", custody_holder: { kind: "facility", id: "hub.east" },
        carrier_entity_id: "vehicle.001", availability_reason: null,
      }),
      record({
        order_id: "order.001", at: { tick: 30, sim_time_ns: 15_000_000_000 },
        status: "delivered", custody_holder: { kind: "facility", id: "target.lower" },
        carrier_entity_id: "", availability_reason: null,
      }),
    ]), "run-1")!;
    expect(identity.latestAt("order.001", 15)).toMatchObject({
      status: "created", carrierEntityId: "uav.inspector", custodyKind: "entity", custodyId: "uav.inspector",
    });
    expect(identity.latestAt("order.001", 20)).toMatchObject({
      status: "in_delivery", carrierEntityId: "vehicle.001", custodyKind: "facility", custodyId: "hub.east",
    });
    // toMatchObject treats absent and undefined identically; assert the
    // carrier absence exactly.
    const delivered = identity.latestAt("order.001", 999)!;
    expect(delivered.status).toBe("delivered");
    expect("carrierEntityId" in delivered).toBe(false);
    expect(identity.latestAt("order.001", 9)).toBeUndefined();
    expect(identity.latestAt("order.unknown", 999)).toBeUndefined();
  });

  it("keeps genuinely missing facts UNKNOWN instead of synthesizing values", () => {
    const identity = buildBusinessIdentityIndex(identityEnvelope([
      record({
        order_id: "order.002", parcel_id: "", carrier_entity_id: null, custody_holder: { kind: "unknown", id: null },
        destination_id: null, attempt: null, status: "",
      }),
    ]), "run-1")!;
    const view = identity.latestAt("order.002", 120)!;
    expect(view.parcelId).toBeUndefined();
    expect(view.carrierEntityId).toBeUndefined();
    expect(view.custodyKind).toBeUndefined();
    expect(view.custodyId).toBeUndefined();
    expect(view.destinationId).toBeUndefined();
    expect(view.attempt).toBeUndefined();
    expect(view.status).toBeUndefined();
    expect(identity.latestAt("order.003", 120)).toBeUndefined();
  });
});

describe("businessIdentityView", () => {
  it("joins the selected entity to the record that explicitly names it as carrier", () => {
    const identity = buildBusinessIdentityIndex(identityEnvelope([
      record({ order_id: "order.001" }),
      record({ order_id: "order.002", carrier_entity_id: "vehicle.001", custody_holder: { kind: "entity", id: "vehicle.001" } }),
    ]), "run-1")!;
    const view = businessIdentityView(identity, "uav.inspector", 120);
    expect(view.orders.map(order => order.orderId)).toEqual(["order.001"]);
    expect(view.carrierFor).toEqual(["order.001"]);
    const other = businessIdentityView(identity, "vehicle.001", 120);
    expect(other.orders.map(order => order.orderId)).toEqual(["order.002"]);
    const none = businessIdentityView(identity, "pedestrian.01", 120);
    expect(none.orders).toEqual([]);
    expect(none.carrierFor).toEqual([]);
  });

  it("never joins by trace-entity-id equality alone (no record means no order)", () => {
    const identity = buildBusinessIdentityIndex(identityEnvelope([record({ order_id: "order.001" })]), "run-1")!;
    // vehicle.001 appears in trace mission events but in no business record.
    const view = businessIdentityView(identity, "vehicle.001", 300);
    expect(view.orders).toEqual([]);
  });
});

describe("labels", () => {
  it("formats clocks and keeps UNKNOWN explicit", () => {
    expect(formatRunClock(0)).toBe("00:00.0");
    expect(formatRunClock(145)).toBe("02:25.0");
    expect(formatRunClock(undefined)).toBe(UNKNOWN_LABEL_ZH);
    expect(stageLabel(undefined)).toBe(UNKNOWN_LABEL_ZH);
    expect(stageLabel("delivered")).toBe("delivered");
  });

  it("extends stage badges with encountered states without duplicates", () => {
    expect(stageChainBadges(["mission.takeoff", "mission.land"], ["mission.takeoff", "delivered", undefined])).toEqual([
      "mission.takeoff", "mission.land", "delivered",
    ]);
  });

  it("maps scenario entity kinds once per trace", () => {
    const kinds = entityKindsOf(traceInput(INSPECTION_RUN).scenario);
    expect(kinds.get("uav.inspector")).toBe("uav");
    expect(kinds.get("vehicle.001")).toBe("ugv");
  });
});

describe("p02OrderSelection", () => {
  it("selects only the record's declared carrier entity, never a substitute id", () => {
    expect(p02OrderSelection({ orderId: "order.001", carrierEntityId: "uav.inspector" }))
      .toEqual({ kind: "entity", id: "uav.inspector" });
    // No carrier in the record: no selection — a dynamic entity id must
    // never be promoted into the business selector.
    expect(p02OrderSelection({ orderId: "order.001" })).toBeNull();
    expect(p02OrderSelection({ orderId: "order.001", carrierEntityId: undefined })).toBeNull();
  });
});

describe("p02OverviewPose", () => {
  it("fits an oblique pose around the declared ENU points without offsets", () => {
    const points = [
      { east: -450, north: -450, up: 0.1 },
      { east: -510, north: -437, up: 20.1 },
    ];
    const pose = p02OverviewPose(points);
    expect(pose).not.toBeNull();
    // Target is the exact centre of the supplied points.
    expect(pose!.target.east).toBeCloseTo(-480, 5);
    expect(pose!.target.north).toBeCloseTo(-443.5, 5);
    // The camera sits obliquely south-west of and above the target.
    expect(pose!.position.east).toBeLessThan(pose!.target.east);
    expect(pose!.position.north).toBeLessThan(pose!.target.north);
    expect(pose!.position.up).toBeGreaterThan(pose!.target.up);
  });

  it("returns null when no finite point exists and never throws on garbage", () => {
    expect(p02OverviewPose([])).toBeNull();
    expect(p02OverviewPose([{ east: Number.NaN, north: 0, up: 0 }])).toBeNull();
    const pose = p02OverviewPose([
      { east: Number.NaN, north: Number.NaN, up: Number.NaN },
      { east: 10, north: 20, up: 5 },
    ]);
    expect(pose).not.toBeNull();
    expect(pose!.target.east).toBe(10);
    expect(pose!.target.north).toBe(20);
  });

  it("enforces the minimum span so a point-dense cluster stays viewable", () => {
    const pose = p02OverviewPose([{ east: 0, north: 0, up: 2 }]);
    expect(pose).not.toBeNull();
    const lateral = Math.hypot(
      pose!.position.east - pose!.target.east,
      pose!.position.north - pose!.target.north,
    );
    expect(lateral).toBeGreaterThanOrEqual(260 * 0.7);
  });
});


describe("selected frame business identity", () => {
  it("keeps exact identity and source at the cursor and discards future records on seek", () => {
    const trace = { ...traceInput({ missionEvents: [] }), run_id: "run-1" } as PublicTrace;
    const compiled = buildRunIndex({ trace, entityKinds: entityKindsOf(trace.scenario), identity: identityEnvelope([
      record({ order_id: "order.exact", at: { tick: 10, sim_time_ns: 10_000_000_000 } }),
      record({ order_id: "order.exact", at: { tick: 20, sim_time_ns: 20_000_000_000 },
        parcel_id: "parcel.future", carrier_entity_id: "vehicle.001",
        custody_holder: { kind: "facility", id: "facility.exact" } }),
    ]) });
    const at20 = selectedFrameBusinessView(compiled, trace, "vehicle.001", 20);
    expect(at20.orders[0]).toMatchObject({ parcelId: "parcel.future", custodyId: "facility.exact" });
    const at10 = selectedFrameBusinessView(compiled, trace, "uav.inspector", 10);
    expect(at10.sourceKind).toBe("authored_business_fixture");
    expect(at10.orders[0]).toMatchObject({ orderId: "order.exact", parcelId: "parcel.001", custodyId: "uav.inspector" });
    expect(selectedFrameBusinessView(compiled, trace, "vehicle.001", 10).orders).toEqual([]);
    expect(selectedFrameBusinessView(compiled, trace, "uav.inspector", 9).orders).toEqual([]);
  });
});

it("uses compact stable map copy while preserving full identity in the source record", () => {
  const id = "parcel.logistics.fixture.order-000017";
  expect(compactCargoLabel(id)).toBe("parc…-000017");
  expect(compactCargoLabel(id)).toHaveLength(12);
  expect(compactCargoLabel("parcel.001")).toBe("parcel.001");
});

it("positions only exact authored destination entities from the same frame or static config", () => {
  const coordinate = { enu: { east_m: 12, north_m: 18, up_m: 0 },
    wgs84: { latitude_deg: 31.2, longitude_deg: 121.5 } };
  const business = { cursorTick: 10, orders: [{ destinationId: "facility.exact" }] } as unknown as Parameters<typeof businessDestinationMarkers>[0];
  const scenario = { entities: [{ entity_id: "facility.exact", state: "dynamic", initial_pose: { position: coordinate } }] } as unknown as Parameters<typeof businessDestinationMarkers>[1];
  const frame = { at: { tick: 10 }, samples: [{ entity_id: "facility.exact", at: { tick: 10 }, pose: { position: coordinate } }] } as unknown as Parameters<typeof businessDestinationMarkers>[2];
  expect(businessDestinationMarkers(business, scenario, frame)).toEqual([{ destinationId: "facility.exact", position: coordinate }]);
  expect(businessDestinationMarkers(business, scenario, { ...frame!, at: { ...frame!.at, tick: 11 } })).toEqual([]);
  expect(businessDestinationMarkers(business, scenario, { ...frame!, samples: [{ ...frame!.samples[0], entity_id: "other.entity" }] })).toEqual([]);
  expect(businessDestinationMarkers(business, { ...scenario!, entities: [{ ...scenario!.entities[0]!, entity_id: "facility.nearby" }] }, frame)).toEqual([]);
  expect(businessDestinationMarkers(business, { ...scenario!, entities: [{ ...scenario!.entities[0]!, state: "static" }] }, { ...frame!, samples: [{ ...frame!.samples[0], entity_id: "other.entity" }] })).toEqual([{ destinationId: "facility.exact", position: coordinate }]);
});

it("places parcels at exact current custody and never at a former carrier when facility custody is unresolved", () => {
  const coordinate = { enu: { east_m: 12, north_m: 18, up_m: 0 }, wgs84: { latitude_deg: 31.2, longitude_deg: 121.5 } };
  const business = { cursorTick: 10, orders: [{ parcelId: "parcel.exact", carrierEntityId: "uav.inspector",
    custodyKind: "facility", custodyId: "facility.exact", status: "delivered" }] } as unknown as Parameters<typeof businessParcelAnchor>[1];
  const scenario = { entities: [{ entity_id: "uav.inspector", state: "dynamic" },
    { entity_id: "facility.exact", state: "static", initial_pose: { position: coordinate } }] } as unknown as Parameters<typeof businessParcelAnchor>[2];
  const frame = { at: { tick: 10 }, samples: [{ entity_id: "uav.inspector", at: { tick: 10 }, pose: { position: coordinate } }] } as unknown as Parameters<typeof businessParcelAnchor>[3];
  expect(businessParcelAnchor("parcel.exact", business, scenario, frame)).toEqual({ holderId: "facility.exact", position: coordinate });
  expect(businessParcelAnchor("parcel.exact", business, { ...scenario!, entities: [scenario!.entities[0]!] }, frame)).toBeNull();
  expect(businessParcelAnchor("parcel.other", business, scenario, frame)).toBeNull();
  expect(businessParcelAnchor("parcel.exact", business, scenario, { ...frame!, at: { ...frame!.at, tick: 11 } })).toBeNull();
});
