import { describe, expect, it } from "vitest";

import { ageTone, formatAge, formatLatency, linkHealth } from "./appearance";
import {
  buildTopology,
  healthOf,
  normaliseMac,
  type Radio,
  type StationRecord,
} from "./topology";

/** A radio with everything absent, so each test states only what it is about. */
function radio(partial: Partial<Radio> & Pick<Radio, "id" | "agentId" | "appKey">): Radio {
  return {
    deviceName: partial.agentId,
    groupName: null,
    radioMac: null,
    apMac: null,
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
    ...partial,
  };
}

const AP_MAC = "28:70:4e:e2:9e:3c";
const STA_MAC = "28:70:4e:e2:96:b9";

function station(mac: string, extra: Partial<StationRecord> = {}): StationRecord {
  return { mac, ...extra };
}

describe("normaliseMac", () => {
  it("accepts the shapes airOS and config actually produce", () => {
    expect(normaliseMac("28:70:4E:E2:96:B9")).toBe(STA_MAC);
    expect(normaliseMac("28-70-4e-e2-96-b9")).toBe(STA_MAC);
    expect(normaliseMac("28704ee296b9")).toBe(STA_MAC);
  });

  it("treats the unassociated placeholder as no peer", () => {
    // Seven idle Bullet ACs on a bench all reported exactly this.
    expect(normaliseMac("00:00:00:00:00:00")).toBeNull();
    expect(normaliseMac("ff:ff:ff:ff:ff:ff")).toBeNull();
  });

  it("returns null rather than throwing on anything else", () => {
    for (const value of [null, undefined, "", "Bullet AC IP67", 42, {}]) {
      expect(normaliseMac(value)).toBeNull();
    }
  });
});

describe("healthOf", () => {
  it("bands SNR, and says unknown rather than guessing", () => {
    expect(healthOf(59)).toBe("excellent");
    expect(healthOf(20)).toBe("good");
    expect(healthOf(14)).toBe("fair");
    expect(healthOf(4)).toBe("poor");
    expect(healthOf(null)).toBe("unknown");
    expect(healthOf(NaN)).toBe("unknown");
  });
});

describe("buildTopology — wireless edges", () => {
  it("links a station to its AP from the station's ap_mac", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, snrDb: 59 });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC });

    const { links } = buildTopology([sta, ap]);
    const wireless = links.filter((l) => l.kind === "wireless");
    expect(wireless).toHaveLength(1);
    expect(wireless[0].source).toBe("a:sta");
    expect(wireless[0].target).toBe("b:ap");
    expect(wireless[0].snrDb).toBe(59);
    expect(wireless[0].declared).toBe(false);
  });

  it("links from the AP's station list when the station has not reported", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC });
    const ap = radio({
      id: "b:ap",
      agentId: "b",
      appKey: "down",
      radioMac: AP_MAC,
      stations: [station(STA_MAC, { signal_dbm: -61 })],
    });

    const { links } = buildTopology([sta, ap]);
    expect(links.filter((l) => l.kind === "wireless")).toHaveLength(1);
    expect(links[0].apSideSignalDbm).toBe(-61);
  });

  it("draws one edge, not two, when both ends report the same hop", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, signalDbm: -37 });
    const ap = radio({
      id: "b:ap",
      agentId: "b",
      appKey: "down",
      radioMac: AP_MAC,
      stations: [station(STA_MAC, { signal_dbm: -61, ccq_pct: 97 })],
    });

    const { links } = buildTopology([sta, ap]);
    const wireless = links.filter((l) => l.kind === "wireless");
    expect(wireless).toHaveLength(1);
    // Both views survive: the disagreement between the ends is the diagnosis.
    expect(wireless[0].signalDbm).toBe(-37);
    expect(wireless[0].apSideSignalDbm).toBe(-61);
    expect(wireless[0].apSideCcqPct).toBe(97);
  });

  it("joins across mixed MAC case, as a half-upgraded fleet produces", () => {
    // Releases before the topology tags published ap_mac verbatim — upper-cased.
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: "28:70:4E:E2:9E:3C" });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC });

    expect(buildTopology([sta, ap]).links.filter((l) => l.kind === "wireless")).toHaveLength(1);
  });

  it("draws no edge for an unassociated station", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: null });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC });

    const { links, danglingPeers } = buildTopology([sta, ap]);
    expect(links.filter((l) => l.kind === "wireless")).toHaveLength(0);
    expect(danglingPeers).toHaveLength(0);
  });

  it("uses a declared uplink when the radio reports no peer, and marks it", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, uplinkMac: AP_MAC });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC });

    const wireless = buildTopology([sta, ap]).links.filter((l) => l.kind === "wireless");
    expect(wireless).toHaveLength(1);
    expect(wireless[0].declared).toBe(true);
  });

  it("prefers what the radio observed over what config declared", () => {
    const other = "28:70:4e:e2:99:44";
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, uplinkMac: other });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC });
    const decoy = radio({ id: "c:ap", agentId: "c", appKey: "down", radioMac: other });

    const wireless = buildTopology([sta, ap, decoy]).links.filter((l) => l.kind === "wireless");
    expect(wireless).toHaveLength(1);
    expect(wireless[0].target).toBe("b:ap");
    expect(wireless[0].declared).toBe(false);
  });

  it("records a peer it cannot see instead of inventing a node for it", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC });

    const { links, danglingPeers } = buildTopology([sta]);
    expect(links).toHaveLength(0);
    expect(danglingPeers).toEqual([{ radioId: "a:sta", peerMac: AP_MAC }]);
  });

  it("ignores a radio that names itself", () => {
    const self = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: STA_MAC });
    expect(buildTopology([self]).links).toHaveLength(0);
  });
});

