import { beforeEach, describe, expect, it } from "vitest";
import { TelemetryPanel, type TelemetrySource } from "./telemetry-panel";
import { coordinate, identifier, publicTrace, scenario, stateSample } from "./testing/trace-v3-fixture";
import { parsePublicTrace } from "./trace";
import { initLanguage, t } from "./i18n";

/** One declared ResolvedCoordinate with an ENU offset, for region geometry. */
function fixtureVertex(east: number, north: number, up: number): Record<string, unknown> {
  return coordinate({
    enu: { east_m: east, north_m: north, up_m: up },
    ned: { east_m: east, north_m: north, down_m: -up },
  });
}

function sourceWith(overrides: Partial<TelemetrySource> = {}): TelemetrySource {
  const entityId = identifier("uav");
  return {
    selectedEntityId: entityId,
    sample: stateSample(1, entityId) as unknown as TelemetrySource["sample"],
    sampleTick: 1,
    missionStatus: null,
    missionEvents: [],
    networkFrame: null,
    networkEvents: [],
    regions: [],
    verifier: null,
    trace: null,
    ...overrides,
  };
}

describe("TelemetryPanel", () => {
  beforeEach(() => {
    document.body.replaceChildren();
    initLanguage();
  });

  it("renders the backend-resolved frames verbatim for a declared sample", () => {
    const panel = new TelemetryPanel(document.createElement("div"));
    panel.update(sourceWith());
    const text = panel.container.textContent ?? "";
    expect(text).toContain("31.200000°");
    expect(text).toContain("121.500000°");
    expect(text).toContain("-2160000.000");
    expect(text).toContain("0.00 m");
    panel.dispose();
  });

  it("marks optional fields as undeclared instead of inventing values", () => {
    const entityId = identifier("uav");
    const sample = stateSample(1, entityId);
    delete (sample as Record<string, unknown>).battery;
    delete (sample as Record<string, unknown>).armed;
    const panel = new TelemetryPanel(document.createElement("div"));
    panel.update(sourceWith({ sample: sample as unknown as TelemetrySource["sample"] }));
    const text = panel.container.textContent ?? "";
    expect(text).toContain(t("label.undeclared"));
    expect(text).not.toContain("100.0%");
    panel.dispose();
  });

  it("states explicitly when no recorded sample exists at the tick", () => {
    const panel = new TelemetryPanel(document.createElement("div"));
    panel.update(sourceWith({ sample: null }));
    expect(panel.container.textContent).toContain(t("telemetry.noSample"));
    panel.dispose();
  });

  it("keeps recorded battery and false states while removing absent replay fields", () => {
    const trace = parsePublicTrace(publicTrace());
    const sample = structuredClone(trace.scene_states[0]!.samples[0]!);
    sample.battery = { remaining_fraction: 0.55, voltage_v: null, current_a: null, temperature_c: null, consumed_mah: null, attributes: [] };
    sample.armed = false;
    const panel = new TelemetryPanel(document.createElement("div"));
    panel.update(sourceWith({ trace, sample }));
    expect(panel.container.textContent).toContain("55.0%");
    expect(panel.container.textContent).not.toContain(t("label.undeclared"));
    expect(panel.container.querySelectorAll(".empty-row")).toHaveLength(0);
    expect(panel.container.textContent).not.toContain(t("telemetry.batteryDetail"));
    panel.dispose();
  });

  it("renders geofence regions from the declared scenario", () => {
    const trace = parsePublicTrace(
      publicTrace({
        scenario: scenario({
          regions: [
            {
              region_id: "fixture.zone",
              kind: "no_fly",
              communications_shadow_attenuation_db: null,
              anchor_east_m: 0,
              anchor_north_m: 0,
              lower_vertices: [
                fixtureVertex(0, 0, 0),
                fixtureVertex(1, 1, 0),
                fixtureVertex(2, 2, 0),
              ],
              upper_vertices: [
                fixtureVertex(0, 0, 10),
                fixtureVertex(1, 1, 10),
                fixtureVertex(2, 2, 10),
              ],
            },
          ],
        }),
      }),
    );
    const panel = new TelemetryPanel(document.createElement("div"));
    panel.update(sourceWith({ regions: trace.scenario.regions }));
    expect(panel.container.textContent).toContain("fixture.zone");
    expect(panel.container.textContent).toContain("禁飞区");
    panel.dispose();
  });
});
