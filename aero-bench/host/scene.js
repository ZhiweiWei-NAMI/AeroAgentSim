// Owned host scene renderer. Attribution: this module is adapted from the P02
// parcel prototype (validation/p02-parcel-host/source @
// 15f473a4dc0ed4f80e3000b0acef6e875c617393, file src/scene.js, Apache-2.0 per the checkout's LICENSE
// the checkout's LICENSE). The prototype's static authored park — its fixed
// stations, buildings, trees, roads and "SMART PARK" caption — is intentionally
// NOT reused: host-supplied geometry alone is drawn. The drawing helpers that
// remain (projection, isometric box, path, pick, marker, label) come from the
// prototype with attribution. PR12: light presentation pass — the same
// host-supplied geometry in a bright blue WareTrack-inspired palette on a
// light ground; structure, picks, markers and data unchanged.
import { escapeHtml, project, isoBox } from './adapter.js';

const f = n => Number(n.toFixed(2));
const p = v => project(v).map(n => n.toFixed(2)).join(',');
const poly = (points, fill, stroke = 'none', extra = '') =>
  `<polygon points="${points.map(p).join(' ')}" fill="${fill}" stroke="${stroke}" ${extra}/>`;
const path = points => points.map((v, i) => `${i ? 'L' : 'M'} ${p(v)}`).join(' ');

function box(e, n, u, w, d, hgt, colors, extra = '') {
  return isoBox(e, n, u, w, d, hgt, colors, extra);
}

function marker(position, id, selected) {
  if (selected !== id) return '';
  const [x, y] = project(position);
  return `<g pointer-events="none"><ellipse cx="${x.toFixed(2)}" cy="${(y + 4).toFixed(2)}" rx="22" ry="9" fill="none" stroke="#2563eb" stroke-width="2"/><path d="M${x.toFixed(2)} ${(y - 22).toFixed(2)}v-16" stroke="#1d4ed8" stroke-width="2"/><circle cx="${x.toFixed(2)}" cy="${(y - 42).toFixed(2)}" r="5" fill="#2563eb" stroke="#fff" stroke-width="2"/></g>`;
}

function label(position, title, subtitle = '', color = '#33414d') {
  const [x, y] = project(position);
  return `<g class="scene-label" pointer-events="none"><text x="${x.toFixed(2)}" y="${y.toFixed(2)}" text-anchor="middle" fill="${color}" font-size="14" font-weight="650">${escapeHtml(title)}</text>${subtitle ? `<text x="${x.toFixed(2)}" y="${(y + 18).toFixed(2)}" text-anchor="middle" fill="#93a4b3" font-size="10">${escapeHtml(subtitle)}</text>` : ''}</g>`;
}

function pick(id, body, selected) {
  return `<g data-entity-id="${escapeHtml(id)}" class="scene-object ${selected === id ? 'selected' : ''}" role="img" aria-label="${escapeHtml(id)}">${body}</g>`;
}

/** Bright blue ground plane: host-supplied geometry only, no authored park. */
function hostGround() {
  let s = `<defs><pattern id="host-grid" width="36" height="36" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#a9c8e8"/></pattern></defs>`;
  s += `<rect width="1100" height="680" fill="#e8f2fd"/>`;
  s += `<rect width="1100" height="680" fill="url(#host-grid)" opacity=".35"/>`;
  s += `<g class="scene-compass" transform="translate(44 602)"><path d="M0 24v-24l-4 9m4-9 4 9M0 24h28l-9-4m9 4-9 4" stroke="#6d93b8" stroke-width="1.5" fill="none"/><text x="-4" y="-8" font-size="11" fill="#4d759c">N</text><text x="33" y="29" font-size="11" fill="#4d759c">E</text></g>`;
  return s;
}

function stationGlyph(station, selected) {
  const [e, n, u] = station.position;
  let body = `<ellipse cx="${project([e, n, 0])[0].toFixed(2)}" cy="${project([e, n, 0])[1].toFixed(2)}" rx="17" ry="7" fill="#2f7fd820"/>`;
  body += box(e - 2.2, n - 2.2, u, 4.4, 4.4, 1.6, ['#cfe4f6', '#8db8dc', '#6ba3cf']);
  body += marker([e, n, (u ?? 0) + 2.2], station.id, selected);
  body += `<text x="${project([e, n, u + 3])[0].toFixed(2)}" y="${(project([e, n, u + 3])[1] + 4).toFixed(2)}" text-anchor="middle" font-size="11" fill="#33586e">${escapeHtml(station.label)}</text>`;
  return pick(station.id, body, selected);
}

