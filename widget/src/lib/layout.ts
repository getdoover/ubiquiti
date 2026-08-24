/**
 * Positions for the diagram.
 *
 * Laid out at the *device* level, not the radio level: dagre (or the serpentine
 * walk below) ranks the Doovits by the RF hops between them, and each Doovit's
 * radios are then stacked inside its box at fixed offsets. Two reasons for that
 * split — a site's radios always want to sit together regardless of what a
 * layout algorithm would prefer, and ranking a dozen boxes is far more stable
 * than ranking two dozen loose nodes, so the diagram does not reshuffle when one
 * radio drops out.
 *
 * A pure chain gets **serpentined** rather than laid out in one line. A ten-site
 * chain is ten box-widths across and one tall, which no screen shows usefully;
 * wrapping it into rows that alternate direction keeps the hop order readable
 * left-to-right, then right-to-left, while fitting a sane aspect ratio. Anything
 * that is not a simple path (a branching AP, a redundant loop) falls back to
 * dagre, which handles those properly and a serpentine cannot.
 */

import dagre from "@dagrejs/dagre";

import type { Link, Radio, Topology } from "./topology";

export const NODE_WIDTH = 176;
export const NODE_HEIGHT = 58;
const NODE_GAP = 16;
const PAD_X = 16;
const PAD_TOP = 34; // room for the device name along the top of the box
const PAD_BOTTOM = 14;

export const GROUP_WIDTH = NODE_WIDTH + PAD_X * 2;

/** Gaps between device boxes. Horizontal is wide enough for an edge's stat pill. */
const COL_GAP = 96;
const ROW_GAP = 72;
/** Space between disconnected components, stacked below one another. */
const COMPONENT_GAP = 96;

export function groupHeight(radioCount: number): number {
  const n = Math.max(1, radioCount);
  return PAD_TOP + n * NODE_HEIGHT + (n - 1) * NODE_GAP + PAD_BOTTOM;
}

export interface FlowNode {
  id: string;
  type: "device" | "radio";
  position: { x: number; y: number };
  data: Record<string, unknown>;
  parentId?: string;
  extent?: "parent";
  draggable?: boolean;
  selectable?: boolean;
  style?: Record<string, string | number>;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  /** Named explicitly: a radio node exposes handles on all four sides, and
   * React Flow warns and picks arbitrarily if an edge does not say which. */
  sourceHandle: string;
  targetHandle: string;
  type: "link";
  data: { link: Link };
  zIndex?: number;
}

type Adjacency = Map<string, Set<string>>;

/** Device-level graph. Only RF hops rank the layout — a LAN link lives inside
 * one box and says nothing about where that box belongs. */
export function deviceAdjacency(topology: Topology): Adjacency {
  const deviceOf = new Map<string, string>();
  for (const device of topology.devices) {
    for (const radio of device.radios) deviceOf.set(radio.id, device.agentId);
  }

  const adjacency: Adjacency = new Map();
  for (const device of topology.devices) adjacency.set(device.agentId, new Set());

  for (const link of topology.links) {
    if (link.kind !== "wireless") continue;
    const a = deviceOf.get(link.source);
    const b = deviceOf.get(link.target);
    if (!a || !b || a === b) continue;
    adjacency.get(a)?.add(b);
    adjacency.get(b)?.add(a);
  }
  return adjacency;
}

/** Connected components, each in deterministic order. */
export function connectedComponents(adjacency: Adjacency): string[][] {
  const seen = new Set<string>();
  const out: string[][] = [];
  for (const start of [...adjacency.keys()].sort()) {
    if (seen.has(start)) continue;
    const stack = [start];
    const component: string[] = [];
    seen.add(start);
    while (stack.length) {
      const id = stack.pop() as string;
      component.push(id);
      for (const next of [...(adjacency.get(id) ?? [])].sort()) {
        if (seen.has(next)) continue;
        seen.add(next);
        stack.push(next);
      }
    }
    out.push(component.sort());
  }
  return out;
}

