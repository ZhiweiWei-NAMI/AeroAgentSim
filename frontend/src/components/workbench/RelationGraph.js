import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Space, Tooltip, Typography } from 'antd';
import { DragOutlined, MinusOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useI18n } from '../../i18n/I18nProvider';

const { Text } = Typography;

const KIND_COLOR = {
  agent: '#2dd4bf',
  workflow: '#60a5fa',
  component: '#8b5cf6',
  task: '#a78bfa',
  agent_state: '#f59e0b',
  workflow_state: '#f97316',
};

const KIND_LABEL = {
  agent: 'Agent',
  component: 'Component',
  workflow: 'Workflow',
  task: 'Task',
  agent_state: 'Agent State',
  workflow_state: 'Workflow State',
};

function fallbackPositionByKind(node, indexByKind) {
  const laneX = {
    agent: 120,
    component: 340,
    workflow: 560,
    task: 780,
    agent_state: 960,
    workflow_state: 1160,
  };
  const kind = node.kind || 'workflow_state';
  const rank = (indexByKind[kind] || 0) + 1;
  indexByKind[kind] = rank;
  return {
    x: laneX[kind] || 900,
    y: 70 + rank * 110,
  };
}

function buildLayout(graph) {
  const indexByKind = {};
  const positionById = {};

  (graph.nodes || []).forEach((node) => {
    const fallback = fallbackPositionByKind(node, indexByKind);
    const position = node.position || fallback;
    positionById[node.id] = position;
  });

  return positionById;
}

function computeBaseViewBox(positionById) {
  const positions = Object.values(positionById);
  if (!positions.length) {
    return { x: 0, y: 0, width: 1320, height: 760 };
  }
  const pad = 100;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  positions.forEach(({ x, y }) => {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  });
  return {
    x: minX - pad,
    y: minY - pad,
    width: Math.max(maxX - minX + pad * 2, 400),
    height: Math.max(maxY - minY + pad * 2, 300),
  };
}

