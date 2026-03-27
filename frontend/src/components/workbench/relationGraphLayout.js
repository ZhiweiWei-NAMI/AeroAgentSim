import dagre from '@dagrejs/dagre';

export const KIND_ORDER = [
  'agent',
  'component',
  'workflow',
  'task',
  'agent_state',
  'workflow_state',
];

export const KIND_COLOR = {
  agent: '#52e0c4',
  workflow: '#72a8ff',
  component: '#9f8cff',
  task: '#c9a7ff',
  agent_state: '#ffbf5b',
  workflow_state: '#ff8a5b',
};

export const KIND_LABEL = {
  agent: 'Agent',
  component: 'Component',
  workflow: 'Workflow',
  task: 'Task',
  agent_state: 'Agent State',
  workflow_state: 'Workflow State',
};

const DEFAULT_NODE_SIZE = { width: 188, height: 70 };
const FALLBACK_KIND = 'workflow_state';
const LANE_GAP = 92;
const BOUNDS_PADDING = 140;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function kindIndex(kind) {
  const index = KIND_ORDER.indexOf(kind);
  return index >= 0 ? index : KIND_ORDER.length;
}

function normalizeNodeKind(kind) {
  return KIND_COLOR[kind] ? kind : FALLBACK_KIND;
}

function estimateTextWidth(value, factor = 7.2) {
  return String(value || '').trim().length * factor;
}

export function measureNode(node) {
  const kind = normalizeNodeKind(node?.kind);
  const labelWidth = estimateTextWidth(node?.label, 8.2);
  const kindWidth = estimateTextWidth(KIND_LABEL[kind] || kind, 6.4);
  const metadataWidth = estimateTextWidth(node?.metadata?.agent_id || node?.metadata?.workflow_id || node?.id, 5.4);
  const width = clamp(
    Math.max(DEFAULT_NODE_SIZE.width, labelWidth + 78, kindWidth + 78, metadataWidth + 68),
    160,
    280
  );
  const height = kind.includes('state') ? 64 : DEFAULT_NODE_SIZE.height;
  return { width, height };
}

function fallbackPositionByKind(node, indexByKind) {
  const laneX = {
    agent: 160,
    component: 420,
    workflow: 680,
    task: 940,
    agent_state: 1200,
    workflow_state: 1460,
  };
  const kind = normalizeNodeKind(node?.kind);
  const rank = (indexByKind[kind] || 0) + 1;
  indexByKind[kind] = rank;
  return {
    x: laneX[kind] || 900,
    y: 110 + rank * 110,
  };
}

function createLaneCenters(nodes) {
  const presentKinds = KIND_ORDER.filter((kind) => nodes.some((node) => normalizeNodeKind(node.kind) === kind));
  const laneWidths = presentKinds.reduce((result, kind) => {
    const maxWidth = nodes
      .filter((node) => normalizeNodeKind(node.kind) === kind)
      .reduce((width, node) => Math.max(width, measureNode(node).width), DEFAULT_NODE_SIZE.width);
    result[kind] = maxWidth + 52;
    return result;
  }, {});

  const centers = {};
  const lanes = [];
  let cursor = 120;
  presentKinds.forEach((kind) => {
    const width = laneWidths[kind] || DEFAULT_NODE_SIZE.width;
    const left = cursor;
    const center = left + width / 2;
    centers[kind] = center;
    lanes.push({
      kind,
      label: KIND_LABEL[kind] || kind,
      center,
      left,
      right: left + width,
      width,
    });
    cursor += width + LANE_GAP;
  });

  return { centers, lanes };
}