describe("buildTopology — devices and LAN links", () => {
  it("keeps a radio with no identity rather than dropping it", () => {
    const known = radio({ id: "a:up", agentId: "a", appKey: "up", radioMac: STA_MAC });
    const nameless = radio({ id: "a:down", agentId: "a", appKey: "down" });

    const { placed, unplaceable } = buildTopology([known, nameless]);
    expect(placed.map((r) => r.id)).toEqual(["a:up"]);
    expect(unplaceable.map((r) => r.id)).toEqual(["a:down"]);
  });

  it("cables a device's radios as a path, not a mesh", () => {
    const three = ["a", "b", "c"].map((k, i) =>
      radio({ id: `d:${k}`, agentId: "d", appKey: k, radioMac: `28:70:4e:e2:00:0${i}` }),
    );
    const lan = buildTopology(three).links.filter((l) => l.kind === "lan");
    expect(lan).toHaveLength(2); // a path through 3 nodes, not 3 pairs
  });

  it("does not draw a LAN link where an RF hop already connects the pair", () => {
    const a = radio({ id: "d:a", agentId: "d", appKey: "a", radioMac: STA_MAC, apMac: AP_MAC });
    const b = radio({ id: "d:b", agentId: "d", appKey: "b", radioMac: AP_MAC });

    const { links } = buildTopology([a, b]);
    expect(links).toHaveLength(1);
    expect(links[0].kind).toBe("wireless");
  });

  it("groups radios by device", () => {
    const radios = [
      radio({ id: "a:up", agentId: "a", appKey: "up", deviceName: "station-2", radioMac: STA_MAC }),
      radio({ id: "a:down", agentId: "a", appKey: "down", deviceName: "station-2", radioMac: AP_MAC }),
      radio({ id: "b:up", agentId: "b", appKey: "up", deviceName: "station-3", radioMac: "28:70:4e:e2:99:44" }),
    ];
    const { devices } = buildTopology(radios);
    expect(devices.map((d) => d.deviceName)).toEqual(["station-2", "station-3"]);
    expect(devices[0].radios).toHaveLength(2);
  });
});

describe("buildTopology — a real chain", () => {
  // The shape observed on Maxitool: each site has a client and an AP cabled
  // together, and each site's client associates to the previous site's AP.
  const MACS = ["28:70:4e:e2:00:01", "28:70:4e:e2:00:02", "28:70:4e:e2:00:03", "28:70:4e:e2:00:04"];

  const chain = [
    radio({ id: "s1:down", agentId: "s1", deviceName: "station-1", appKey: "airmax_downstream", radioMac: MACS[0] }),
    radio({ id: "s2:up", agentId: "s2", deviceName: "station-2", appKey: "airmax_upstream", radioMac: MACS[1], apMac: MACS[0], snrDb: 59 }),
    radio({ id: "s2:down", agentId: "s2", deviceName: "station-2", appKey: "airmax_downstream", radioMac: MACS[2] }),
    radio({ id: "s3:up", agentId: "s3", deviceName: "station-3", appKey: "airmax_upstream", radioMac: MACS[3], apMac: MACS[2], snrDb: 41 }),
  ];

  it("produces two RF hops and one LAN link", () => {
    const { links } = buildTopology(chain);
    expect(links.filter((l) => l.kind === "wireless")).toHaveLength(2);
    expect(links.filter((l) => l.kind === "lan")).toHaveLength(1);
  });

  it("is stable across re-renders regardless of input order", () => {
    const forward = buildTopology(chain);
    const reversed = buildTopology([...chain].reverse());
    expect(reversed.links.map((l) => l.id)).toEqual(forward.links.map((l) => l.id));
    expect(reversed.devices.map((d) => d.agentId)).toEqual(forward.devices.map((d) => d.agentId));
  });

  it("does not loop forever on a cycle", () => {
    const cyclic = [
      radio({ id: "a", agentId: "a", appKey: "x", radioMac: MACS[0], apMac: MACS[1] }),
      radio({ id: "b", agentId: "b", appKey: "x", radioMac: MACS[1], apMac: MACS[0] }),
    ];
    // Both name each other; that is one hop, not two, and must not recurse.
    expect(buildTopology(cyclic).links.filter((l) => l.kind === "wireless")).toHaveLength(1);
  });
});

