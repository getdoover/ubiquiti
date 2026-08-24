import { describe, expect, it } from "vitest";

import {
  columnsFor,
  connectedComponents,
  deviceAdjacency,
  layoutTopology,
  pathOrder,
  pickHandles,
  serpentineCell,
} from "./layout";
import { buildTopology, type Radio } from "./topology";

function radio(id: string, agentId: string, mac: string, apMac?: string): Radio {
  return {
    id,
    agentId,
    appKey: id.split(":")[1] ?? id,
    deviceName: agentId,
    groupName: null,
    radioMac: mac,
    apMac: apMac ?? null,
    uplinkMac: null,
    stations: [],
    online: true,
    agentOnline: true,
    stale: false,
    hostname: null,
    model: null,
    wirelessMode: null,
    essid: null,
    frequencyMhz: null,
    signalDbm: null,
    noiseDbm: null,
    snrDb: null,
    ccqPct: null,
    txRateMbps: null,
    rxRateMbps: null,
    txThroughputKbps: null,
    rxThroughputKbps: null,
    latencyMs: null,
    lastSeenMs: null,
    lastUpdated: null,
  };
}

const mac = (n: number) => `28:70:4e:e2:00:${n.toString(16).padStart(2, "0")}`;

/** A chain of `n` sites: each has a client associated to the previous site's AP. */
function chain(n: number): Radio[] {
  const radios: Radio[] = [];
  for (let i = 0; i < n; i += 1) {
    if (i > 0) radios.push(radio(`s${i}:up`, `s${i}`, mac(i * 2), mac(i * 2 - 1)));
    radios.push(radio(`s${i}:down`, `s${i}`, mac(i * 2 + 1)));
  }
  return radios;
}

describe("serpentineCell", () => {
  it("reverses every other row so the chain stays continuous", () => {
    const cols = 4;
    // Row 0 runs left to right...
    expect([0, 1, 2, 3].map((i) => serpentineCell(i, cols).col)).toEqual([0, 1, 2, 3]);
    // ...row 1 comes back the other way, so hop 3->4 is a short vertical step
    // rather than a jump all the way back across the diagram.
    expect([4, 5, 6, 7].map((i) => serpentineCell(i, cols).col)).toEqual([3, 2, 1, 0]);
    expect([4, 5, 6, 7].map((i) => serpentineCell(i, cols).row)).toEqual([1, 1, 1, 1]);
  });
});

describe("columnsFor", () => {
  it("keeps short chains on one row", () => {
    expect(columnsFor(2)).toBe(2);
    expect(columnsFor(3)).toBe(3);
  });

  it("wraps a long chain instead of drawing one very wide line", () => {
    const cols = columnsFor(12);
    expect(cols).toBeGreaterThan(1);
    expect(cols).toBeLessThan(12);
  });

  it("never returns more columns than there are boxes", () => {
    for (const n of [1, 2, 5, 9, 30, 100]) {
      expect(columnsFor(n)).toBeLessThanOrEqual(n);
      expect(columnsFor(n)).toBeGreaterThanOrEqual(1);
    }
  });
});

describe("pathOrder", () => {
  it("walks a chain end to end", () => {
    const topology = buildTopology(chain(4));
    const adjacency = deviceAdjacency(topology);
    const [component] = connectedComponents(adjacency);
    expect(pathOrder(component, adjacency)).toEqual(["s0", "s1", "s2", "s3"]);
  });

  it("accepts an AP between two stations — that is still a line", () => {
    const radios = [
      radio("hub:down", "hub", mac(1)),
      radio("a:up", "a", mac(2), mac(1)),
      radio("b:up", "b", mac(3), mac(1)),
    ];
    const topology = buildTopology(radios);
    const adjacency = deviceAdjacency(topology);
    const [component] = connectedComponents(adjacency);
    // Degrees are 1, 2, 1 — a path, so it snakes like any other chain.
    expect(pathOrder(component, adjacency)).toEqual(["a", "hub", "b"]);
  });

  it("refuses a real branch, so it falls back to dagre", () => {
    // One AP serving three stations: the hub is degree 3, which no serpentine
    // can represent without implying an order that does not exist.
    const radios = [
      radio("hub:down", "hub", mac(1)),
      radio("a:up", "a", mac(2), mac(1)),
      radio("b:up", "b", mac(3), mac(1)),
      radio("c:up", "c", mac(4), mac(1)),
    ];
    const topology = buildTopology(radios);
    const adjacency = deviceAdjacency(topology);
    const [component] = connectedComponents(adjacency);
    expect(pathOrder(component, adjacency)).toBeNull();
  });

  it("refuses a ring, which has no end to start from", () => {
    const radios = [
      radio("a:x", "a", mac(1), mac(2)),
      radio("b:x", "b", mac(2), mac(3)),
      radio("c:x", "c", mac(3), mac(1)),
    ];
    const topology = buildTopology(radios);
    const adjacency = deviceAdjacency(topology);
    const [component] = connectedComponents(adjacency);
    expect(pathOrder(component, adjacency)).toBeNull();
  });
});

