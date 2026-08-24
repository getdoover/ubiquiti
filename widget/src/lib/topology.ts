/**
 * Turning the fleet's AirMax tags into a graph.
 *
 * Deliberately pure and free of React: this is the part that silently draws the
 * wrong picture if it is wrong, and a wrong graph is far more dangerous than a
 * blank one — it looks authoritative. Everything here is unit tested.
 *
 * The join, in one line: a station's `ap_mac` is the BSSID of the AP it is
 * associated with, and on a Bullet AC that BSSID is the AP's own `radio_mac`
 * (ath0 == deviceId == the discovery MAC). So an edge exists where
 * `station.apMac === ap.radioMac`. Confirmed against a live chain: station-3's
 * client reports `28:70:4E:E2:9E:3C`, which is station-2's downstream AP.
 */

/** Canonical lowercase colon-separated MAC, or null when there isn't one.
 *
 * Every join here is a string comparison, so both ends must agree on case and
 * separator — and they do not in a fleet mid-upgrade. AirMax releases before the
 * topology tags published `ap_mac` verbatim from the radio, which upper-cases it.
 *
 * All-zero is what an unassociated station reports for `ap_mac`: "no peer", not
 * an address. Left as-is it becomes a single phantom node that every idle radio
 * in the fleet appears to link to.
 */
export function normaliseMac(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  const hex = value.replace(/[^0-9a-fA-F]/g, "").toLowerCase();
  if (hex.length !== 12) return null;
  if (/^0+$/.test(hex) || /^f+$/.test(hex)) return null;
  return (hex.match(/.{2}/g) as string[]).join(":");
}

export interface StationRecord {
  mac: string | null;
  hostname?: string | null;
  signal_dbm?: number | null;
  noise_dbm?: number | null;
  ccq_pct?: number | null;
  tx_rate_mbps?: number | null;
  rx_rate_mbps?: number | null;
  uptime_s?: number | null;
  distance_m?: number | null;
}

/** One AirMax install: a radio, the device hosting it, and its live tags. */
export interface Radio {
  /** `${agentId}:${appKey}` — unique fleet-wide; a device may host several. */
  id: string;
  agentId: string;
  appKey: string;
  deviceName: string;
  groupName: string | null;

  radioMac: string | null;
  apMac: string | null;
  uplinkMac: string | null;
  stations: StationRecord[];

  /** The radio answered this pass. Distinct from whether the Doovit is up. */
  online: boolean;
  /** The Doovit's own connection state — a different fault with a different fix. */
  agentOnline: boolean;
  /** Tags older than the configured staleness window. */
  stale: boolean;

  hostname: string | null;
  model: string | null;
  wirelessMode: string | null;
  essid: string | null;
  frequencyMhz: number | null;

  signalDbm: number | null;
  noiseDbm: number | null;
  snrDb: number | null;
  ccqPct: number | null;
  txRateMbps: number | null;
  rxRateMbps: number | null;
  txThroughputKbps: number | null;
  rxThroughputKbps: number | null;
  /** When the app last actually reached this radio, ms since epoch.
   *
   * The single most important field on the card. Every other value is a
   * snapshot, and without knowing how old the snapshot is, a fleet that went
   * down five minutes ago is indistinguishable from one that is healthy now. */
  lastSeenMs: number | null;
  lastUpdated: number | null;
}

export type LinkKind = "wireless" | "lan";

export interface Link {
  id: string;
  /** The station end of a wireless link; the lower appKey of a LAN pair. */
  source: string;
  /** The AP end of a wireless link. */
  target: string;
  kind: LinkKind;
  /** True when the peer was named by config rather than observed. */
  declared: boolean;

  /** Station-side measurements — the station measures its own link. */
  snrDb: number | null;
  signalDbm: number | null;
  ccqPct: number | null;
  txRateMbps: number | null;
  rxRateMbps: number | null;
  txThroughputKbps: number | null;
  rxThroughputKbps: number | null;

  /** The AP's view of the same station, when it reported one. */
  apSideSignalDbm: number | null;
  apSideCcqPct: number | null;
}

export type Health = "excellent" | "good" | "fair" | "poor" | "unknown";

/**
 * SNR bands. Conventional airMAX rules of thumb rather than anything the
 * hardware reports — a link below ~10 dB will not carry useful traffic, and
 * above ~25 dB more signal buys nothing. Adjust here, in one place.
 */
export const SNR_BANDS: ReadonlyArray<{ min: number; health: Health }> = [
  { min: 25, health: "excellent" },
  { min: 18, health: "good" },
  { min: 12, health: "fair" },
  { min: -Infinity, health: "poor" },
];

export function healthOf(snrDb: number | null): Health {
  if (snrDb === null || !Number.isFinite(snrDb)) return "unknown";
  return SNR_BANDS.find((band) => snrDb >= band.min)!.health;
}

export interface Topology {
  /** Radios that can be placed on the graph, i.e. they have an identity. */
  placed: Radio[];
  /** Radios with no `radio_mac` — kept, never dropped, but not drawable. */
  unplaceable: Radio[];
  links: Link[];
  /** Device id -> its placeable radios, for the per-device group boxes. */
  devices: Array<{ agentId: string; deviceName: string; radios: Radio[] }>;
  /** Wireless links whose peer MAC matched no radio we can see. */
  danglingPeers: Array<{ radioId: string; peerMac: string }>;
}

/** Stable ordering so a re-render never reshuffles the diagram. */
function byAppKey(a: Radio, b: Radio) {
  return a.appKey.localeCompare(b.appKey);
}

