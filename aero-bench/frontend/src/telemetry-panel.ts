import type {
  PublicMissionEvent,
  PublicMissionStatus,
  PublicNetworkFrame,
  PublicRunEvent,
  PublicVerificationReport,
  PublicTrace,
  ResolvedRegion,
  StateSample,
} from "./generated/aero-bench-contracts";
import { currentLanguage, t, type I18nKey } from "./i18n";
import {
  displayDegrees,
  displayMeters,
  displayRatio,
  displaySpeed,
  displayValue,
  formatSimTime,
  sampleInstruments,
  shortDigest,
  statusTone,
  type InstrumentKey,
} from "./view-model";
import { append, bar, element, emptyRow, keyValue, sectionTitle, statusValue } from "./ui";
import { showAvailableContent } from "./available-content";

const INSTRUMENT_LABEL_KEYS: Readonly<Record<InstrumentKey, I18nKey>> = {
  armed: "instrument.armed",
  battery: "instrument.battery",
  mode: "instrument.mode",
  health: "instrument.health",
  contacts: "instrument.contacts",
};

const REGION_KIND_KEYS: Readonly<Record<ResolvedRegion["kind"], I18nKey>> = {
  geofence: "regionKind.geofence",
  no_fly: "regionKind.no_fly",
  communications_shadow: "regionKind.communications_shadow",
};

const STAGE_KEYS: Readonly<Record<StateSample["stage"], I18nKey>> = {
  motion: "stage.motion",
  network: "stage.network",
  business_environment: "stage.business_environment",
};

export interface TelemetrySource {
  readonly selectedEntityId: string | null;
  /** The exact recorded sample of the selected entity at the cursor tick. */
  readonly sample: StateSample | null;
  readonly sampleTick: number | null;
  readonly missionStatus: PublicMissionStatus | null;
  readonly missionEvents: readonly PublicMissionEvent[];
  readonly networkFrame: PublicNetworkFrame | null;
  /** Authorized public network-link event projections (live stream or sealed trace). */
  readonly networkEvents: readonly PublicRunEvent[];
  readonly regions: readonly ResolvedRegion[];
  readonly verifier: PublicVerificationReport | null;
  /** Sealed replay trace, when the console is in replay mode. */
  readonly trace: PublicTrace | null;
}

const UNDECLARED = (): string => t("label.undeclared", currentLanguage());

/**
 * Exact-tick telemetry panel. Every value is read from the declared
 * StateSample / scenario / trace fields at the recorded tick; a field
 * the contract does not carry renders as an explicit undeclared state.
 * No frame conversion and no interpolation happens anywhere in this
 * panel — WGS84/ENU/NED/ECEF/AGL/AMSL values are the backend-declared
 * numbers verbatim.
 */
