/**
 * Derived primary-flight display from one declared UAV StateSample.
 *
 * This panel is not a Gazebo camera feed. Attitude, speed and altitude are
 * computed from the recorded pose and velocity; missing contract fields
 * render as undeclared. Target lock is shown only when the caller supplies
 * a declared entity id and a distance derived from two ECEF samples.
 */

import type { SceneState, StateSample } from "./generated/aero-bench-contracts";
import { element, append, actionButton } from "./ui";

export interface TelemetryHudCallbacks {
  readonly onCameraModeChange: (mode: "free" | "chase" | "cockpit") => void;
  readonly onSwapViews?: () => void;
  /** Select one of the declared UAV samples as the primary instrument view. */
  readonly onUavSelect?: (entityId: string) => void;
  /** The HUD's on-screen rectangle changed (shown/hidden, drag end, minimize, clamp); overlays must re-place. */
  readonly onLayoutChange?: () => void;
}

function drawMiniHud(canvas: HTMLCanvasElement, sample: StateSample): void {
  const context = canvas.getContext("2d");
  if (context === null) return;
  const width = canvas.width;
  const height = canvas.height;
  const velocity = sample.linear_velocity_enu;
  const speed = Math.hypot(velocity.east_mps, velocity.north_mps, velocity.up_mps);
  const altitude = sample.pose.position.agl_m;
  const battery = sample.battery?.remaining_fraction;
  context.clearRect(0, 0, width, height);
  const gradient = context.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#0d3046");
  gradient.addColorStop(1, "#08131c");
  context.fillStyle = gradient;
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "rgba(79,216,245,.42)";
  context.strokeRect(1, 1, width - 2, height - 2);
  context.strokeStyle = "rgba(205,241,252,.45)";
  context.beginPath();
  context.moveTo(width / 2, 10);
  context.lineTo(width / 2, height - 10);
  context.moveTo(20, height / 2);
  context.lineTo(width - 20, height / 2);
  context.stroke();
  context.fillStyle = "#e9fbff";
  context.font = "600 16px ui-monospace,monospace";
  context.textAlign = "left";
  context.fillText(`ALT ${altitude.toFixed(1)} m`, 10, 22);
  context.fillStyle = "#74e7f8";
  context.font = "11px ui-monospace,monospace";
  context.fillText(`SPD ${speed.toFixed(1)} m/s`, 10, 40);
  context.textAlign = "right";
  context.fillStyle = battery === null || battery === undefined ? "#9bb2bf" : battery > 0.3 ? "#70e0a0" : "#f17a7a";
  if (battery !== null && battery !== undefined) context.fillText(`BAT ${(battery * 100).toFixed(0)}%`, width - 10, 22);
  context.fillStyle = "#ffd166";
  context.fillText(`T${sample.at.tick}`, width - 10, 40);
}

export class TelemetryHud {
  readonly root: HTMLElement;
  private readonly callbacks: TelemetryHudCallbacks;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly badge: HTMLElement;
  private readonly titleText: HTMLElement;
  private readonly modeSelect: HTMLSelectElement;
  private readonly fleetStrip: HTMLElement;
  private readonly miniCards = new Map<string, { card: HTMLButtonElement; canvas: HTMLCanvasElement; label: HTMLElement; badge: HTMLElement }>();
  private isMinimized = false;
  private fleetSamples: readonly StateSample[] = [];
  private readonly minimizeButton: HTMLButtonElement;
  private readonly resize = () => this.clampToMap();
  private currentSample: StateSample | null = null;
  private selectedUavId: string | null = null;
  private currentTick = 0;
  private currentTargetName: string | null = null;
  private currentTargetDist: number | null = null;
  private lastNotifiedLayoutHidden: boolean | null = null;

  private notifyLayoutChange(): void {
    const hidden = this.root.hidden === true;
    // One notification per real visibility flip; visibility-triggered flips
    // also notify because overlays re-place whenever this state changes.
    if (hidden !== this.lastNotifiedLayoutHidden) {
      this.lastNotifiedLayoutHidden = hidden;
      this.callbacks.onLayoutChange?.();
    }
  }

