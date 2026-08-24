import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Radio as RadioIcon, RadioTower } from "lucide-react";

import {
  STATE_COLOUR,
  STATE_LABEL,
  isAccessPoint,
  radioState,
  signalGlow,
} from "../lib/appearance";
import type { Radio } from "../lib/topology";

function RadioNodeInner({ data }: { data: { radio: Radio } }) {
  const { radio } = data;
  const state = radioState(radio);
  const colour = STATE_COLOUR[state];
  const glow = signalGlow(radio.signalDbm);
  const ap = isAccessPoint(radio);
  const Icon = ap ? RadioTower : RadioIcon;

  return (
    <div
      className="flex h-full w-full items-center gap-2 rounded-lg border border-border bg-card px-2 shadow-sm"
      title={`${radio.deviceName} · ${radio.appKey} · ${STATE_LABEL[state]}`}
    >
      {/* Handles on both sides so an RF hop attaches whichever way dagre ranked
          the two sites, and top/bottom for the LAN link inside a box. */}
      <Handle id="l" type="target" position={Position.Left} className="!opacity-0" />
      <Handle id="r" type="source" position={Position.Right} className="!opacity-0" />
      <Handle id="t" type="target" position={Position.Top} className="!opacity-0" />
      <Handle id="b" type="source" position={Position.Bottom} className="!opacity-0" />

      <span
        className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
        style={{
          color: colour,
          // The reference's halo: a ring plus a soft bloom that grows with
          // signal strength, so a strong link is visible before reading a number.
          boxShadow:
            `0 0 0 2px ${colour}` +
            (glow > 0 ? `, 0 0 ${6 + glow * 14}px ${1 + glow * 4}px ${colour}44` : ""),
        }}
      >
        <Icon size={14} aria-hidden />
      </span>

      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[12px] font-medium text-foreground">
          {radio.hostname || radio.appKey}
        </span>
        <span className="block truncate text-[10px] text-muted-foreground">
          {ap === null ? "unknown mode" : ap ? "AP" : "Station"}
          {radio.frequencyMhz ? ` · ${Math.round(radio.frequencyMhz)} MHz` : ""}
        </span>
      </span>
    </div>
  );
}

export const RadioNode = memo(RadioNodeInner);
