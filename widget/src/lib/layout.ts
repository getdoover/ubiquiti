/**
 * Positions for the diagram.
 *
 * Laid out at the *device* level, not the radio level: dagre ranks the Doovits
 * by the RF hops between them, and each Doovit's radios are then stacked inside
 * its box at fixed offsets. Two reasons for that split — a site's radios always
 * want to sit together regardless of what dagre would prefer, and ranking a
 * dozen boxes is far more stable than ranking two dozen loose nodes, so the
 * diagram does not reshuffle when one radio drops out.
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
  /** Named explicitly: a radio node exposes four handles, and React Flow warns
   * and picks arbitrarily if an edge does not say which it means. */
  sourceHandle: string;
  targetHandle: string;
  type: "link";
  data: { link: Link };
  zIndex?: number;
}

/**
 * Nodes and edges ready for React Flow.
 *
 * Parents are emitted before their children: React Flow resolves `parentId` in
 * array order and silently drops a child that precedes its parent.
 */
export function layoutTopology(topology: Topology): {
  nodes: FlowNode[];
  edges: FlowEdge[];
} {
  const { devices, links } = topology;

  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "LR", nodesep: 48, ranksep: 110, marginx: 24, marginy: 24 });
  graph.setDefaultEdgeLabel(() => ({}));

  for (const device of devices) {
    graph.setNode(device.agentId, {
      width: GROUP_WIDTH,
      height: groupHeight(device.radios.length),
    });
  }

  // Only RF hops rank the layout. A LAN link lives inside one box and says
  // nothing about where that box belongs.
  const deviceOf = new Map<string, string>();
  for (const device of devices) {
    for (const radio of device.radios) deviceOf.set(radio.id, device.agentId);
  }
  const seenDevicePair = new Set<string>();
  for (const link of links) {
    if (link.kind !== "wireless") continue;
    const a = deviceOf.get(link.source);
    const b = deviceOf.get(link.target);
    if (!a || !b || a === b) continue;
    const key = [a, b].sort().join("|");
    if (seenDevicePair.has(key)) continue;
    seenDevicePair.add(key);
    graph.setEdge(a, b);
  }

  dagre.layout(graph);

  const nodes: FlowNode[] = [];
  for (const device of devices) {
    const laid = graph.node(device.agentId);
    const height = groupHeight(device.radios.length);
    // dagre reports centres; React Flow positions from the top-left corner.
    const x = (laid?.x ?? 0) - GROUP_WIDTH / 2;
    const y = (laid?.y ?? 0) - height / 2;

    nodes.push({
      id: device.agentId,
      type: "device",
      position: { x, y },
      data: { device },
      draggable: false,
      selectable: false,
      style: { width: GROUP_WIDTH, height },
    });

    device.radios.forEach((radio: Radio, index: number) => {
      nodes.push({
        id: radio.id,
        type: "radio",
        // Relative to the parent group.
        position: { x: PAD_X, y: PAD_TOP + index * (NODE_HEIGHT + NODE_GAP) },
        data: { radio },
        parentId: device.agentId,
        extent: "parent",
        draggable: false,
        style: { width: NODE_WIDTH, height: NODE_HEIGHT },
      });
    });
  }

  const edges: FlowEdge[] = links.map((link) => ({
    id: link.id,
    source: link.source,
    target: link.target,
    // RF hops run between boxes, so they leave the right edge and arrive at the
    // left. A LAN link is vertical inside one box.
    sourceHandle: link.kind === "wireless" ? "r" : "b",
    targetHandle: link.kind === "wireless" ? "l" : "t",
    type: "link",
    data: { link },
    // RF hops carry the information; LAN links are context and sit behind them.
    zIndex: link.kind === "wireless" ? 2 : 1,
  }));

  return { nodes, edges };
}