  constructor(callbacks: TelemetryHudCallbacks) {
    this.callbacks = callbacks;
    this.root = element("div", "telemetry-hud");
    this.root.setAttribute("role", "region");
    this.root.setAttribute("aria-label", "无人机快递站点多视角回放");

    // Header bar
    const header = element("div", "gz-header");
    header.dataset.dragHandle = "true";
    this.titleText = element("div", "gz-title", "无人机快递站点 · 多视角 PFD");
    this.titleText.title = "Primary flight display derived from the recorded UAV StateSample. Not a Gazebo camera frame.";
    this.badge = element("span", "gz-badge", "STANDBY");

    const controls = element("div", "gz-controls");

    // Camera view select
    this.modeSelect = document.createElement("select");
    this.modeSelect.id = "camera-mode-select";
    this.modeSelect.className = "gz-mode-select";
    this.modeSelect.setAttribute("aria-label", "Camera Mode");
    const modes = [
      { id: "cockpit", label: "视角: 机载主视 (Onboard Cam)" },
      { id: "chase", label: "视角: 机尾追随 (Chase)" },
      { id: "free", label: "视角: 自由漫游" },
    ];
    for (const m of modes) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      this.modeSelect.append(opt);
    }
    this.modeSelect.value = "cockpit";
    this.modeSelect.addEventListener("change", () => {
      this.callbacks.onCameraModeChange(this.modeSelect.value as "free" | "chase" | "cockpit");
    });

    const swapBtn = actionButton("⇋ 对调", () => {
      this.callbacks.onSwapViews?.();
    });
    swapBtn.className = "gz-icon-btn gz-swap";
    swapBtn.hidden = callbacks.onSwapViews === undefined;
    swapBtn.title = "主副视口对调";

    const minBtn = actionButton("—", () => {
      this.toggleMinimize();
    });
    minBtn.className = "gz-icon-btn gz-minimize";
    this.minimizeButton = minBtn;
    minBtn.setAttribute("aria-expanded", "true");
    minBtn.title = "最小化/展开";
    minBtn.setAttribute("aria-label", "最小化或展开多无人机视口");

    append(controls, this.modeSelect, swapBtn, minBtn);
    append(header, this.titleText, this.badge, controls);

    // Canvas container
    const canvasContainer = element("div", "gz-canvas-container");
    this.canvas = document.createElement("canvas");
    this.canvas.className = "gz-canvas";
    this.canvas.width = 480;
    this.canvas.height = 270;
    this.ctx = this.canvas.getContext("2d");

    append(canvasContainer, this.canvas);
    this.fleetStrip = element("div", "uav-fleet-strip");
    this.fleetStrip.setAttribute("role", "listbox");
    this.fleetStrip.setAttribute("aria-label", "无人机视角列表");
    append(this.root, header, canvasContainer, this.fleetStrip);