function RelationGraph({ graph, selectedNodeId, onNodeSelect }) {
  const { t } = useI18n();
  const svgRef = useRef(null);
  const canvasRef = useRef(null);
  const dragStateRef = useRef(null);
  const positionById = useMemo(() => buildLayout(graph || { nodes: [], edges: [] }), [graph]);
  const baseViewBox = useMemo(() => computeBaseViewBox(positionById), [positionById]);
  const criticalPath = useMemo(() => new Set(graph?.critical_path || []), [graph]);
  const edges = graph?.edges || [];
  const nodes = graph?.nodes || [];
  const [viewport, setViewport] = useState(baseViewBox);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    setViewport(baseViewBox);
  }, [baseViewBox]);

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
  }, [baseViewBox.height, baseViewBox.width]);

  const presentKinds = useMemo(() => {
    const kinds = new Set(nodes.map((n) => n.kind));
    return Object.keys(KIND_COLOR).filter((k) => kinds.has(k));
  }, [nodes]);

  const zoomPercent = useMemo(
    () => Math.round((baseViewBox.width / Math.max(viewport.width, 1)) * 100),
    [baseViewBox.width, viewport.width]
  );

  const zoomViewport = (factor, origin = { x: 0.5, y: 0.5 }) => {
    setViewport((current) => {
      const nextWidth = Math.min(Math.max(current.width * factor, baseViewBox.width * 0.28), baseViewBox.width * 4);
      const nextHeight = Math.min(
        Math.max(current.height * factor, baseViewBox.height * 0.28),
        baseViewBox.height * 4
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
  };

  const resetViewport = () => {
    setViewport(baseViewBox);
  };

  const onPointerDown = (event) => {
    if (event.target?.closest?.('[data-node="true"]')) {
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dragStateRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      viewport,
    };
    setIsDragging(true);
    canvas.setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event) => {
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
    setViewport({
      x: dragState.viewport.x - (deltaX * dragState.viewport.width) / rect.width,
      y: dragState.viewport.y - (deltaY * dragState.viewport.height) / rect.height,
      width: dragState.viewport.width,
      height: dragState.viewport.height,
    });
  };

  const stopDragging = (event) => {
    const canvas = canvasRef.current;
    if (dragStateRef.current?.pointerId === event.pointerId) {
      event.preventDefault();
      event.stopPropagation();
      canvas?.releasePointerCapture?.(event.pointerId);
      dragStateRef.current = null;
      setIsDragging(false);
    }
  };

  const viewBox = `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`;

  return (
    <div className="relation-graph-shell">
      <div className="relation-graph-toolbar">
        {presentKinds.length > 0 && (
          <div className="relation-graph-legend">
            {presentKinds.map((kind) => (
              <span key={kind} className="relation-graph-legend-item">
                <span className="relation-graph-legend-dot" style={{ backgroundColor: KIND_COLOR[kind] }} />
                {KIND_LABEL[kind] || kind}
              </span>
            ))}
          </div>
        )}
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
          <Tooltip title={t('graphResetView')}>
            <Button size="small" icon={<ReloadOutlined />} onClick={resetViewport} />
          </Tooltip>
        </Space>
      </div>
      <div
        ref={canvasRef}
        className={`relation-graph-canvas ${isDragging ? 'relation-graph-canvas-dragging' : ''}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={stopDragging}
        onPointerLeave={stopDragging}
      >
        <svg
          ref={svgRef}
          className={`relation-graph ${isDragging ? 'relation-graph-dragging' : ''}`}
          viewBox={viewBox}
          role="img"
          aria-label="Workflow-Agent-State relation graph"
        >
          <defs>
            <marker
              id="relation-arrow"
              markerWidth="6"
              markerHeight="6"
              refX="6"
              refY="3"
              orient="auto"
              markerUnits="strokeWidth"
            >
              <path d="M0,0 L6,3 L0,6 z" fill="#73839f" />
            </marker>
          </defs>

          {edges.map((edge) => {
            const from = positionById[edge.source];
            const to = positionById[edge.target];
            if (!from || !to) {
              return null;
            }
            const isCritical =
              criticalPath.has(edge.id) ||
              criticalPath.has(edge.source) ||
              criticalPath.has(edge.target);
            const midX = (from.x + to.x) / 2;
            const midY = (from.y + to.y) / 2;
            return (
              <g key={edge.id}>
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={isCritical ? '#f97316' : '#73839f'}
                  strokeWidth={isCritical ? 2.5 : 1.5}
                  markerEnd="url(#relation-arrow)"
                  opacity={0.95}
                />
                <text x={midX + 6} y={midY - 6} className="relation-edge-label">
                  {edge.label || edge.relation}
                </text>
              </g>
            );
          })}

          {nodes.map((node) => {
            const pos = positionById[node.id];
            if (!pos) {
              return null;
            }
            const isSelected = selectedNodeId === node.id;
            const isCriticalNode = criticalPath.has(node.id);
            const fill = KIND_COLOR[node.kind] || '#9ca3af';
            return (
              <g
                key={node.id}
                data-node="true"
                role="button"
                tabIndex={0}
                onClick={() => onNodeSelect?.(node.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    onNodeSelect?.(node.id);
                  }
                }}
              >
                <rect
                  x={pos.x - 72}
                  y={pos.y - 22}
                  width={144}
                  height={44}
                  rx={10}
                  fill={fill}
                  fillOpacity={isSelected || isCriticalNode ? 0.3 : 0.16}
                  stroke={isSelected || isCriticalNode ? '#f97316' : fill}
                  strokeWidth={isSelected || isCriticalNode ? 2.4 : 1.5}
                />
                <text x={pos.x} y={pos.y - 2} className="relation-node-label" textAnchor="middle">
                  {node.label}
                </text>
                <text x={pos.x} y={pos.y + 14} className="relation-node-kind" textAnchor="middle">
                  {node.kind}
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
