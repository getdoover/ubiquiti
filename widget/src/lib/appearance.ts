/**
 * How the graph is coloured. One place, so the diagram, the legend and the
 * table cannot drift apart.
 */

import type { Health, Link, Radio } from "./topology";

/** Explicit hex rather than Tailwind classes: SVG strokes and box-shadows need
 * a real colour value, and these have to match between an edge and its pill. */
export const HEALTH_COLOUR: Record<Health, string> = {
  excellent: "#16a34a",
  good: "#65a30d",
  fair: "#d97706",
  poor: "#dc2626",
  unknown: "#94a3b8",
};

export type RadioState = "ok" | "radio-down" | "device-down" | "stale";

/**
 * Why a radio is not reporting, in the order the operator can act on it.
 *
 * `device-down` outranks `radio-down` deliberately: if the Doovit is
 * unreachable, "the radio is offline" is not a finding — we simply have not
 * heard from anything at that site, and sending someone to look at the radio
 * would be the wrong callout.
 */
export function radioState(radio: Radio): RadioState {
  if (!radio.agentOnline) return "device-down";
  if (!radio.online) return "radio-down";
  if (radio.stale) return "stale";
  return "ok";
}

export const STATE_COLOUR: Record<RadioState, string> = {
  ok: "#16a34a",
  "radio-down": "#dc2626",
  "device-down": "#64748b",
  stale: "#d97706",
};

export const STATE_LABEL: Record<RadioState, string> = {
  ok: "Online",
  "radio-down": "Radio unreachable",
  "device-down": "Device offline",
  stale: "Stale readings",
};

/** True for an AP, false for a station, null when the radio has not said. */
export function isAccessPoint(radio: Radio): boolean | null {
  const mode = (radio.wirelessMode || "").toLowerCase();
  if (!mode) return null;
  if (mode.includes("ap") || mode.includes("master")) return true;
  if (mode.includes("sta") || mode.includes("managed")) return false;
  return null;
}

/**
 * Halo radius from signal strength.
 *
 * Signal is dBm and negative: about -30 is as strong as a radio realistically
 * reads, -90 is noise. Mapped to 0..1 and then to a glow, so a strong link
 * visibly blooms the way the reference does.
 */
export function signalGlow(signalDbm: number | null): number {
  if (signalDbm === null || !Number.isFinite(signalDbm)) return 0;
  const clamped = Math.min(-30, Math.max(-90, signalDbm));
  return (clamped + 90) / 60;
}

/**
 * Stroke width from throughput.
 *
 * The reference uses one very thick edge for its backbone, which is the single
 * most readable thing in that picture — it shows where the traffic actually is.
 * Log-scaled: link rates span three orders of magnitude and a linear map would
 * leave everything except the busiest hop hairline-thin.
 */
export function edgeWidth(link: Link): number {
  const kbps = Math.max(link.txThroughputKbps ?? 0, link.rxThroughputKbps ?? 0);
  if (!kbps || kbps <= 0) return 1.5;
  const scaled = Math.log10(kbps) / Math.log10(100_000); // 100 Mbps -> 1.0
  return 1.5 + Math.min(1, Math.max(0, scaled)) * 6.5;
}

export function formatThroughput(kbps: number | null): string | null {
  if (kbps === null || !Number.isFinite(kbps)) return null;
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${Math.round(kbps)} kbps`;
}

export function formatRate(mbps: number | null): string | null {
  if (mbps === null || !Number.isFinite(mbps)) return null;
  return `${Math.round(mbps)}`;
}