function stationSideStats(radio: Radio) {
  return {
    snrDb: radio.snrDb,
    signalDbm: radio.signalDbm,
    ccqPct: radio.ccqPct,
    txRateMbps: radio.txRateMbps,
    rxRateMbps: radio.rxRateMbps,
    txThroughputKbps: radio.txThroughputKbps,
    rxThroughputKbps: radio.rxThroughputKbps,
  };
}

/**
 * Build the graph.
 *
 * Wireless edges are discovered from both ends and deduplicated: a station names
 * its AP via `ap_mac`, and an AP names its stations via `stations_json`. Either
 * alone is enough to draw the hop, which matters because the two ends are polled
 * independently and one may be unreachable while the other is fine. The AP-side
 * record is still kept when present — it carries that end's view of the signal,
 * and a large disagreement between the two is itself the diagnosis.
 */
export function buildTopology(input: Radio[]): Topology {
  // Normalised here rather than trusted from the caller. This module's entire
  // job is a string join between MACs that arrive from three different places
  // (a tag written by a new AirMax release, a tag written by an old one, and an
  // operator-typed config field), and they do not agree on case. Getting this
  // wrong yields an empty graph with no error anywhere — so the join owns it.
  // The normalised copies are what the caller renders, so the UI shows
  // canonical MACs too.
  const radios: Radio[] = input.map((radio) => ({
    ...radio,
    radioMac: normaliseMac(radio.radioMac),
    apMac: normaliseMac(radio.apMac),
    uplinkMac: normaliseMac(radio.uplinkMac),
  }));

  const placed = radios.filter((r) => r.radioMac).sort(byAppKey);
  const unplaceable = radios.filter((r) => !r.radioMac).sort(byAppKey);

  const byMac = new Map<string, Radio>();
  for (const radio of placed) byMac.set(radio.radioMac as string, radio);

  // Keyed by the unordered pair, so the same hop found from both ends collapses
  // to one edge instead of drawing a duplicate back the other way.
  const links = new Map<string, Link>();
  const danglingPeers: Topology["danglingPeers"] = [];

  const pairKey = (a: string, b: string) => [a, b].sort().join("|");

  // --- the station end: `ap_mac`, or a declared `uplink_mac` -----------------
  for (const radio of placed) {
    const observed = radio.apMac;
    const declared = radio.uplinkMac;
    const peerMac = observed ?? declared;
    if (!peerMac) continue;

    const peer = byMac.get(peerMac);
    if (!peer) {
      danglingPeers.push({ radioId: radio.id, peerMac });
      continue;
    }
    if (peer.id === radio.id) continue; // a radio cannot link to itself

    const key = pairKey(radio.id, peer.id);
    links.set(key, {
      id: `w:${key}`,
      source: radio.id,
      target: peer.id,
      kind: "wireless",
      declared: observed === null,
      ...stationSideStats(radio),
      apSideSignalDbm: null,
      apSideCcqPct: null,
    });
  }

  // --- the AP end: `stations_json` ------------------------------------------
  for (const ap of placed) {
    for (const station of ap.stations) {
      const mac = normaliseMac(station.mac);
      if (!mac) continue;
      const peer = byMac.get(mac);
      if (!peer || peer.id === ap.id) continue;

      const key = pairKey(peer.id, ap.id);
      const existing = links.get(key);
      if (existing) {
        // Same hop, already drawn from the station side. Enrich it rather than
        // adding a second edge.
        existing.apSideSignalDbm = station.signal_dbm ?? null;
        existing.apSideCcqPct = station.ccq_pct ?? null;
        continue;
      }
      links.set(key, {
        id: `w:${key}`,
        source: peer.id,
        target: ap.id,
        kind: "wireless",
        declared: false,
        ...stationSideStats(peer),
        apSideSignalDbm: station.signal_dbm ?? null,
        apSideCcqPct: station.ccq_pct ?? null,
      });
    }
  }

  // --- devices, and the LAN links inside them -------------------------------
  const deviceMap = new Map<string, { agentId: string; deviceName: string; radios: Radio[] }>();
  for (const radio of placed) {
    let entry = deviceMap.get(radio.agentId);
    if (!entry) {
      entry = { agentId: radio.agentId, deviceName: radio.deviceName, radios: [] };
      deviceMap.set(radio.agentId, entry);
    }
    entry.radios.push(radio);
  }

  for (const device of deviceMap.values()) {
    device.radios.sort(byAppKey);
    // Radios on one Doovit are cabled together through its bridge, not linked by
    // RF. Drawn as a path through the device's radios in a stable order, so a
    // three-radio site does not sprout a full mesh of meaningless edges.
    for (let i = 1; i < device.radios.length; i += 1) {
      const source = device.radios[i - 1];
      const target = device.radios[i];
      const key = pairKey(source.id, target.id);
      if (links.has(key)) continue; // an RF hop between them already won
      links.set(key, {
        id: `l:${key}`,
        source: source.id,
        target: target.id,
        kind: "lan",
        declared: false,
        snrDb: null,
        signalDbm: null,
        ccqPct: null,
        txRateMbps: null,
        rxRateMbps: null,
        txThroughputKbps: null,
        rxThroughputKbps: null,
        apSideSignalDbm: null,
        apSideCcqPct: null,
      });
    }
  }

  return {
    placed,
    unplaceable,
    links: [...links.values()].sort((a, b) => a.id.localeCompare(b.id)),
    devices: [...deviceMap.values()].sort((a, b) =>
      a.deviceName.localeCompare(b.deviceName),
    ),
    danglingPeers,
  };
}
