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

  const stats: string[] = [];
  if (link.snrDb !== null) stats.push(`${Math.round(link.snrDb)} dB`);
  if (link.signalDbm !== null) stats.push(`${Math.round(link.signalDbm)} dBm`);
  if (link.ccqPct !== null) stats.push(`${Math.round(link.ccqPct)}%`);
  if (link.txRateMbps !== null && link.rxRateMbps !== null) {
    stats.push(`${Math.round(link.txRateMbps)}/${Math.round(link.rxRateMbps)} Mbps`);
  }
  if (throughput) stats.push(throughput);
  // A hop we know exists but have no measurements for still has to read as a
  // link, not as a blank line.
  if (stats.length === 0) stats.push(link.declared ? "declared" : "no data");

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
          className="nodrag nopan pointer-events-none absolute rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white shadow-sm"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: colour,
          }}
        >
          {stats.join(" · ")}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const LinkEdge = memo(LinkEdgeInner);