    // The viewport is an operator widget, so it can be repositioned without
    // changing the map camera. Dragging starts only on the title strip and is
    // bounded to the map container by CSS/DOM coordinates.
    let drag: { pointerId: number; x: number; y: number; left: number; top: number } | null = null;
    header.addEventListener("pointerdown", (event) => {
      if (event.target instanceof HTMLElement && event.target.closest("button,select")) return;
      const rect = this.root.getBoundingClientRect();
      drag = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      header.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    });
    header.addEventListener("pointermove", (event) => {
      if (drag === null || drag.pointerId !== event.pointerId) return;
      const parent = this.root.parentElement?.getBoundingClientRect();
      const bounds = parent ?? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight, width: window.innerWidth, height: window.innerHeight };
      const nextLeft = Math.max(bounds.left, Math.min(bounds.right - this.root.offsetWidth, drag.left + event.clientX - drag.x));
      const nextTop = Math.max(bounds.top, Math.min(bounds.bottom - this.root.offsetHeight, drag.top + event.clientY - drag.y));
      this.root.style.left = `${nextLeft - bounds.left}px`;
      this.root.style.top = `${nextTop - bounds.top}px`;
      this.root.style.right = "auto";
      this.root.style.bottom = "auto";
    });
    const releaseDrag = (event: PointerEvent) => {
      if (drag?.pointerId === event.pointerId) {
        drag = null;
        this.callbacks.onLayoutChange?.();
      }
    };
    header.addEventListener("pointerup", releaseDrag);
    header.addEventListener("pointercancel", releaseDrag);

    window.addEventListener("resize", this.resize);

    // Initial render of standby HUD
    this.renderHud();
  }

  setCameraModeSelect(mode: "free" | "chase" | "cockpit"): void {
    this.modeSelect.value = mode;
  }

  setSelectedUav(entityId: string | null): void {
    this.selectedUavId = entityId;
    this.syncFleetSelection();
  }

  toggleMinimize(): void {
    this.isMinimized = !this.isMinimized;
    this.root.classList.toggle("minimized", this.isMinimized);
    this.minimizeButton.textContent = this.isMinimized ? "展开" : "—";
    this.minimizeButton.setAttribute("aria-expanded", String(!this.isMinimized));
    this.minimizeButton.title = this.isMinimized ? "恢复无人机视窗" : "最小化无人机视窗";
    this.clampToMap();
    this.callbacks.onLayoutChange?.();
    // Drawing is skipped while minimized; catch up with the latest recorded state on restore.
    if (!this.isMinimized) {
      this.renderHud();
      this.renderFleet(this.fleetSamples);
    }
  }

  private clampToMap(): void {
    const bounds = this.root.parentElement?.getBoundingClientRect();
    const rect = this.root.getBoundingClientRect();
    if (!bounds || bounds.width <= 0 || rect.width <= 0) return;
    const left = Math.max(0, Math.min(Math.max(0, bounds.width - rect.width), rect.left - bounds.left));
    const top = Math.max(0, Math.min(Math.max(0, bounds.height - rect.height), rect.top - bounds.top));
    this.root.style.left = `${left}px`;
    this.root.style.top = `${top}px`;
    this.root.style.right = "auto";
    this.root.style.bottom = "auto";
  }

  dispose(): void {
    window.removeEventListener("resize", this.resize);
    this.root.remove();
  }

  update(sceneState: SceneState | null, targetInfo?: { name: string; distanceM: number | null }, selectedUavId?: string | null): void {
    if (sceneState === null) {
      this.root.hidden = true;
      delete this.root.dataset.entityId;
      delete this.root.dataset.sampleDigest;
      this.currentSample = null;
      this.selectedUavId = null;
      this.currentTick = 0;
      this.currentTargetName = null;
      this.currentTargetDist = null;
      this.titleText.textContent = "姿态 PFD · —";
      this.badge.textContent = "OFFLINE";
      this.badge.className = "gz-badge tone-bad";
      this.renderFleet([]);
      this.renderHud();
      this.notifyLayoutChange();
      return;
    }

    this.currentTick = sceneState.at.tick;
    const uavs = sceneState.samples.filter((sample) => sample.entity_id.toLowerCase().includes("uav"));
    this.root.hidden = uavs.length === 0;
    if (selectedUavId !== undefined) this.selectedUavId = selectedUavId;
    if (this.selectedUavId === null || !uavs.some((sample) => sample.entity_id === this.selectedUavId)) {
      this.selectedUavId = uavs[0]?.entity_id ?? null;
    }
    this.currentSample = uavs.find((sample) => sample.entity_id === this.selectedUavId) ?? uavs[0] ?? null;
    this.root.dataset.entityId = this.currentSample?.entity_id ?? "";
    this.root.dataset.sampleDigest = this.currentSample?.sample_digest ?? "";

    if (targetInfo !== undefined) {
      this.currentTargetName = targetInfo.name;
      this.currentTargetDist = targetInfo.distanceM;
    } else {
      this.currentTargetName = null;
      this.currentTargetDist = null;
    }

    const isHold = this.currentSample?.mode?.includes("HOLD") || this.currentSample?.mode?.includes("INSPECT");
    if (isHold) {
      this.badge.textContent = this.currentSample?.mode ?? "HOLD";
      this.badge.className = "gz-badge tone-active";
    } else {
      this.badge.textContent = "ONLINE · TICK " + this.currentTick;
      this.badge.className = "gz-badge tone-ok";
    }

    this.renderHud();
    this.renderFleet(uavs);
    this.notifyLayoutChange();
  }

  private renderFleet(samples: readonly StateSample[]): void {
    this.fleetSamples = samples;
    this.root.dataset.uavCount = String(samples.length);
    this.fleetStrip.style.gridTemplateColumns = `repeat(${Math.max(1, Math.min(3, samples.length))}, minmax(0, 1fr))`;
    const activeIds = new Set(samples.map((sample) => sample.entity_id));
    for (const [id, refs] of this.miniCards) {
      if (!activeIds.has(id)) {
        refs.card.remove();
        this.miniCards.delete(id);
      }
    }
    for (const sample of samples) {
      let refs = this.miniCards.get(sample.entity_id);
      if (refs === undefined) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "uav-view-card";
        card.dataset.uavId = sample.entity_id;
        card.setAttribute("role", "option");
        card.addEventListener("click", () => {
          this.selectedUavId = sample.entity_id;
          this.callbacks.onUavSelect?.(sample.entity_id);
          this.syncFleetSelection();
          this.renderHud();
        });
        const miniCanvas = document.createElement("canvas");
        miniCanvas.width = 220;
        miniCanvas.height = 76;
        miniCanvas.className = "uav-view-canvas";
        const label = element("strong", "uav-view-label", sample.entity_id);
        const badge = element("span", "uav-view-badge", "TICK —");
        append(card, miniCanvas, label, badge);
        this.fleetStrip.append(card);
        refs = { card, canvas: miniCanvas, label, badge };
        this.miniCards.set(sample.entity_id, refs);
      }
      refs.label.textContent = sample.entity_id;
      const battery = sample.battery?.remaining_fraction;
      refs.badge.textContent = [`TICK ${sample.at.tick}`, sample.mode, battery == null ? null : `电量 ${(battery * 100).toFixed(0)}%`].filter(value => value != null).join(" · ");
      // The minimized widget hides the fleet strip; its canvases are redrawn on restore.
      if (!this.isMinimized) drawMiniHud(refs.canvas, sample);
    }
    this.syncFleetSelection();
  }

  private syncFleetSelection(): void {
    for (const [id, refs] of this.miniCards) {
      const selected = id === this.selectedUavId;
      refs.card.classList.toggle("selected", selected);
      refs.card.setAttribute("aria-selected", String(selected));
    }
    if (this.currentSample !== null) this.titleText.textContent = `姿态 PFD · ${this.currentSample.entity_id}`;
  }

  private renderHud(): void {
    const ctx = this.ctx;
    // The minimized widget hides the PFD canvas, so per-tick drawing there is wasted work.
    if (ctx === null || this.isMinimized) return;
    const w = this.canvas.width;
    const h = this.canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    ctx.clearRect(0, 0, w, h);
    if (this.currentSample === null) {
      ctx.fillStyle = "#8ba7b6";
      ctx.font = "14px monospace";
      ctx.textAlign = "center";
      ctx.fillText("NO RECORDED STATE", cx, cy);
      return;
    }

    // Extract roll, pitch, yaw from authoritative orientation_enu
    let roll = 0;
    let pitch = 0;
    let yaw = 0;
    let agl = 0;
    let speed = 0;
    let flightMode: string | null = null;
    let battery: number | null = null;

    if (this.currentSample) {
      const q = this.currentSample.pose.orientation_enu;
      // Exact Tait-Bryan ZYX Euler angles from quaternion
      const sinR = 2 * (q.qw * q.qx + q.qy * q.qz);
      const cosR = 1 - 2 * (q.qx * q.qx + q.qy * q.qy);
      roll = Math.atan2(sinR, cosR) * (180 / Math.PI);

      const sinP = 2 * (q.qw * q.qy - q.qz * q.qx);
      pitch = Math.asin(Math.max(-1, Math.min(1, sinP))) * (180 / Math.PI);

      const sinY = 2 * (q.qw * q.qz + q.qx * q.qy);
      const cosY = 1 - 2 * (q.qy * q.qy + q.qz * q.qz);
      yaw = (Math.atan2(sinY, cosY) * (180 / Math.PI) + 360) % 360;

      agl = this.currentSample.pose.position.agl_m;
      const vx = this.currentSample.linear_velocity_enu.east_mps;
      const vy = this.currentSample.linear_velocity_enu.north_mps;
      const vz = this.currentSample.linear_velocity_enu.up_mps;
      speed = Math.sqrt(vx * vx + vy * vy + vz * vz);
      flightMode = this.currentSample.mode ?? null;
      const remaining = this.currentSample.battery?.remaining_fraction;
      battery = remaining === undefined || remaining === null ? null : remaining * 100;
    }

    // In aviation: HDG = (90 - yaw_enu) mod 360 (0=North, 90=East, 180=South, 270=West)
    const heading = ((90 - yaw) % 360 + 360) % 360;

    // 1. Sky & Ground Primary Flight Display Artificial Horizon
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    ctx.clip();

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((-roll * Math.PI) / 180);
    const pitchPxPerDeg = 2.4;
    const pitchOffset = pitch * pitchPxPerDeg;
    ctx.translate(0, pitchOffset);

    // Sky gradient (top)
    const skyGrad = ctx.createLinearGradient(0, -h * 1.5, 0, 0);
    skyGrad.addColorStop(0, "#082136");
    skyGrad.addColorStop(1, "#103c5e");
    ctx.fillStyle = skyGrad;
    ctx.fillRect(-w * 1.5, -h * 1.5, w * 3, h * 1.5);

    // Ground gradient (bottom)
    const groundGrad = ctx.createLinearGradient(0, 0, 0, h * 1.5);
    groundGrad.addColorStop(0, "#1c2830");
    groundGrad.addColorStop(1, "#0a131a");
    ctx.fillStyle = groundGrad;
    ctx.fillRect(-w * 1.5, 0, w * 3, h * 1.5);

    // Horizon line
    ctx.strokeStyle = "#e6f6ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-w, 0);
    ctx.lineTo(w, 0);
    ctx.stroke();

    // Pitch ladder rungs (+/- 10, 20, 30 deg)
    for (const p of [30, 20, 10, -10, -20, -30]) {
      const py = -p * pitchPxPerDeg;
      const isPositive = p > 0;
      const rungWidth = Math.abs(p) % 20 === 0 ? 54 : 36;
      ctx.strokeStyle = isPositive ? "rgba(230, 246, 255, 0.75)" : "rgba(255, 180, 120, 0.75)";
      ctx.lineWidth = 1.5;
      if (!isPositive) {
        ctx.setLineDash([4, 4]);
      } else {
        ctx.setLineDash([]);
      }
      ctx.beginPath();
      ctx.moveTo(-rungWidth, py);
      ctx.lineTo(rungWidth, py);
      // Down/up end ticks
      const tickH = isPositive ? 5 : -5;
      ctx.moveTo(-rungWidth, py);
      ctx.lineTo(-rungWidth, py + tickH);
      ctx.moveTo(rungWidth, py);
      ctx.lineTo(rungWidth, py + tickH);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = isPositive ? "rgba(230, 246, 255, 0.85)" : "rgba(255, 180, 120, 0.85)";
      ctx.font = "bold 9px monospace";
      ctx.textAlign = "right";
      ctx.fillText(String(Math.abs(p)), -rungWidth - 6, py + 3);
      ctx.textAlign = "left";
      ctx.fillText(String(Math.abs(p)), rungWidth + 6, py + 3);
    }
    ctx.restore();
    ctx.restore();

    // 2. Roll Angle Arc (Top Center)
    ctx.save();
    ctx.strokeStyle = "rgba(180, 220, 245, 0.4)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(cx, cy, 110, (-140 * Math.PI) / 180, (-40 * Math.PI) / 180);
    ctx.stroke();

    // Roll scale tick marks
    for (const rDeg of [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]) {
      const angle = ((-90 + rDeg) * Math.PI) / 180;
      const rInner = 105;
      const rOuter = Math.abs(rDeg) % 30 === 0 ? 116 : 112;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(angle) * rInner, cy + Math.sin(angle) * rInner);
      ctx.lineTo(cx + Math.cos(angle) * rOuter, cy + Math.sin(angle) * rOuter);
      ctx.stroke();
    }
    // Roll pointer (rotates with aircraft roll)
    const rollPtrAngle = ((-90 - roll) * Math.PI) / 180;
    ctx.fillStyle = "#ffd166";
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(rollPtrAngle) * 104, cy + Math.sin(rollPtrAngle) * 104);
    ctx.lineTo(cx + Math.cos(rollPtrAngle - 0.05) * 94, cy + Math.sin(rollPtrAngle - 0.05) * 94);
    ctx.lineTo(cx + Math.cos(rollPtrAngle + 0.05) * 94, cy + Math.sin(rollPtrAngle + 0.05) * 94);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    // 3. Central Fixed Aircraft Reference Symbol (Waterline Pip & Wings)
    ctx.strokeStyle = "#ffd166";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    // Left wing
    ctx.moveTo(cx - 50, cy);
    ctx.lineTo(cx - 20, cy);
    ctx.lineTo(cx - 20, cy + 6);
    // Right wing
    ctx.moveTo(cx + 50, cy);
    ctx.lineTo(cx + 20, cy);
    ctx.lineTo(cx + 20, cy + 6);
    // Center dot
    ctx.moveTo(cx - 3, cy);
    ctx.lineTo(cx + 3, cy);
    ctx.moveTo(cx, cy - 3);
    ctx.lineTo(cx, cy + 3);
    ctx.stroke();

    // 4. Compass Heading Tape (Top)
    const tapeW = 180;
    const tapeH = 24;
    const tapeY = 6;
    ctx.fillStyle = "rgba(7, 20, 32, 0.85)";
    ctx.fillRect(cx - tapeW / 2, tapeY, tapeW, tapeH);
    ctx.strokeStyle = "rgba(79, 216, 245, 0.6)";
    ctx.lineWidth = 1;
    ctx.strokeRect(cx - tapeW / 2, tapeY, tapeW, tapeH);

    // Dynamic compass ticks
    ctx.save();
    ctx.beginPath();
    ctx.rect(cx - tapeW / 2 + 1, tapeY + 1, tapeW - 2, tapeH - 2);
    ctx.clip();
    const pxPerDeg = 2.2;
    for (let deg = 0; deg < 360; deg += 5) {
      let diff = deg - heading;
      while (diff < -180) diff += 360;
      while (diff > 180) diff -= 360;
      const tickX = cx + diff * pxPerDeg;
      if (tickX < cx - tapeW / 2 - 10 || tickX > cx + tapeW / 2 + 10) continue;

      if (deg % 30 === 0) {
        ctx.strokeStyle = "#ffffff";
        ctx.beginPath();
        ctx.moveTo(tickX, tapeY + tapeH - 1);
        ctx.lineTo(tickX, tapeY + tapeH - 8);
        ctx.stroke();

        let label = (deg / 10).toString().padStart(2, "0");
        if (deg === 0) label = "N";
        else if (deg === 90) label = "E";
        else if (deg === 180) label = "S";
        else if (deg === 270) label = "W";

        ctx.fillStyle = deg % 90 === 0 ? "#ffd166" : "#e6f6ff";
        ctx.font = "bold 10px monospace";
        ctx.textAlign = "center";
        ctx.fillText(label, tickX, tapeY + 11);
      } else {
        ctx.strokeStyle = "rgba(230, 246, 255, 0.4)";
        ctx.beginPath();
        ctx.moveTo(tickX, tapeY + tapeH - 1);
        ctx.lineTo(tickX, tapeY + tapeH - 4);
        ctx.stroke();
      }
    }
    ctx.restore();

    // Center lubber line on compass
    ctx.fillStyle = "#ffd166";
    ctx.beginPath();
    ctx.moveTo(cx, tapeY + tapeH + 3);
    ctx.lineTo(cx - 4, tapeY + tapeH - 2);
    ctx.lineTo(cx + 4, tapeY + tapeH - 2);
    ctx.closePath();
    ctx.fill();

    // 5. Speed Tape (Left)
    ctx.fillStyle = "rgba(7, 20, 32, 0.82)";
    ctx.fillRect(10, cy - 42, 60, 84);
    ctx.strokeStyle = "rgba(79, 216, 245, 0.45)";
    ctx.strokeRect(10, cy - 42, 60, 84);
    ctx.fillStyle = "#7aa7ff";
    ctx.font = "bold 9px monospace";
    ctx.textAlign = "center";
    ctx.fillText("SPD m/s", 40, cy - 28);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 15px monospace";
    ctx.fillText(speed.toFixed(1), 40, cy);
    ctx.fillStyle = "#8ba7b6";
    ctx.font = "9px sans-serif";
    ctx.fillText("GS", 40, cy + 18);
    ctx.fillText(`${(speed * 3.6).toFixed(0)} km/h`, 40, cy + 32);

    // 6. Altitude Tape (Right)
    ctx.fillStyle = "rgba(7, 20, 32, 0.82)";
    ctx.fillRect(w - 70, cy - 42, 60, 84);
    ctx.strokeStyle = "rgba(79, 216, 245, 0.45)";
    ctx.strokeRect(w - 70, cy - 42, 60, 84);
    ctx.fillStyle = "#7aa7ff";
    ctx.font = "bold 9px monospace";
    ctx.textAlign = "center";
    ctx.fillText("ALT m", w - 40, cy - 28);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 15px monospace";
    ctx.fillText(agl.toFixed(1), w - 40, cy);
    ctx.fillStyle = "#8ba7b6";
    ctx.font = "9px sans-serif";
    ctx.fillText("AGL", w - 40, cy + 18);
    ctx.fillText(`BARO`, w - 40, cy + 32);

    // 7. Inspection Target Lock Reticle (if locked)
    if (this.currentTargetName) {
      ctx.strokeStyle = "#ffd166";
      ctx.lineWidth = 1.5;
      const tx = cx + 35;
      const ty = cy - 20;
      ctx.strokeRect(tx - 22, ty - 22, 44, 44);
      ctx.fillStyle = "#ffd166";
      ctx.font = "bold 10px monospace";
      ctx.textAlign = "center";
      ctx.fillText("LOCK: " + this.currentTargetName, tx, ty - 27);
      if (this.currentTargetDist !== null) {
        ctx.fillText(`DIST: ${this.currentTargetDist.toFixed(1)}m`, tx, ty + 34);
      }
    }

    // 8. Bottom Status Bar (Mode, Pitch, Roll, HDG, Battery)
    ctx.fillStyle = "rgba(5, 15, 24, 0.92)";
    ctx.fillRect(6, h - 24, w - 12, 20);
    ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
    ctx.strokeRect(6, h - 24, w - 12, 20);

    ctx.fillStyle = "#38bdf8";
    ctx.font = "bold 10px monospace";
    ctx.textAlign = "left";
    if (flightMode !== null) ctx.fillText(`MODE: ${flightMode}`, 14, h - 10);

    ctx.textAlign = "center";
    ctx.fillStyle = "#e6f6ff";
    ctx.fillText(
      `PITCH: ${pitch >= 0 ? "+" : ""}${pitch.toFixed(1)}°  ROLL: ${roll >= 0 ? "+" : ""}${roll.toFixed(1)}°  HDG: ${Math.round(heading).toString().padStart(3, "0")}°`,
      cx,
      h - 10,
    );

    ctx.textAlign = "right";
    ctx.fillStyle = battery === null ? "#8ba7b6" : battery > 30 ? "#7ee08a" : "#ff7a6b";
    if (battery !== null) ctx.fillText(`BAT: ${battery.toFixed(0)}%`, w - 14, h - 10);
  }
}