function vehicleGlyph(entity, selected, kind) {
  const [e, n, u] = entity.position;
  let body = '';
  const [sx, sy] = project([e, n, 0]);
  body += `<ellipse cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" rx="${kind === 'uav' ? 20 : 17}" ry="7" fill="#2f7fd820"/>`;
  if (kind === 'ugv') {
    body += box(e - 2.5, n - 1.5, Math.max(u - 0.5, 0), 5, 3, 0.85, ['#ffffff', '#9dbdd8', '#bcd5ea']);
    body += box(e - 2.5, n - 1.5, Math.max(u - 0.5, 0) + 0.85, 1.3, 3, 1.4, ['#dbeefe', '#79a6d0', '#a4c8e6']);
    for (const offset of [-1.7, 1.5]) {
      const [x, y] = project([e + offset, n - 1.5, 0.5]);
      body += `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="#2c4a63"/><circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2" fill="#7fa6c6"/>`;
    }
  } else if (kind === 'uav') {
    const [x, y] = project(entity.position);
    body += `<path d="M${sx.toFixed(2)} ${sy.toFixed(2)}L${x.toFixed(2)} ${y.toFixed(2)}" stroke="#8fb4d4" stroke-width="1" stroke-dasharray="3 5"/>`;
    for (const [de, dn] of [[-3, -2], [-3, 2], [3, -2], [3, 2]]) {
      const [a, b] = project([e + de, n + dn, u]);
      body += `<path d="M${x.toFixed(2)} ${y.toFixed(2)}L${a.toFixed(2)} ${b.toFixed(2)}" stroke="#4e7ba3" stroke-width="4"/><ellipse cx="${a.toFixed(2)}" cy="${b.toFixed(2)}" rx="12" ry="4.8" fill="#a5c9e86b" stroke="#2f7fd8" stroke-width="1.6"/>`;
    }
    body += box(e - 1.2, n - 1, Math.max(u - 0.25, 0), 2.4, 2, 0.65, ['#f2f9ff', '#7aa6cd', '#a8cae6']);
  } else {
    // Facility kind without a specialised glyph: an explicit host marker.
    body += `<circle cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" r="6" fill="#2f7fd8" stroke="#fff" stroke-width="1.5"/>`;
  }
  body += marker(entity.position, entity.id, selected);
  const [lx, ly] = project([e, n, u]);
  body += `<text x="${lx.toFixed(2)}" y="${(ly + 29).toFixed(2)}" text-anchor="middle" font-size="11" fill="#33586e">${escapeHtml(entity.label ?? entity.id)}</text>`;
  return pick(entity.id, body, selected);
}

function parcelGlyph(parcel, selected, options = {}) {
  const [e, n, u] = parcel.position;
  const inactive = parcel.custodyKnown === false || parcel.state === 'unknown';
  let body = box(e - 0.8, n - 0.7, u, 1.6, 1.4, 1.25, inactive ? ['#f3e4cf', '#d9c1a1', '#c4a986'] : ['#ffd88a', '#f0a63c', '#d98f24']);
  const [x, y] = project([e, n, u + 1.25]);
  body += `<path d="M${(x - 5).toFixed(2)} ${(y - 1).toFixed(2)}l9 -3" stroke="#a5712c" stroke-width="2"/>`;
  body += marker([e, n, u + 1.7], parcel.id, selected);
  // Stable cargo-ID callout. Placement is collision-aware in PROJECTED space
  // (real coordinates of this frame's already-drawn labels; data positions
  // and custody are never touched): it tries right, left, above, below and
  // takes the first slot whose rectangle does not overlap an occupied label
  // rect, then records its own rect. Presentation only — never a coordinate
  // or custody claim.
  const text = String(parcel.label ?? parcel.id);
  const width = Math.max(66, text.length * 8) + 4;
  const height = 21;
  const slots = [
    { dx: 12, dy: -27, anchorX: x + 12 + width / 2, textY: y - 12, tail: `M${(x + 13).toFixed(2)} ${(y - 8).toFixed(2)}l-9 8` },
    { dx: -12 - width, dy: -27, anchorX: x - 12 - width / 2, textY: y - 12, tail: `M${(x - 13).toFixed(2)} ${(y - 8).toFixed(2)}l9 8` },
    { dx: -width / 2, dy: -62, anchorX: x, textY: y - 47, tail: `M${x.toFixed(2)} ${(y - 30).toFixed(2)}l0 9` },
    { dx: -width / 2, dy: 16, anchorX: x, textY: y + 31, tail: `M${x.toFixed(2)} ${(y + 13).toFixed(2)}l0 -9` },
  ];
  const occupied = options.labelRects ?? [];
  const collides = r => occupied.some(o =>
    r.x < o.x + o.w && r.x + r.w > o.x && r.y < o.y + o.h && r.y + r.h > o.y);
  let chosen = slots[0];
  for (const slot of slots) {
    if (!collides({ x: x + slot.dx, y: y + slot.dy, w: width, h: height })) { chosen = slot; break; }
  }
  occupied.push({ x: x + chosen.dx, y: y + chosen.dy, w: width, h: height });
  const rectX = x + chosen.dx;
  const rectY = y + chosen.dy;
  body += `<g pointer-events="none"><rect x="${rectX.toFixed(2)}" y="${rectY.toFixed(2)}" width="${width.toFixed(2)}" height="${height}" rx="5" fill="#ffffff" stroke="#9dbdd8"/><text x="${chosen.anchorX.toFixed(2)}" y="${chosen.textY.toFixed(2)}" text-anchor="middle" fill="#1d4ed8" font-size="11" font-weight="600">${escapeHtml(text)}</text><path d="${chosen.tail}" stroke="#9dbdd8" stroke-width="1.5"/></g>`;
  return pick(parcel.id, body, selected);
}

