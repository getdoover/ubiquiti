import { memo } from "react";

import type { Topology } from "../lib/topology";

type Device = Topology["devices"][number];

/** The box around one Doovit's radios. Purely a container — it carries no
 * status of its own, because a site is only as healthy as the radios in it. */
function DeviceGroupNodeInner({ data }: { data: { device: Device } }) {
  const { device } = data;
  return (
    <div className="h-full w-full rounded-xl border border-border/70 bg-muted/30 backdrop-blur-[1px]">
      <div className="truncate px-3 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {device.deviceName}
      </div>
    </div>
  );
}

export const DeviceGroupNode = memo(DeviceGroupNodeInner);
