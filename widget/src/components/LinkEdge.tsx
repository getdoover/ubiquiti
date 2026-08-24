import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

import { HEALTH_COLOUR, edgeWidth, formatThroughput } from "../lib/appearance";
import { healthOf, type Link } from "../lib/topology";

/**
 * One hop.
 *
 * A LAN link is drawn thin and dashed with no label: the two radios are cabled
 * together in the same cabinet and there is nothing to measure. An RF hop gets
 * the reference's treatment — coloured by SNR band, thickened by throughput,
 * with a stat pill sitting on the line.
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

  const health = healthOf(link.snrDb);
  const colour = HEALTH_COLOUR[health];
  const throughput =
    formatThroughput(link.rxThroughputKbps) ?? formatThroughput(link.txThroughputKbps);

  // Two values on the pill, not five. The gap between two device boxes is about
  // 156px; the previous label ran to roughly 260px, so its middle disappeared
  // behind the boxes and it read as two broken fragments. Everything else is a
  // hover away.
  const headline = link.snrDb !== null ? `${Math.round(link.snrDb)} dB` : link.declared ? "declared" : "no data";

  const full: string[] = [];
  if (link.snrDb !== null) full.push(`SNR ${Math.round(link.snrDb)} dB`);
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
          // A declared uplink is inferred from config, not observed on air.
          strokeDasharray: link.declared ? "6 4" : undefined,
        }}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute flex flex-col items-center rounded-md px-1.5 py-0.5 text-center leading-tight text-white shadow-md ring-1 ring-white/60"
          title={full.join(" · ") || "no measurements"}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: colour,
            // Hoverable, so the full stats are reachable without a click.
            pointerEvents: "all",
          }}
        >
          <span className="text-[11px] font-semibold tabular-nums">{headline}</span>
          {throughput && (
            <span className="text-[9px] font-medium tabular-nums opacity-90">{throughput}</span>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const LinkEdge = memo(LinkEdgeInner);
