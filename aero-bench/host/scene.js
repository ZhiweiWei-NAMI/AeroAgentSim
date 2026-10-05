// Owned host scene renderer. Attribution: this module is adapted from the P02
// parcel prototype (validation/p02-parcel-host/source @
// 15f473a4dc0ed4f80e3000b0acef6e875c617393, file src/scene.js, Apache-2.0 per the checkout's LICENSE
// the checkout's LICENSE). The prototype's static authored park — its fixed
// stations, buildings, trees, roads and "SMART PARK" caption — is intentionally
// NOT reused: host-supplied geometry alone is drawn. The drawing helpers that
// remain (projection, isometric box, path, pick, marker, label) come from the
// prototype with attribution.
import { escapeHtml, h, project, isoBox } from './adapter.js';

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
  return `<g pointer-events="none"><ellipse cx="${x.toFixed(2)}" cy="${(y + 4).toFixed(2)}" rx="22" ry="9" fill="none" stroke="#e19228" stroke-width="2"/><path d="M${x.toFixed(2)} ${(y - 22).toFixed(2)}v-16" stroke="#bd761c" stroke-width="2"/><circle cx="${x.toFixed(2)}" cy="${(y - 42).toFixed(2)}" r="5" fill="#e8a13b" stroke="#fff" stroke-width="2"/></g>`;
}

function label(position, title, subtitle = '', color = '#425363') {
  const [x, y] = project(position);
  return `<g class="scene-label" pointer-events="none"><text x="${x.toFixed(2)}" y="${y.toFixed(2)}" text-anchor="middle" fill="${color}" font-size="14" font-weight="650">${escapeHtml(title)}</text>${subtitle ? `<text x="${x.toFixed(2)}" y="${(y + 18).toFixed(2)}" text-anchor="middle" fill="#6e7c89" font-size="10">${escapeHtml(subtitle)}</text>` : ''}</g>`;
}

function pick(id, body, selected) {
  return `<g data-entity-id="${escapeHtml(id)}" class="scene-object ${selected === id ? 'selected' : ''}" role="img" aria-label="${escapeHtml(id)}">${body}</g>`;
}

/** Empty ground plane: neutral grid only, no authored park content. */
function hostGround() {
  let s = `<defs><pattern id="host-grid" width="36" height="36" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#bdc7cf"/></pattern></defs>`;
  s += `<rect width="1100" height="680" fill="#eef2f3"/>`;
  s += `<rect width="1100" height="680" fill="url(#host-grid)" opacity=".33"/>`;
  s += `<g class="scene-compass" transform="translate(44 602)"><path d="M0 24v-24l-4 9m4-9 4 9M0 24h28l-9-4m9 4-9 4" stroke="#8a9eaa" stroke-width="1.5" fill="none"/><text x="-4" y="-8" font-size="11" fill="#667c8b">N</text><text x="33" y="29" font-size="11" fill="#667c8b">E</text></g>`;
  return s;
}

function stationGlyph(station, selected) {
  const [e, n, u] = station.position;
  let body = `<ellipse cx="${project([e, n, 0])[0].toFixed(2)}" cy="${project([e, n, 0])[1].toFixed(2)}" rx="17" ry="7" fill="#59778228"/>`;
  body += box(e - 2.2, n - 2.2, u, 4.4, 4.4, 1.6, ['#c7d6dc', '#9fb4bd', '#8aa3ad']);
  body += marker([e, n, (u ?? 0) + 2.2], station.id, selected);
  body += `<text x="${project([e, n, u + 3])[0].toFixed(2)}" y="${(project([e, n, u + 3])[1] + 4).toFixed(2)}" text-anchor="middle" font-size="11" fill="#496579">${escapeHtml(station.label)}</text>`;
  return pick(station.id, body, selected);
}

