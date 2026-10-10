import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Space, Tooltip, Typography } from 'antd';
import { DragOutlined, MinusOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';

import { useI18n } from '../../i18n/I18nProvider';
import {
  buildRelationGraphLayout,
  KIND_COLOR,
  KIND_LABEL,
} from './relationGraphLayout';

const { Text } = Typography;

function computeBaseViewBox(bounds) {
  if (!bounds) {
    return { x: 0, y: 0, width: 1320, height: 760 };
  }
  return {
    x: bounds.x,
    y: bounds.y,
    width: Math.max(bounds.width, 420),
    height: Math.max(bounds.height, 320),
  };
}

function estimateLabelWidth(value) {
  return Math.max(String(value || '').trim().length * 6.6 + 24, 48);
}

function RelationGraph({ graph, selectedNodeId, onNodeSelect }) {
  const { t } = useI18n();
  const canvasRef = useRef(null);
  const dragStateRef = useRef(null);
  const suppressClickRef = useRef('');

  const graphSignature = useMemo(() => {
    const nodeIds = (graph?.nodes || []).map((node) => node.id).join('|');
    const edgeIds = (graph?.edges || []).map((edge) => edge.id).join('|');
    return `${nodeIds}::${edgeIds}`;
  }, [graph]);

  const [manualPositions, setManualPositions] = useState({});
  const [viewport, setViewport] = useState({ x: 0, y: 0, width: 1320, height: 760 });
  const [interactionMode, setInteractionMode] = useState('idle');

  const autoLayout = useMemo(
    () => buildRelationGraphLayout(graph || { nodes: [], edges: [] }, {}),
    [graph]
  );
  const layout = useMemo(
    () => buildRelationGraphLayout(graph || { nodes: [], edges: [] }, manualPositions),
    [graph, manualPositions]
  );
  const referenceViewBox = useMemo(
    () => computeBaseViewBox(autoLayout.bounds),
    [autoLayout.bounds]
  );
  const criticalPath = useMemo(() => new Set(graph?.critical_path || []), [graph]);

  useEffect(() => {
    setManualPositions({});
    setViewport(referenceViewBox);
  }, [graphSignature, referenceViewBox]);

  const zoomViewport = useCallback(
    (factor, origin = { x: 0.5, y: 0.5 }) => {
      setViewport((current) => {
        const nextWidth = Math.min(
          Math.max(current.width * factor, referenceViewBox.width * 0.32),
          referenceViewBox.width * 4
        );
        const nextHeight = Math.min(
          Math.max(current.height * factor, referenceViewBox.height * 0.32),
          referenceViewBox.height * 4
        );
        const deltaWidth = current.width - nextWidth;
        const deltaHeight = current.height - nextHeight;
        return {
          x: current.x + deltaWidth * origin.x,
          y: current.y + deltaHeight * origin.y,
          width: nextWidth,
          height: nextHeight,
        };
      });
    },
    [referenceViewBox.height, referenceViewBox.width]
  );

  const resetViewport = useCallback(() => {
    setViewport(referenceViewBox);
  }, [referenceViewBox]);

  const resetLayout = useCallback(() => {
    setManualPositions({});
    setViewport(referenceViewBox);
  }, [referenceViewBox]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return undefined;
    }

    const handleWheel = (event) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) {
        return;
      }
      const origin = {
        x: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
        y: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
      };
      zoomViewport(event.deltaY > 0 ? 1.12 : 0.88, origin);
    };

    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => {
      canvas.removeEventListener('wheel', handleWheel);
    };
  }, [zoomViewport]);

  const zoomPercent = useMemo(
    () => Math.round((referenceViewBox.width / Math.max(viewport.width, 1)) * 100),
    [referenceViewBox.width, viewport.width]
  );

  const beginCanvasPan = useCallback(
    (event) => {
      if (event.target?.closest?.('[data-node-root="true"]')) {
        return;
      }
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      dragStateRef.current = {
        mode: 'pan',
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        viewport: { ...viewport },
      };
      setInteractionMode('pan');
      canvas.setPointerCapture?.(event.pointerId);
    },
    [viewport]
  );

  const beginNodeDrag = useCallback((event, node) => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragStateRef.current = {
      mode: 'node',
      pointerId: event.pointerId,
      nodeId: node.id,
      clientX: event.clientX,
      clientY: event.clientY,
      origin: { ...node.position },
      viewport: { ...viewport },
      moved: false,
    };
    setInteractionMode('node');
    canvas.setPointerCapture?.(event.pointerId);
  }, [viewport]);

  const onPointerMove = useCallback((event) => {
    const dragState = dragStateRef.current;
    const canvas = canvasRef.current;
    if (!dragState || !canvas || dragState.pointerId !== event.pointerId) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }
    const deltaX = event.clientX - dragState.clientX;
    const deltaY = event.clientY - dragState.clientY;

    if (dragState.mode === 'pan') {
      setViewport({
        x: dragState.viewport.x - (deltaX * dragState.viewport.width) / rect.width,
        y: dragState.viewport.y - (deltaY * dragState.viewport.height) / rect.height,
        width: dragState.viewport.width,
        height: dragState.viewport.height,
      });
      return;
    }

    const graphDeltaX = (deltaX * dragState.viewport.width) / rect.width;
    const graphDeltaY = (deltaY * dragState.viewport.height) / rect.height;
    if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) {
      dragState.moved = true;
    }
    setManualPositions((current) => ({
      ...current,
      [dragState.nodeId]: {
        x: dragState.origin.x + graphDeltaX,
        y: dragState.origin.y + graphDeltaY,
      },
    }));
  }, []);

  const stopDragging = useCallback((event) => {
    const canvas = canvasRef.current;
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    canvas?.releasePointerCapture?.(event.pointerId);
    if (dragState.mode === 'node' && dragState.moved) {
      suppressClickRef.current = dragState.nodeId;
    }
    dragStateRef.current = null;
    setInteractionMode('idle');
  }, []);

  const handleNodeActivate = useCallback((nodeId) => {
    if (suppressClickRef.current === nodeId) {
      suppressClickRef.current = '';
      return;
    }
    onNodeSelect?.(nodeId);
  }, [onNodeSelect]);

  const viewBox = `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;
  const presentKinds = layout.lanes.map((lane) => lane.kind);

  return (
    <div className="relation-graph-shell">
      <div className="relation-graph-toolbar">
        <div className="relation-graph-toolbar-main">
          <div className="relation-graph-legend">
            {presentKinds.map((kind) => (
              <span key={kind} className="relation-graph-legend-item">
                <span
                  className="relation-graph-legend-dot"
                  style={{ backgroundColor: KIND_COLOR[kind] }}
                />
                {KIND_LABEL[kind] || kind}
              </span>
            ))}
          </div>
          <div className="relation-graph-stats">
            <span className="relation-graph-stat-pill">{`${layout.nodes.length} ${t('nodes') || 'nodes'}`}</span>
            <span className="relation-graph-stat-pill">{`${layout.edges.length} ${t('edges') || 'edges'}`}</span>
            <span className="relation-graph-stat-pill">{`${Object.keys(manualPositions).length} ${t('graphManualAdjustments')}`}</span>
          </div>
        </div>
        <Space wrap size={8}>
          <Text type="secondary" className="relation-graph-hint">
            <DragOutlined /> {t('graphPanHint')}
          </Text>
          <Space.Compact>
            <Tooltip title={t('graphZoomOut')}>
              <Button size="small" icon={<MinusOutlined />} onClick={() => zoomViewport(1.12)} />
            </Tooltip>
            <Button size="small" onClick={resetViewport}>
              {zoomPercent}%
            </Button>
            <Tooltip title={t('graphZoomIn')}>
              <Button size="small" icon={<PlusOutlined />} onClick={() => zoomViewport(0.88)} />
            </Tooltip>
          </Space.Compact>
          <Tooltip title={t('graphResetLayout')}>
            <Button size="small" onClick={resetLayout}>
              {t('graphAutoLayout')}
            </Button>
          </Tooltip>
          <Tooltip title={t('graphResetView')}>
            <Button size="small" icon={<ReloadOutlined />} onClick={resetViewport} />
          </Tooltip>
        </Space>
      </div>

      <div
        ref={canvasRef}
        className={`relation-graph-canvas relation-graph-canvas-${interactionMode}`}
        onPointerDown={beginCanvasPan}
        onPointerMove={onPointerMove}
        onPointerUp={stopDragging}
        onPointerLeave={stopDragging}
      >
        <svg
          className="relation-graph"
          viewBox={viewBox}
          role="img"
          aria-label="Workflow-Agent-State relation graph"
        >
          <defs>
            <pattern id="relation-grid" width="36" height="36" patternUnits="userSpaceOnUse">
              <path d="M 36 0 L 0 0 0 36" fill="none" stroke="rgba(148, 163, 184, 0.12)" strokeWidth="1" />
            </pattern>
            <linearGradient id="relation-surface" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#071221" />
              <stop offset="100%" stopColor="#0b1729" />
            </linearGradient>
            <filter id="relation-node-shadow" x="-20%" y="-20%" width="140%" height="160%">
              <feDropShadow dx="0" dy="14" stdDeviation="12" floodColor="rgba(2, 8, 23, 0.28)" />
            </filter>
            <marker
              id="relation-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
              markerUnits="strokeWidth"
            >
              <path d="M0,0 L8,4 L0,8 z" fill="#93a6c7" />
            </marker>
          </defs>

          <rect
            x={layout.bounds.x}
            y={layout.bounds.y}
            width={layout.bounds.width}
            height={layout.bounds.height}
            fill="url(#relation-surface)"
            rx="28"
          />
          <rect
            x={layout.bounds.x}
            y={layout.bounds.y}
            width={layout.bounds.width}
            height={layout.bounds.height}
            fill="url(#relation-grid)"
            rx="28"
            opacity="0.85"
          />

          {layout.lanes.map((lane) => (
            <g key={lane.kind} className="relation-graph-lane">
              <rect
                x={lane.left - 18}
                y={lane.top}
                width={lane.width + 36}
                height={lane.bottom - lane.top}
                rx="24"
                fill={KIND_COLOR[lane.kind] || '#94a3b8'}
                fillOpacity="0.08"
                stroke={KIND_COLOR[lane.kind] || '#94a3b8'}
                strokeOpacity="0.16"
              />
              <text
                x={lane.center}
                y={lane.top + 26}
                textAnchor="middle"
                className="relation-lane-label"
              >
                {lane.label}
              </text>
            </g>
          ))}

          {layout.edges.map((edge) => {
            const isCritical =
              criticalPath.has(edge.id) ||
              criticalPath.has(edge.source) ||
              criticalPath.has(edge.target);
            const label = edge.label || edge.relation || '';
            const labelWidth = estimateLabelWidth(label);
            return (
              <g key={edge.id} className="relation-edge-group">
                <path
                  d={edge.geometry.path}
                  fill="none"
                  stroke={isCritical ? '#ff9d5c' : '#88a1c7'}
                  strokeWidth={isCritical ? 3 : 2}
                  strokeOpacity={isCritical ? 0.96 : 0.58}
                  markerEnd="url(#relation-arrow)"
                />
                {label ? (
                  <>
                    <rect
                      x={edge.geometry.labelX - labelWidth / 2}
                      y={edge.geometry.labelY - 14}
                      width={labelWidth}
                      height="24"
                      rx="12"
                      fill="rgba(8, 18, 33, 0.88)"
                      stroke="rgba(136, 161, 199, 0.24)"
                    />
                    <text
                      x={edge.geometry.labelX}
                      y={edge.geometry.labelY + 2}
                      textAnchor="middle"
                      className="relation-edge-label"
                    >
                      {label}
                    </text>
                  </>
                ) : null}
              </g>
            );
          })}

          {layout.nodes.map((node) => {
            const isSelected = selectedNodeId === node.id;
            const isCriticalNode = criticalPath.has(node.id);
            const fill = KIND_COLOR[node.kind] || '#94a3b8';
            const width = node.size?.width || 188;
            const height = node.size?.height || 70;
            const left = node.position.x - width / 2;
            const top = node.position.y - height / 2;
            const metadataId =
              node.metadata?.agent_id ||
              node.metadata?.workflow_id ||
              node.metadata?.task_ref?.definition_id ||
              node.id;

            return (
              <g
                key={node.id}
                data-node-root="true"
                role="button"
                tabIndex={0}
                className="relation-node-group"
                onPointerDown={(event) => beginNodeDrag(event, node)}
                onClick={() => handleNodeActivate(node.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    handleNodeActivate(node.id);
                  }
                }}
              >
                <title>{`${node.label} (${node.kind})`}</title>
                {(isSelected || isCriticalNode) ? (
                  <rect
                    x={left - 8}
                    y={top - 8}
                    width={width + 16}
                    height={height + 16}
                    rx="24"
                    fill="none"
                    stroke={isSelected ? '#ffd06f' : '#ff9d5c'}
                    strokeWidth="2.5"
                    strokeOpacity="0.95"
                  />
                ) : null}
                <rect
                  x={left}
                  y={top}
                  width={width}
                  height={height}
                  rx="20"
                  fill="rgba(8, 18, 33, 0.94)"
                  stroke={fill}
                  strokeWidth={isSelected || isCriticalNode ? 2.2 : 1.4}
                  strokeOpacity={isSelected || isCriticalNode ? 1 : 0.56}
                  filter="url(#relation-node-shadow)"
                />
                <rect
                  x={left + 14}
                  y={top + 12}
                  width={Math.min(width - 28, 72)}
                  height="18"
                  rx="9"
                  fill={fill}
                  fillOpacity="0.18"
                />
                <text x={left + 24} y={top + 25} className="relation-node-chip">
                  {KIND_LABEL[node.kind] || node.kind}
                </text>
                <text
                  x={node.position.x}
                  y={top + 46}
                  className="relation-node-label"
                  textAnchor="middle"
                >
                  {node.label}
                </text>
                <text
                  x={node.position.x}
                  y={top + 64}
                  className="relation-node-kind"
                  textAnchor="middle"
                >
                  {metadataId}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

export default RelationGraph;