describe("layoutTopology", () => {
  it("condenses a long chain into rows rather than one line", () => {
    const { nodes } = layoutTopology(buildTopology(chain(10)));
    const devices = nodes.filter((n) => n.type === "device");
    expect(devices).toHaveLength(10);

    const rows = new Set(devices.map((n) => Math.round(n.position.y)));
    expect(rows.size).toBeGreaterThan(1); // it wrapped

    const width = Math.max(...devices.map((n) => n.position.x));
    const height = Math.max(...devices.map((n) => n.position.y));
    // The whole point: not a strip ten boxes wide and one tall.
    expect(height).toBeGreaterThan(0);
    expect(width / Math.max(1, height)).toBeLessThan(6);
  });

  it("emits every parent before its children", () => {
    const { nodes } = layoutTopology(buildTopology(chain(5)));
    const seen = new Set<string>();
    for (const node of nodes) {
      if (node.parentId) expect(seen.has(node.parentId)).toBe(true);
      seen.add(node.id);
    }
  });

  it("routes each hop from the side it actually needs", () => {
    const { edges } = layoutTopology(buildTopology(chain(10)));
    const wireless = edges.filter((e) => e.id.startsWith("w:"));
    // A serpentine turns corners, so the handles cannot all be the same pair.
    const combos = new Set(wireless.map((e) => `${e.sourceHandle}->${e.targetHandle}`));
    expect(combos.size).toBeGreaterThan(1);
  });

  it("stacks disconnected components instead of overlapping them", () => {
    const radios = [...chain(3), radio("z:down", "z", mac(90))];
    const { nodes } = layoutTopology(buildTopology(radios));
    const z = nodes.find((n) => n.id === "z");
    const others = nodes.filter((n) => n.type === "device" && n.id !== "z");
    expect(z).toBeDefined();
    expect(Math.min(...others.map((n) => n.position.y))).toBeLessThan(
      (z as { position: { y: number } }).position.y,
    );
  });
});

describe("pickHandles", () => {
  it("goes right when the peer is to the right, left when it is to the left", () => {
    expect(pickHandles({ x: 0, y: 0, h: 100 }, { x: 400, y: 0, h: 100 })).toEqual({
      sourceHandle: "r-s",
      targetHandle: "l-t",
    });
    expect(pickHandles({ x: 400, y: 0, h: 100 }, { x: 0, y: 0, h: 100 })).toEqual({
      sourceHandle: "l-s",
      targetHandle: "r-t",
    });
  });

  it("routes a wrap around the outside, not down through the boxes", () => {
    // Both ends leave on the same outer side, so the line loops around the edge
    // of the block. Going out the bottom sent it straight through the sibling
    // radio card and then through the box below.
    expect(pickHandles({ x: 0, y: 0, h: 100 }, { x: 0, y: 300, h: 100 }, "r")).toEqual({
      sourceHandle: "r-s",
      targetHandle: "r-t",
    });
    expect(pickHandles({ x: 0, y: 0, h: 100 }, { x: 0, y: 300, h: 100 }, "l")).toEqual({
      sourceHandle: "l-s",
      targetHandle: "l-t",
    });
  });
});