function vehicleGlyph(entity, selected, kind) {
  const [e, n, u] = entity.position;
  let body = '';
  const [sx, sy] = project([e, n, 0]);
  body += `<ellipse cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" rx="${kind === 'uav' ? 20 : 17}" ry="7" fill="#59778228"/>`;
  if (kind === 'ugv') {
    body += box(e - 2.5, n - 1.5, Math.max(u - 0.5, 0), 5, 3, 0.85, ['#e8edef', '#7796a6', '#9eafb9']);
    body += box(e - 2.5, n - 1.5, Math.max(u - 0.5, 0) + 0.85, 1.3, 3, 1.4, ['#d5e0e5', '#668ba0', '#acc1cd']);
    for (const offset of [-1.7, 1.5]) {
      const [x, y] = project([e + offset, n - 1.5, 0.5]);
      body += `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="#34495a"/><circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2" fill="#7897a9"/>`;
    }
  } else if (kind === 'uav') {
    const [x, y] = project(entity.position);
    body += `<path d="M${sx.toFixed(2)} ${sy.toFixed(2)}L${x.toFixed(2)} ${y.toFixed(2)}" stroke="#8da8b2" stroke-width="1" stroke-dasharray="3 5"/>`;
    for (const [de, dn] of [[-3, -2], [-3, 2], [3, -2], [3, 2]]) {
      const [a, b] = project([e + de, n + dn, u]);
      body += `<path d="M${x.toFixed(2)} ${y.toFixed(2)}L${a.toFixed(2)} ${b.toFixed(2)}" stroke="#536b7a" stroke-width="4"/><ellipse cx="${a.toFixed(2)}" cy="${b.toFixed(2)}" rx="12" ry="4.8" fill="#91acb06b" stroke="#3d6978" stroke-width="1.6"/>`;
    }
    body += box(e - 1.2, n - 1, Math.max(u - 0.25, 0), 2.4, 2, 0.65, ['#eff5f4', '#75949d', '#a0b9c0']);
  } else {
    // Facility kind without a specialised glyph: an explicit host marker.
    body += `<circle cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" r="6" fill="#4c8c9d" stroke="#fff" stroke-width="1.5"/>`;
  }
  body += marker(entity.position, entity.id, selected);
  const [lx, ly] = project([e, n, u]);
  body += `<text x="${lx.toFixed(2)}" y="${(ly + 29).toFixed(2)}" text-anchor="middle" font-size="11" fill="#496579">${escapeHtml(entity.label ?? entity.id)}</text>`;
  return pick(entity.id, body, selected);
}

function parcelGlyph(parcel, selected) {
  const [e, n, u] = parcel.position;
  const inactive = parcel.custodyKnown === false || parcel.state === 'unknown';
  let body = box(e - 0.8, n - 0.7, u, 1.6, 1.4, 1.25, inactive ? ['#d2b895', '#bda180', '#ac9070'] : ['#facb7d', '#dfaa55', '#c58b3d']);
  const [x, y] = project([e, n, u + 1.25]);
  body += `<path d="M${(x - 5).toFixed(2)} ${(y - 1).toFixed(2)}l9 -3" stroke="#96703c" stroke-width="2"/>`;
  body += marker([e, n, u + 1.7], parcel.id, selected);
  body += `<g pointer-events="none"><rect x="${(x + 12).toFixed(2)}" y="${(y - 27).toFixed(2)}" width="${Math.max(66, String(parcel.label).length * 8)}" height="21" rx="5" fill="#203546"/><text x="${(x + 12 + Math.max(66, String(parcel.label).length * 8) / 2).toFixed(2)}" y="${(y - 12).toFixed(2)}" text-anchor="middle" fill="#fff1db" font-size="11" font-weight="600">${escapeHtml(parcel.label)}</text><path d="M${(x + 13).toFixed(2)} ${(y - 8).toFixed(2)}l-9 8" stroke="#203546" stroke-width="1.5"/></g>`;
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
      body += `<path d="${path(frame.routes[key])}" fill="none" stroke="#6eacb3" stroke-width="2" stroke-dasharray="6 6" opacity=".55"/>`;
    }
    if (frame.activeRoute && frame.routes[frame.activeRoute]) {
      body += `<path d="${path(frame.routes[frame.activeRoute])}" fill="none" stroke="#4e9a83" stroke-width="3" stroke-dasharray="7 5" opacity=".85"/>`;
    }
  }
  for (const entity of drawableEntities.filter(e => e.kind === 'station')) {
    body += stationGlyph(entity, selectedId);
  }
  for (const entity of drawableEntities.filter(e => e.kind !== 'station')) {
    body += vehicleGlyph(entity, selectedId, entityKind(entity.id));
  }
  for (const parcel of frame.parcels.filter(p2 => p2.position !== null)) {
    body += parcelGlyph(parcel, selectedId);
  }
  // Explicitly report declared-but-unplaced parcels on the canvas.
  for (const id of frame.unresolvedParcelIds ?? []) {
    const [x, y] = [80, 46 + 22 * (frame.unresolvedParcelIds.indexOf(id) + 1)];
    body += `<g pointer-events="none" class="unresolved-parcel"><rect x="${x - 12}" y="${y - 13}" width="150" height="18" rx="4" fill="#f3e8d8" stroke="#bda180"/><text x="${x - 4}" y="${y}" font-size="10" fill="#7c5f36">${escapeHtml(t('parcelUnknown'))} · ${escapeHtml(id)}</text></g>`;
  }
  const selected = [...frame.parcels, ...frame.entities].find(e => e.id === selectedId && e.position !== null);
  let viewBox = '-10 30 1100 650';
  if (follow && selected) {
    const [x, y] = project(selected.position);
    viewBox = `${(x - 330).toFixed(2)} ${(y - 240).toFixed(2)} 660 430`;
  }
  return `<svg class="park-scene" xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" role="img" aria-label="${escapeHtml(t('scene'))}"><title>${escapeHtml(t('scene'))}</title><desc>${escapeHtml(t('hostSceneNote'))} ${escapeHtml(t('mapHint'))}</desc>${body}</svg>`;
}
