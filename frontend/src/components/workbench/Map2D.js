import React, { useMemo } from 'react';
import { MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { dedupeLatLngPoints, isValidLatLng, sanitizeLatLngPoints, shouldUsePointViewport, toLatLng } from './map2dUtils';

function buildStatusIcon(status, isSelected = false) {
  const level = status === 'running' ? 'running' : status === 'error' ? 'error' : 'idle';
  return L.divIcon({
    className: 'map-marker-shell',
    html: `<div class="map-marker map-marker-${level} ${isSelected ? 'map-marker-selected' : ''}"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

function FitToContent({ markerLatLng, trajectoryLatLng, mode }) {
  const map = useMap();

  React.useEffect(() => {
    const points = [];
    markerLatLng.forEach((point) => {
      if (isValidLatLng(point)) {
        points.push(point);
      }
    });
    trajectoryLatLng.forEach((line) => {
      sanitizeLatLngPoints(line).forEach((point) => points.push(point));
    });

    if (!points.length) {
      return;
    }

    const viewportPoints = dedupeLatLngPoints(points);
    if (shouldUsePointViewport(viewportPoints)) {
      map.stop();
      map.setView(viewportPoints[0], mode === 'geo_osm' ? 13 : 2, { animate: false });
      return;
    }

    const bounds = L.latLngBounds(viewportPoints);
    if (!bounds.isValid()) {
      map.stop();
      map.setView(viewportPoints[0], mode === 'geo_osm' ? 13 : 2, { animate: false });
      return;
    }

    map.stop();
    map.fitBounds(bounds, { padding: [24, 24], animate: false });
  }, [map, markerLatLng, trajectoryLatLng, mode]);

  return null;
}

function Map2D({
  mode = 'simulation_plane',
  markers = [],
  trajectories = [],
  height = 360,
  selectedMarkerId,
  onSelectMarker,
}) {
  const markerLatLng = useMemo(
    () => markers.map((marker) => toLatLng(marker.position, mode)),
    [markers, mode]
  );

  const trajectoryLatLng = useMemo(
    () =>
      trajectories.map((trajectory) =>
        (trajectory.points || [])
          .map((point) => toLatLng(point, mode))
          .filter(isValidLatLng)
      ),
    [trajectories, mode]
  );

  const center = useMemo(() => {
    const firstPoint = markerLatLng.find(Boolean);
    if (firstPoint) {
      return firstPoint;
    }
    return mode === 'geo_osm' ? [39.9042, 116.4074] : [180, 180];
  }, [markerLatLng, mode]);

  const mapCrs = mode === 'geo_osm' ? L.CRS.EPSG3857 : L.CRS.Simple;

  return (
    <div className="map2d-shell" style={{ height }}>
      <MapContainer
        center={center}
        zoom={mode === 'geo_osm' ? 12 : 1}
        crs={mapCrs}
        className="map2d"
      >
        {mode === 'geo_osm' ? (
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="&copy; OpenStreetMap contributors"
          />
        ) : null}

        {markers.map((marker, index) => {
          const markerPos = markerLatLng[index];
          if (!markerPos) {
            return null;
          }
          const z = marker.position?.z ?? marker.position?.alt ?? marker.position?.altitude ?? 0;
          return (
            <Marker
              key={marker.id || `${marker.type}_${index}`}
              position={markerPos}
              icon={buildStatusIcon(marker.status, marker.id === selectedMarkerId)}
              eventHandlers={
                onSelectMarker
                  ? {
                      click: () => onSelectMarker(marker),
                    }
                  : undefined
              }
            >
              <Tooltip direction="top" offset={[0, -8]} opacity={0.95}>
                {marker.id}
              </Tooltip>
              <Popup>
                <div className="map-popup">
                  <div><strong>{marker.id}</strong></div>
                  <div>Type: {marker.type || 'unknown'}</div>
                  <div>Status: {marker.status || 'idle'}</div>
                  <div>Workflow: {marker.workflow_id || '-'}</div>
                  <div>Task: {marker.task_id || '-'}</div>
                  <div>Z: {z}</div>
                </div>
              </Popup>
            </Marker>
          );
        })}

        {trajectoryLatLng.map((line, index) =>
          line.length >= 2 ? (
            <Polyline key={`line_${index}`} positions={line} pathOptions={{ color: '#5aa9ff', weight: 2 }} />
          ) : null
        )}

        <FitToContent markerLatLng={markerLatLng} trajectoryLatLng={trajectoryLatLng} mode={mode} />
      </MapContainer>
    </div>
  );
}

export default Map2D;