/**
 * The component walked end to end, or null if it is not a simple path.
 *
 * A path has every node at degree <= 2 and exactly two endpoints at degree 1.
 * A ring (every node degree 2) is deliberately rejected: it has no end to start
 * from, and snaking it would imply an order the topology does not have.
 */
export function pathOrder(component: string[], adjacency: Adjacency): string[] | null {
  if (component.length === 1) return [...component];

  const degree = (id: string) => (adjacency.get(id)?.size ?? 0);
  if (component.some((id) => degree(id) > 2)) return null;

  const ends = component.filter((id) => degree(id) === 1).sort();
  if (ends.length !== 2) return null;

  const ordered: string[] = [];
  const seen = new Set<string>();
  let current: string | undefined = ends[0];
  while (current) {
    ordered.push(current);
    seen.add(current);
    current = [...(adjacency.get(current) ?? [])].sort().find((n) => !seen.has(n));
  }
  return ordered.length === component.length ? ordered : null;
}

/**
 * Columns for a serpentine of `count` boxes.
 *
 * Chosen so the block lands near a 16:9-ish aspect rather than a long line or a
 * tall stack — the whole point is seeing the fleet at once.
 */
export function columnsFor(count: number, heightHint = groupHeight(2)): number {
  if (count <= 3) return count;
  const cellW = GROUP_WIDTH + COL_GAP;
  const cellH = heightHint + ROW_GAP;
  const target = 1.7; // width / height
  const cols = Math.round(Math.sqrt((count * target * cellH) / cellW));
  return Math.max(2, Math.min(count, cols));
}

/** Grid slot for the i-th box, reversing every other row so the chain stays
 * continuous instead of jumping back across the diagram at each wrap. */
export function serpentineCell(index: number, columns: number): { row: number; col: number } {
  const row = Math.floor(index / columns);
  const offset = index % columns;
  return { row, col: row % 2 === 0 ? offset : columns - 1 - offset };
}

interface Placement {
  x: number;
  y: number;
}

function placeSerpentine(
  ordered: string[],
  heights: Map<string, number>,
  originY: number,
): { placements: Map<string, Placement>; height: number } {
  const columns = columnsFor(
    ordered.length,
    Math.max(...ordered.map((id) => heights.get(id) ?? groupHeight(1))),
  );
  const placements = new Map<string, Placement>();

  const rowHeights: number[] = [];
  ordered.forEach((id, index) => {
    const { row } = serpentineCell(index, columns);
    rowHeights[row] = Math.max(rowHeights[row] ?? 0, heights.get(id) ?? groupHeight(1));
  });

  const rowTops: number[] = [];
  let y = originY;
  rowHeights.forEach((h, row) => {
    rowTops[row] = y;
    y += h + ROW_GAP;
  });

  ordered.forEach((id, index) => {
    const { row, col } = serpentineCell(index, columns);
    placements.set(id, { x: col * (GROUP_WIDTH + COL_GAP), y: rowTops[row] });
  });

  return { placements, height: y - originY - ROW_GAP };
}

function placeWithDagre(
  component: string[],
  adjacency: Adjacency,
  heights: Map<string, number>,
  originY: number,
): { placements: Map<string, Placement>; height: number } {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "LR", nodesep: 48, ranksep: COL_GAP, marginx: 0, marginy: 0 });
  graph.setDefaultEdgeLabel(() => ({}));

  const inComponent = new Set(component);
  for (const id of component) {
    graph.setNode(id, { width: GROUP_WIDTH, height: heights.get(id) ?? groupHeight(1) });
  }
  const seenPair = new Set<string>();
  for (const id of component) {
    for (const neighbour of adjacency.get(id) ?? []) {
      if (!inComponent.has(neighbour)) continue;
      const key = [id, neighbour].sort().join("|");
      if (seenPair.has(key)) continue;
      seenPair.add(key);
      graph.setEdge(id, neighbour);
    }
  }
  dagre.layout(graph);

  const placements = new Map<string, Placement>();
  let minY = Infinity;
  let maxY = -Infinity;
  for (const id of component) {
    const laid = graph.node(id);
    const h = heights.get(id) ?? groupHeight(1);
    // dagre reports centres; React Flow positions from the top-left corner.
    const x = (laid?.x ?? 0) - GROUP_WIDTH / 2;
    const y = (laid?.y ?? 0) - h / 2;
    placements.set(id, { x, y });
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y + h);
  }
  // Shift so the component starts at originY.
  const shift = originY - (Number.isFinite(minY) ? minY : 0);
  for (const [id, p] of placements) placements.set(id, { x: p.x, y: p.y + shift });

  return { placements, height: Number.isFinite(minY) ? maxY - minY : 0 };
}

