import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

import {
  HEALTH_COLOUR,
  edgeWidth,
  formatLatency,
  formatThroughput,
  linkHealth,
} from "../lib/appearance";
import type { Link } from "../lib/topology";

/**
 * One hop.
 *
 * A LAN link is drawn thin and dashed with no label: the two radios are cabled
 * together in the same cabinet and there is nothing to measure. An RF hop is
 * coloured by SNR band, thickened by throughput, and labelled with **latency
 * first** — a link can hold a healthy SNR and still be unusable if it is
 * queueing, so round-trip time is the figure that actually says whether traffic
 * is getting through. SNR and throughput sit underneath as the supporting pair.
 */
function LinkEdgeInner({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps) {
  const link = (data as unknown as { link: Link }).link;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
  });

  if (link.kind === "lan") {
    return (
      <BaseEdge
        id={id}
        path={path}
        style={{ stroke: "#94a3b8", strokeWidth: 1.25, strokeDasharray: "3 3", opacity: 0.7 }}
      />
    );
  }

  const health = linkHealth(link);
  const colour = HEALTH_COLOUR[health];
  const latency = formatLatency(link.latencyMs);
  const throughput =
    formatThroughput(link.rxThroughputKbps) ?? formatThroughput(link.txThroughputKbps);
  const snr = link.snrDb !== null ? `${Math.round(link.snrDb)} dB` : null;

  // Headline. Latency when we have it; SNR while the fleet is still on a
  // release that does not publish latency; a question mark when neither end is
  // reporting, because then no figure here describes the present.
  let headline: string;
  if (link.unreachable) headline = "?";
  else if (latency) headline = latency;
  else if (snr) headline = snr;
  else headline = link.declared ? "declared" : "no data";

  // The supporting pair, minus whatever was promoted to the headline.
  const secondary = [headline === snr ? null : snr, throughput].filter(Boolean).join(" · ");

  const full: string[] = [];
  if (link.unreachable) full.push("Both ends unreachable — figures below are the last known");
  if (latency) full.push(`latency ${latency}`);
  if (link.apSideLatencyMs !== null) {
    full.push(`AP side ${formatLatency(link.apSideLatencyMs)}`);
  }
  if (snr) full.push(`SNR ${snr}`);
  if (link.signalDbm !== null) full.push(`signal ${Math.round(link.signalDbm)} dBm`);
  if (link.apSideSignalDbm !== null) full.push(`AP side ${Math.round(link.apSideSignalDbm)} dBm`);
  if (link.ccqPct !== null) full.push(`CCQ ${Math.round(link.ccqPct)}%`);
  if (link.txRateMbps !== null && link.rxRateMbps !== null) {
    full.push(`rate ${Math.round(link.txRateMbps)}/${Math.round(link.rxRateMbps)} Mbps`);
  }
  if (throughput) full.push(`throughput ${throughput}`);
  if (link.declared) full.push("peer declared in config, not observed");

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: colour,
          strokeWidth: edgeWidth(link),
          // Declared-not-observed, or nobody reporting: either way the line is
          // an assertion we cannot currently back up, so it is not drawn solid.
          strokeDasharray: link.declared || link.unreachable ? "6 4" : undefined,
          opacity: link.unreachable ? 0.75 : 1,
        }}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute flex flex-col items-center rounded-md px-1.5 py-0.5 text-center leading-tight text-white shadow-md ring-1 ring-white/60"
          title={full.join(" · ") || "no measurements"}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: colour,
            pointerEvents: "all",
          }}
        >
          <span className="text-[11px] font-semibold tabular-nums">{headline}</span>
          {secondary && (
            <span className="text-[9px] font-medium tabular-nums opacity-90">{secondary}</span>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const LinkEdge = memo(LinkEdgeInner);
