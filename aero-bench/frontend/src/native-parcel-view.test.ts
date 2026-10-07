import { describe, expect, it } from "vitest";
import { buildRunIndex, entityKindsOf } from "./p02-entity-overlays";
import { parsePublicTrace } from "./trace";
import { publicRunEvent, publicTrace, simulationTime } from "./testing/trace-v3-fixture";

describe("native parcel public trace", () => {
  it("keeps custody, carrier release and parcel pose at the selected tick", () => {
    const events = ["awaiting_pickup", "carried", "delivered"].map((state, index) => {
      const payload = {
        order_id: "order.single", parcel_id: "parcel.single",
        carrier_entity_id: index === 1 ? "fixture.uav" : null,
        custody_holder_id: index === 1 ? "fleet.carrier:1" : index === 0 ? "facility.pickup" : "facility.dropoff",
        custody_holder_kind: index === 1 ? "carrier" : "facility",
        destination_id: "facility.dropoff", parcel_state: state,
        frame_digest: "e".repeat(64), x_m: index * 15, y_m: index === 1 ? 20 : 0.72, z_m: 450,
      };
      return publicRunEvent(index, { at: simulationTime(index + 1, (index + 1) * 1e9),
        source_kind: "provider", source: "logistics.native-business", provider_id: "logistics.native-business",
        event_type: "public.parcel-projection", interaction_type: "logistics.parcel_projection.v1",
        public_payload: Object.entries(payload).map(([name, value]) => ({ name, value,
          value_type: value === null ? "null" : typeof value === "number" ? "float" : "str" })) });
    });
    const trace = parsePublicTrace(publicTrace({ events }));
    const identity = buildRunIndex({ trace, entityKinds: entityKindsOf(trace.scenario) }).identity!;
    expect(identity.latestAt("order.single", 0)).toBeUndefined();
    expect(identity.latestAt("order.single", 2)).toMatchObject({ status: "carried", carrierEntityId: "fixture.uav",
      custodyId: "fleet.carrier:1", parcelPositionEnu: { east_m: 15, north_m: -450, up_m: 20 } });
    const liveSource = { run_id: trace.run_id, scenario: trace.scenario, events: trace.events,
      mission_events: [], verifier_public: null };
    const liveIdentity = buildRunIndex({ trace: liveSource, entityKinds: entityKindsOf(trace.scenario) }).identity!;
    expect(liveIdentity.latestAt("order.single", 2)).toEqual(identity.latestAt("order.single", 2));
    const delivered = identity.latestAt("order.single", 3)!;
    expect(delivered.status).toBe("delivered");
    expect(delivered.carrierEntityId).toBeUndefined();
    expect(delivered.custodyId).toBe("facility.dropoff");
    expect(delivered.parcelPositionEnu).toEqual({ east_m: 30, north_m: -450, up_m: 0.72 });
  });
});