/**
 * Which side of each node an edge should leave from and arrive at.
 *
 * Derived from the final geometry rather than fixed, because a serpentine sends
 * a hop left-to-right on one row, right-to-left on the next, and downwards at
 * every wrap. Hardcoding right-to-left would route half the edges backwards
 * through their own boxes.
 */
export function pickHandles(
  from: Placement & { h: number },
  to: Placement & { h: number },
): { sourceHandle: string; targetHandle: string } {
  const dx = to.x - from.x;
  const dy = to.y + to.h / 2 - (from.y + from.h / 2);
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0
      ? { sourceHandle: "r-s", targetHandle: "l-t" }
      : { sourceHandle: "l-s", targetHandle: "r-t" };
  }
  return dy >= 0
    ? { sourceHandle: "b-s", targetHandle: "t-t" }
    : { sourceHandle: "t-s", targetHandle: "b-t" };
}

export function layoutTopology(topology: Topology): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const { devices, links } = topology;

  const heights = new Map<string, number>();
  for (const device of devices) heights.set(device.agentId, groupHeight(device.radios.length));

  const adjacency = deviceAdjacency(topology);
  const placements = new Map<string, Placement>();

  let originY = 0;
  for (const component of connectedComponents(adjacency)) {
    const ordered = pathOrder(component, adjacency);
    const result = ordered
      ? placeSerpentine(ordered, heights, originY)
      : placeWithDagre(component, adjacency, heights, originY);
    for (const [id, p] of result.placements) placements.set(id, p);
    originY += result.height + COMPONENT_GAP;
  }

  // Parents before children: React Flow resolves `parentId` in array order and
  // silently drops a child that precedes its parent.
  const nodes: FlowNode[] = [];
  const deviceOfRadio = new Map<string, string>();
  for (const device of devices) {
    const position = placements.get(device.agentId) ?? { x: 0, y: 0 };
    const height = heights.get(device.agentId) as number;

    nodes.push({
      id: device.agentId,
      type: "device",
      position,
      data: { device },
      draggable: false,
      selectable: false,
      style: { width: GROUP_WIDTH, height },
    });

    device.radios.forEach((radio: Radio, index: number) => {
      deviceOfRadio.set(radio.id, device.agentId);
      nodes.push({
        id: radio.id,
        type: "radio",
        position: { x: PAD_X, y: PAD_TOP + index * (NODE_HEIGHT + NODE_GAP) },
        data: { radio },
        parentId: device.agentId,
        extent: "parent",
        draggable: false,
        style: { width: NODE_WIDTH, height: NODE_HEIGHT },
      });
    });
  }

  const edges: FlowEdge[] = links.map((link) => {
    const fromDevice = deviceOfRadio.get(link.source);
    const toDevice = deviceOfRadio.get(link.target);
    let handles = { sourceHandle: "b-s", targetHandle: "t-t" };
    if (link.kind === "wireless" && fromDevice && toDevice) {
      const from = placements.get(fromDevice);
      const to = placements.get(toDevice);
      if (from && to) {
        handles = pickHandles(
          { ...from, h: heights.get(fromDevice) as number },
          { ...to, h: heights.get(toDevice) as number },
        );
      }
    }
    return {
      id: link.id,
      source: link.source,
      target: link.target,
      ...handles,
      type: "link" as const,
      data: { link },
      // RF hops carry the information; LAN links are context and sit behind them.
      zIndex: link.kind === "wireless" ? 2 : 1,
    };
  });

  return { nodes, edges };
}