export class TelemetryPanel {
  readonly container: HTMLElement;
  private source: TelemetrySource | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.container.classList.add("telemetry-panel");
  }

  update(source: TelemetrySource): void {
    this.source = source;
    this.render();
  }

  refresh(): void {
    this.render();
  }

  private render(): void {
    const lang = currentLanguage();
    const source = this.source;
    this.container.replaceChildren();
    this.container.append(sectionTitle(t("telemetry.title", lang)));
    if (source === null) {
      this.container.append(emptyRow(t("telemetry.noSelection", lang)));
      return;
    }
    if (source.sample === null) {
      this.container.append(emptyRow(t("telemetry.noSample", lang)));
    } else {
      this.renderPose();
      this.renderVelocities();
      this.renderSystems();
      this.renderAttributes();
    }
    this.renderMission();
    this.renderGeofence();
    this.renderNetwork();
    this.renderEvidence();
    if (source.trace !== null) showAvailableContent(this.container);
  }

  private renderPose(): void {
    const lang = currentLanguage();
    const sample = this.source!.sample!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("telemetry.pose", lang)));
    const position = sample.pose.position;
    panel.append(
      keyValue(t("telemetry.wgs84Lat", lang), displayDegrees(position.wgs84.latitude_deg)),
      keyValue(t("telemetry.wgs84Lon", lang), displayDegrees(position.wgs84.longitude_deg)),
      keyValue(t("telemetry.ellipsoidHeight", lang), displayMeters(position.wgs84.ellipsoid_height_m)),
      keyValue(
        t("telemetry.enu", lang),
        `${displayMeters(position.enu.east_m)} / ${displayMeters(position.enu.north_m)} / ${displayMeters(position.enu.up_m)}`,
      ),
      keyValue(
        t("telemetry.ned", lang),
        `${displayMeters(position.ned.east_m)} / ${displayMeters(position.ned.north_m)} / ${displayMeters(position.ned.down_m)}`,
      ),
      keyValue(
        t("telemetry.ecef", lang),
        `${position.ecef.x_m.toFixed(3)} / ${position.ecef.y_m.toFixed(3)} / ${position.ecef.z_m.toFixed(3)} m`,
      ),
      keyValue(t("telemetry.agl", lang), displayMeters(position.agl_m)),
      keyValue(t("telemetry.amsl", lang), displayMeters(position.amsl_m)),
      keyValue(t("telemetry.terrain", lang), displayMeters(position.terrain_amsl_m)),
      keyValue(t("telemetry.geoid", lang), displayMeters(position.geoid_separation_m)),
      keyValue(
        t("telemetry.orientationEnu", lang),
        `${sample.pose.orientation_enu.qw.toFixed(4)} ${sample.pose.orientation_enu.qx.toFixed(4)} ${sample.pose.orientation_enu.qy.toFixed(4)} ${sample.pose.orientation_enu.qz.toFixed(4)}`,
      ),
      keyValue(
        t("telemetry.orientationNed", lang),
        `${sample.pose.orientation_ned.qw.toFixed(4)} ${sample.pose.orientation_ned.qx.toFixed(4)} ${sample.pose.orientation_ned.qy.toFixed(4)} ${sample.pose.orientation_ned.qz.toFixed(4)}`,
      ),
    );
    this.container.append(panel);
  }

  private renderVelocities(): void {
    const lang = currentLanguage();
    const sample = this.source!.sample!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("telemetry.velocity", lang)));
    const enu = sample.linear_velocity_enu;
    const ned = sample.linear_velocity_ned;
    panel.append(
      keyValue(
        t("telemetry.velocityEnu", lang),
        `${displaySpeed(enu.east_mps)} / ${displaySpeed(enu.north_mps)} / ${displaySpeed(enu.up_mps)}`,
      ),
      keyValue(
        t("telemetry.velocityNed", lang),
        `${displaySpeed(ned.east_mps)} / ${displaySpeed(ned.north_mps)} / ${displaySpeed(ned.down_mps)}`,
      ),
    );
    const angular = sample.angular_velocity_body;
    if (angular === undefined || angular === null) {
      panel.append(keyValue(t("telemetry.angularBody", lang), UNDECLARED()));
    } else {
      panel.append(
        keyValue(
          t("telemetry.angularBody", lang),
          `${angular.x_radps.toFixed(4)} / ${angular.y_radps.toFixed(4)} / ${angular.z_radps.toFixed(4)} rad/s`,
        ),
      );
    }
    this.container.append(panel);
  }

  private renderSystems(): void {
    const lang = currentLanguage();
    const sample = this.source!.sample!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("telemetry.systems", lang)));
    for (const instrument of sampleInstruments(sample)) {
      if (!instrument.declared) {
        panel.append(keyValue(t(INSTRUMENT_LABEL_KEYS[instrument.key], lang), UNDECLARED()));
        continue;
      }
      const value = instrument.value;
      const text =
        typeof value === "number"
          ? instrument.key === "battery"
            ? `${(value * 100).toFixed(1)}%`
            : value.toFixed(2)
          : displayValue(value);
      const tone = instrument.key === "health" || instrument.key === "armed" ? statusTone(String(value)) : "muted";
      panel.append(statusValue(t(INSTRUMENT_LABEL_KEYS[instrument.key], lang), text, tone));
      if (instrument.key === "battery" && typeof value === "number") {
        panel.append(bar(value));
      }
    }
    const battery = sample.battery;
    if (battery !== undefined && battery !== null) {
      panel.append(
        keyValue(
          t("telemetry.batteryDetail", lang),
          [
            formatOptional(battery.voltage_v, (value) => `${value.toFixed(2)} V`),
            formatOptional(battery.current_a, (value) => `${value.toFixed(2)} A`),
            formatOptional(battery.temperature_c, (value) => `${value.toFixed(1)} °C`),
            formatOptional(battery.consumed_mah, (value) => `${value.toFixed(0)} mAh`),
          ].filter(value => this.source?.trace == null || value !== UNDECLARED()).join(" · "),
        ),
      );
    }
    this.container.append(panel);
  }

  private renderAttributes(): void {
    const lang = currentLanguage();
    const sample = this.source!.sample!;
    if (sample.attributes === undefined || sample.attributes.length === 0) {
      return;
    }
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("label.properties", lang)));
    for (const attribute of sample.attributes) {
      panel.append(keyValue(attribute.name, displayValue(attribute.value)));
    }
    this.container.append(panel);
  }

  private renderMission(): void {
    const lang = currentLanguage();
    const source = this.source!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("panel.taskStatus", lang)));
    const status = source.missionStatus;
    if (status === null) {
      panel.append(emptyRow(t("boundary.status.title", lang)));
    } else {
      panel.append(
        statusValue(
          t("label.businessState", lang),
          status.business_state ?? UNDECLARED(),
          statusTone(status.business_state ?? ""),
        ),
        statusValue(
          t("label.observationState", lang),
          status.observation_state ?? UNDECLARED(),
          statusTone(status.observation_state ?? ""),
        ),
        statusValue(
          t("label.networkState", lang),
          status.network_state ?? UNDECLARED(),
          statusTone(status.network_state ?? ""),
        ),
      );
      if (status.task_progress_ratio !== null) {
        const progressRow = element("div", "progress-row");
        append(
          progressRow,
          element("span", undefined, t("label.taskProgress", lang)),
          bar(status.task_progress_ratio),
          element("strong", undefined, displayRatio(status.task_progress_ratio)),
        );
        panel.append(progressRow);
      }
    }
    if (source.missionEvents.length > 0) {
      const recent = source.missionEvents.slice(-8);
      for (const event of recent) {
        panel.append(
          keyValue(
            `${t("clock.tick", lang)} ${event.at.tick}`,
            `${event.state}${event.entity_id === null ? "" : ` · ${event.entity_id}`}`,
          ),
        );
      }
    }
    this.container.append(panel);
  }

  private renderGeofence(): void {
    const lang = currentLanguage();
    const source = this.source!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("telemetry.geofence", lang)));
    if (source.regions.length === 0) {
      panel.append(emptyRow(t("boundary.regions.title", lang)));
      this.container.append(panel);
      return;
    }
    for (const region of source.regions) {
      const detail = [
        `${region.lower_vertices.length} ${t("telemetry.vertices", lang)}`,
        region.communications_shadow_attenuation_db === null
          ? null
          : `${region.communications_shadow_attenuation_db} dB`,
      ]
        .filter((part) => part !== null)
        .join(" · ");
      panel.append(
        keyValue(`${region.region_id} (${t(REGION_KIND_KEYS[region.kind], lang)})`, detail),
      );
    }
    this.container.append(panel);
  }

  private renderNetwork(): void {
    const lang = currentLanguage();
    const source = this.source!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("section.network", lang)));
    const frame = source.networkFrame;
    if (frame === null) {
      panel.append(emptyRow(t("telemetry.networkNoFrame", lang)));
    } else {
      panel.append(
        keyValue(
          `${t("clock.tick", lang)} ${frame.at.tick}`,
          `${t("telemetry.nodes", lang)} ${frame.nodes.length} · ${t("telemetry.links", lang)} ${frame.links.length}`,
        ),
      );
    }
    const events = source.networkEvents.slice(-6);
    for (const event of events) {
      const detail = [
        event.entity_id === null ? null : `entity=${event.entity_id}`,
        ...event.public_payload.map((attribute) => `${attribute.name}=${formatAttributeValue(attribute.value)}`),
      ]
        .filter((part) => part !== null)
        .join(" · ");
      panel.append(
        keyValue(
          `${event.source} · ${t("clock.tick", lang)} ${event.at.tick}`,
          detail,
        ),
      );
    }
    this.container.append(panel);
  }

  private renderEvidence(): void {
    const lang = currentLanguage();
    const source = this.source!;
    const panel = element("section", "panel");
    panel.append(element("div", "panel-head", t("section.evidence", lang)));
    const trace = source.trace;
    if (trace === null) {
      panel.append(emptyRow(t("telemetry.evidenceLive", lang)));
      if (source.sample !== null) {
        panel.append(
          keyValue(t("telemetry.sampleDigest", lang), shortDigest(source.sample.sample_digest)),
          keyValue(t("telemetry.sampleStage", lang), t(STAGE_KEYS[source.sample.stage], lang)),
        );
      }
      this.container.append(panel);
      return;
    }
    panel.append(
      keyValue(
        t("telemetry.runtimeArtifacts", lang),
        String(trace.runtime_artifacts.length),
      ),
      keyValue(t("telemetry.sensorFrames", lang), String(trace.sensor_frames.length)),
    );
    const verifier = source.verifier;
    if (verifier === null) {
      panel.append(emptyRow(t("boundary.verifier.title", lang)));
    } else {
      panel.append(
        statusValue(
          t("detection.verifierStatus", lang),
          verifier.status,
          statusTone(verifier.status),
        ),
        keyValue(t("label.coverage", lang), verifier.coverage_complete ? t("coverage.complete", lang) : t("coverage.incomplete", lang)),
      );
      const recentFrames = trace.sensor_frames.slice(-4);
      for (const frame of recentFrames) {
        panel.append(
          keyValue(
            `${t("clock.tick", lang)} ${frame.at.tick} · ${formatSimTime(frame.at.sim_time_ns)}`,
            `${frame.observation_id} · ${frame.frame_id} · ${shortDigest(frame.payload_digest)}`,
          ),
        );
      }
    }
    this.container.append(panel);
  }

  dispose(): void {
    this.container.replaceChildren();
    this.source = null;
  }
}

function formatAttributeValue(value: string | number | boolean | null): string {
  return value === null ? "null" : String(value);
}

function formatOptional(value: number | null | undefined, format: (value: number) => string): string {
  return value === undefined || value === null ? UNDECLARED() : format(value);
}