// ------------------------------------------------------------------ freshness

describe("formatAge", () => {
  it("reads at a glance across the ranges that matter", () => {
    expect(formatAge(42_000)).toBe("42s");
    expect(formatAge(5 * 60_000)).toBe("5m");
    expect(formatAge(3 * 3_600_000)).toBe("3h");
    expect(formatAge(3 * 86_400_000)).toBe("3d");
  });

  it("has no answer rather than a wrong one", () => {
    expect(formatAge(null)).toBeNull();
    expect(formatAge(-1)).toBeNull();
    expect(formatAge(NaN)).toBeNull();
  });
});

describe("ageTone", () => {
  const window = 10 * 60_000; // a 10 minute staleness threshold

  it("warns before the threshold, not only after it", () => {
    // The case that prompted this: the fleet stopped reporting five minutes ago
    // against a ten minute window, and every node was drawn confidently green.
    expect(ageTone(5 * 60_000, window)).toBe("ageing");
  });

  it("is fresh only while the data really is", () => {
    expect(ageTone(30_000, window)).toBe("fresh");
    expect(ageTone(4 * 60_000, window)).toBe("fresh");
  });

  it("treats a reading past the window, or no reading at all, as stale", () => {
    expect(ageTone(11 * 60_000, window)).toBe("stale");
    expect(ageTone(null, window)).toBe("stale");
  });
});

// ---------------------------------------------- latency and unobserved links

describe("link latency and reachability", () => {
  const reporting = { online: true, agentOnline: true, stale: false };
  const silent = { online: false, agentOnline: true, stale: true };

  it("carries the station's latency onto the hop", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, latencyMs: 12, ...reporting });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC, ...reporting });

    const [link] = buildTopology([sta, ap]).links;
    expect(link.latencyMs).toBe(12);
    expect(link.unreachable).toBe(false);
  });

  it("keeps the AP's own view of latency alongside the station's", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, latencyMs: 12, ...reporting });
    const ap = radio({
      id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC, ...reporting,
      stations: [station(STA_MAC, { latency_ms: 31 })],
    });

    const [link] = buildTopology([sta, ap]).links;
    expect(link.latencyMs).toBe(12);
    expect(link.apSideLatencyMs).toBe(31);
  });

  it("marks a hop unobserved only when BOTH ends have gone quiet", () => {
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, ...silent });
    const ap = radio({ id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC, ...silent });

    const [link] = buildTopology([sta, ap]).links;
    expect(link.unreachable).toBe(true);
  });

  it("still trusts a hop one end can see", () => {
    // The AP is up and reports the station as associated, so the link is
    // observed even though the station itself has stopped answering.
    const sta = radio({ id: "a:sta", agentId: "a", appKey: "up", radioMac: STA_MAC, apMac: AP_MAC, ...silent });
    const ap = radio({
      id: "b:ap", agentId: "b", appKey: "down", radioMac: AP_MAC, ...reporting,
      stations: [station(STA_MAC, { signal_dbm: -61 })],
    });

    const [link] = buildTopology([sta, ap]).links;
    expect(link.unreachable).toBe(false);
  });
});

describe("linkHealth", () => {
  it("is unknown when nobody is observing, whatever the last SNR was", () => {
    // The trap: an excellent last-known SNR on a link that has gone dark would
    // otherwise paint bright green and assert the hop is fine right now.
    expect(linkHealth({ unreachable: true, snrDb: 59 } as never)).toBe("unknown");
  });

  it("bands by SNR while the hop is observed", () => {
    expect(linkHealth({ unreachable: false, snrDb: 59 } as never)).toBe("excellent");
    expect(linkHealth({ unreachable: false, snrDb: 4 } as never)).toBe("poor");
  });
});

describe("formatLatency", () => {
  it("keeps a decimal where single-digit differences matter", () => {
    expect(formatLatency(2.4)).toBe("2.4 ms");
    expect(formatLatency(47.6)).toBe("48 ms");
  });

  it("has no answer rather than a misleading zero", () => {
    expect(formatLatency(null)).toBeNull();
    expect(formatLatency(-1)).toBeNull();
  });
});
