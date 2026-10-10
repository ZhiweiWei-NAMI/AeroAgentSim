export function toFiniteNumber(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === 'string' && value.trim() === '') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function toLatLng(position, mode) {
  if (!position) {
    return null;
  }

  if (Array.isArray(position) && position.length >= 2) {
    const lat = toFiniteNumber(position[1]);
    const lng = toFiniteNumber(position[0]);
    return lat === null || lng === null ? null : [lat, lng];
  }

  if (mode === 'geo_osm') {
    const lat = toFiniteNumber(position.lat);
    const lng = toFiniteNumber(position.lng);
    return lat === null || lng === null ? null : [lat, lng];
  }

  const lat = toFiniteNumber(position.y);
  const lng = toFiniteNumber(position.x);
  return lat === null || lng === null ? null : [lat, lng];
}

export function isValidLatLng(point) {
  if (!Array.isArray(point) || point.length < 2) {
    return false;
  }
  return Number.isFinite(point[0]) && Number.isFinite(point[1]);
}

export function sanitizeLatLngPoints(points) {
  return (points || []).filter(isValidLatLng);
}

export function dedupeLatLngPoints(points) {
  const seen = new Set();
  const deduped = [];
  sanitizeLatLngPoints(points).forEach((point) => {
    const key = `${point[0].toFixed(6)}:${point[1].toFixed(6)}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    deduped.push(point);
  });
  return deduped;
}

export function shouldUsePointViewport(points) {
  const deduped = dedupeLatLngPoints(points);
  return deduped.length <= 1;
}