/**
 * Render the host frame. Only host-supplied records are drawn:
 * stations (host evidence geometry), SceneState entities (real BENCH motion),
 * SceneState-joined parcels and host routes. When the frame carries no
 * geometry the canvas is an empty labelled plane — no authored park appears.
 */
export function renderScene(frame, { t, selectedId = null, follow = false, entityKind = () => 'facility' } = {}) {
  let body = hostGround();
  const drawableEntities = frame.entities.filter(e => e.position !== null);
  if (frame.routes) {
    for (const key of Object.keys(frame.routes)) {
      body += `<path d="${path(frame.routes[key])}" fill="none" stroke="#7fb2e0" stroke-width="2" stroke-dasharray="6 6" opacity=".8"/>`;
    }
    if (frame.activeRoute && frame.routes[frame.activeRoute]) {
      body += `<path d="${path(frame.routes[frame.activeRoute])}" fill="none" stroke="#12996e" stroke-width="3" stroke-dasharray="7 5" opacity=".85"/>`;
    }
  }
  // Occupied projected-space label rectangles, filled as glyphs draw so the
  // collision-aware parcel callout avoids entity labels already placed.
  const labelRects = [];
  for (const entity of drawableEntities.filter(e => e.kind === 'station')) {
    body += stationGlyph(entity, selectedId);
    const [e, n, u] = entity.position;
    const [lx, ly] = project([e, n, (u ?? 0) + 3]);
    labelRects.push({ x: lx - 45, y: ly - 6, w: 90, h: 16 });
  }
  for (const entity of drawableEntities.filter(e => e.kind !== 'station')) {
    body += vehicleGlyph(entity, selectedId, entityKind(entity.id));
    const [lx, ly] = project(entity.position);
    labelRects.push({ x: lx - 45, y: ly + 18, w: 90, h: 16 });
  }
  for (const parcel of frame.parcels.filter(p2 => p2.position !== null)) {
    body += parcelGlyph(parcel, selectedId, { labelRects });
  }
  // Explicitly report declared-but-unplaced parcels on the canvas.
  for (const id of frame.unresolvedParcelIds ?? []) {
    const [x, y] = [80, 46 + 22 * (frame.unresolvedParcelIds.indexOf(id) + 1)];
    body += `<g pointer-events="none" class="unresolved-parcel"><rect x="${x - 12}" y="${y - 13}" width="150" height="18" rx="4" fill="#fdf6e6" stroke="#d9c1a1"/><text x="${x - 4}" y="${y}" font-size="10" fill="#8a611b">${escapeHtml(t('parcelUnknown'))} · ${escapeHtml(id)}</text></g>`;
  }
  const selected = [...frame.parcels, ...frame.entities].find(e => e.id === selectedId && e.position !== null);
  let viewBox = '-10 30 1100 650';
  if (follow && selected) {
    const [x, y] = project(selected.position);
    viewBox = `${(x - 330).toFixed(2)} ${(y - 240).toFixed(2)} 660 430`;
  }
  return `<svg class="park-scene" xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" role="img" aria-label="${escapeHtml(t('scene'))}"><title>${escapeHtml(t('scene'))}</title><desc>${escapeHtml(t('hostSceneNote'))} ${escapeHtml(t('mapHint'))}</desc>${body}</svg>`;
}