export function buildRelationGraphLayout(graph, manualPositions = {}) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  if (!nodes.length) {
    return {
      nodes: [],
      edges: [],
      lanes: [],
      bounds: { x: 0, y: 0, width: 1320, height: 760 },
    };
  }

  const dagreGraph = new dagre.graphlib.Graph({ multigraph: true });
  dagreGraph.setGraph({
    rankdir: 'LR',
    ranksep: 84,
    nodesep: 42,
    edgesep: 28,
    acyclicer: 'greedy',
    ranker: 'network-simplex',
    marginx: 48,
    marginy: 52,
  });
  dagreGraph.setDefaultEdgeLabel(() => ({}));

  const laneAnchors = KIND_ORDER.filter((kind) => nodes.some((node) => normalizeNodeKind(node.kind) === kind));
  laneAnchors.forEach((kind) => {
    dagreGraph.setNode(`__lane:${kind}`, { width: 2, height: 2, anchor: true });
  });
  laneAnchors.forEach((kind, index) => {
    const nextKind = laneAnchors[index + 1];
    if (nextKind) {
      dagreGraph.setEdge(`__lane:${kind}`, `__lane:${nextKind}`, {
        weight: 4,
        minlen: 1,
        anchor: true,
      });
    }
  });

  nodes.forEach((node, index) => {
    const kind = normalizeNodeKind(node.kind);
    const size = measureNode(node);
    dagreGraph.setNode(node.id, {
      ...size,
      kind,
      order: index,
    });
    if (laneAnchors.includes(kind)) {
      dagreGraph.setEdge(`__lane:${kind}`, node.id, {
        weight: 2,
        minlen: 0,
        anchor: true,
      });
    }
  });

  edges.forEach((edge, index) => {
    if (!edge?.source || !edge?.target) {
      return;
    }
    dagreGraph.setEdge(edge.source, edge.target, {
      weight: 1 + (edge.label ? 1 : 0),
      minlen: 1,
      index,
    });
  });

  try {
    dagre.layout(dagreGraph);
  } catch (_error) {
    const indexByKind = {};
    const positionedNodes = nodes.map((node) => {
      const size = measureNode(node);
      const manual = manualPositions[node.id];
      const position = manual || fallbackPositionByKind(node, indexByKind);
      return {
        ...node,
        kind: normalizeNodeKind(node.kind),
        position,
        size,
      };
    });
    return finalizeLayout(positionedNodes, edges);
  }

  const { centers, lanes } = createLaneCenters(nodes);
  const positionedNodes = nodes
    .map((node) => {
      const dagreNode = dagreGraph.node(node.id);
      const kind = normalizeNodeKind(node.kind);
      const size = measureNode(node);
      const manual = manualPositions[node.id];
      const position = manual || {
        x: centers[kind] || dagreNode?.x || 160,
        y: dagreNode?.y || 140,
      };
      return {
        ...node,
        kind,
        position,
        size,
      };
    })
    .sort((left, right) => {
      const kindGap = kindIndex(left.kind) - kindIndex(right.kind);
      if (kindGap !== 0) {
        return kindGap;
      }
      return left.position.y - right.position.y;
    });

  return finalizeLayout(positionedNodes, edges, lanes);
}

function finalizeLayout(positionedNodes, rawEdges, incomingLanes = []) {
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node]));
  const lanes =
    incomingLanes.length > 0
      ? incomingLanes.map((lane) => ({
          ...lane,
          top: Infinity,
          bottom: -Infinity,
        }))
      : [];

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  positionedNodes.forEach((node) => {
    const width = node.size?.width || DEFAULT_NODE_SIZE.width;
    const height = node.size?.height || DEFAULT_NODE_SIZE.height;
    minX = Math.min(minX, node.position.x - width / 2);
    minY = Math.min(minY, node.position.y - height / 2);
    maxX = Math.max(maxX, node.position.x + width / 2);
    maxY = Math.max(maxY, node.position.y + height / 2);
    const lane = lanes.find((item) => item.kind === node.kind);
    if (lane) {
      lane.top = Math.min(lane.top, node.position.y - height / 2 - 32);
      lane.bottom = Math.max(lane.bottom, node.position.y + height / 2 + 32);
    }
  });

  const resolvedLanes = lanes.map((lane) => ({
    ...lane,
    top: Number.isFinite(lane.top) ? lane.top : minY - 56,
    bottom: Number.isFinite(lane.bottom) ? lane.bottom : maxY + 56,
  }));

  const edges = rawEdges
    .map((edge) => {
      const source = nodeById.get(edge.source);
      const target = nodeById.get(edge.target);
      if (!source || !target) {
        return null;
      }
      return {
        ...edge,
        geometry: buildEdgeGeometry(source, target),
      };
    })
    .filter(Boolean);

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
    return {
      nodes: positionedNodes,
      edges,
      lanes: resolvedLanes,
      bounds: { x: 0, y: 0, width: 1320, height: 760 },
    };
  }

  return {
    nodes: positionedNodes,
    edges,
    lanes: resolvedLanes,
    bounds: {
      x: minX - BOUNDS_PADDING,
      y: minY - BOUNDS_PADDING,
      width: Math.max(maxX - minX + BOUNDS_PADDING * 2, 760),
      height: Math.max(maxY - minY + BOUNDS_PADDING * 2, 520),
    },
  };
}

function buildEdgeGeometry(source, target) {
  const sourceWidth = source.size?.width || DEFAULT_NODE_SIZE.width;
  const targetWidth = target.size?.width || DEFAULT_NODE_SIZE.width;
  const deltaX = target.position.x - source.position.x;
  const direction = deltaX >= 0 ? 1 : -1;
  const startX = source.position.x + (sourceWidth / 2) * direction;
  const endX = target.position.x - (targetWidth / 2) * direction;
  const startY = source.position.y;
  const endY = target.position.y;
  const controlOffset = clamp(Math.abs(endX - startX) * 0.34, 52, 140);
  const controlOneX = startX + controlOffset * direction;
  const controlTwoX = endX - controlOffset * direction;
  const path = `M ${startX} ${startY} C ${controlOneX} ${startY}, ${controlTwoX} ${endY}, ${endX} ${endY}`;

  const labelX =
    ((startX * 0.125) +
      (controlOneX * 0.375) +
      (controlTwoX * 0.375) +
      (endX * 0.125));
  const labelY =
    ((startY * 0.125) +
      (startY * 0.375) +
      (endY * 0.375) +
      (endY * 0.125)) - 18;

  return {
    path,
    labelX,
    labelY,
    startX,
    startY,
    endX,
    endY,
  };
}
