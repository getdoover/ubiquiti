import "./styles.css";
import "@xyflow/react/dist/style.css";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import RemoteComponentWrapper from "customer_site/RemoteComponentWrapper";
import { useRemoteParams } from "customer_site/useRemoteParams";

import {
  DooverProvider,
  useAgentChannel,
  useDeviceMap,
  useMultiAgentAggregates,
  type DeviceMapEntry,
} from "doover-js/react";
import { peekDooverClient } from "doover-js";

import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import { Maximize2, Minimize2 } from "lucide-react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent } from "./components/ui/card";
import { DeviceGroupNode } from "./components/DeviceGroupNode";
import { LinkEdge } from "./components/LinkEdge";
import { RadioNode } from "./components/RadioNode";
import { HEALTH_COLOUR, STATE_LABEL, radioState } from "./lib/appearance";
import { layoutTopology } from "./lib/layout";
import { buildTopology, healthOf, type Radio, type StationRecord } from "./lib/topology";

const AIRMAX_APPLICATION = "ubiquiti_airmax";
const DEFAULT_STALE_MINUTES = 10;

interface OverviewDeviceEntry extends DeviceMapEntry {
  app_installs?: Array<{ name?: string | null; application_name?: string | null }>;
}

/** `app_key` arrives as a prop from the UI schema — it is not in useRemoteParams. */
interface UiRemoteComponentOverview {
  app_key?: string;
}

// Defined at module scope: React Flow re-creates its internals when these
// identities change, so an inline object would remount the graph every render.
const nodeTypes = { device: DeviceGroupNode, radio: RadioNode };
const edgeTypes = { link: LinkEdge };

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Aggregates stamp seconds in some paths and milliseconds in others. */
function toEpochMs(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return value < 1e12 ? value * 1000 : value;
}

function parseStations(raw: unknown): StationRecord[] {
  if (typeof raw !== "string" || !raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** The Doovit's own connection, which is a different question from whether its
 * radio answered. `doover_connection` nests the status but flattens on some
 * message paths, so both shapes are accepted. */
function agentIsOnline(aggregate: unknown): boolean {
  const data = (aggregate as { data?: Record<string, unknown> } | undefined)?.data;
  if (!data) return false;
  const determination = str(data.determination);
  if (determination) return determination.toLowerCase() === "online";
  const status = data.status as Record<string, unknown> | string | undefined;
  const text = typeof status === "string" ? status : str(status?.status);
  return Boolean(text && text.toLowerCase().includes("online"));
}

function useRadios(agentId: string | undefined, appKey: string | undefined) {
  const { devices, deviceIds, isLoading, hasDeviceMap } =
    useDeviceMap<OverviewDeviceEntry>(agentId, appKey);

  const { data: deploymentConfig } = useAgentChannel<Record<string, any>>(
    agentId,
    "deployment_config",
  );
  const staleAfterMs = useMemo(() => {
    const minutes = num(deploymentConfig?.applications?.[appKey ?? ""]?.stale_after_minutes);
    return Math.max(1, minutes ?? DEFAULT_STALE_MINUTES) * 60_000;
  }, [deploymentConfig, appKey]);

  // App keys come from DEVICE_MAP, never from the shape of `tag_values`: an
  // install's key is operator-chosen (`airmax_upstream`), so prefix-matching
  // would miss exactly the multi-radio devices this view exists to draw.
  const { installsByDevice, fields } = useMemo(() => {
    const byDevice = new Map<string, string[]>();
    const keys = new Set<string>();
    for (const device of devices) {
      const names = (device.app_installs ?? [])
        .filter((i) => (i.application_name ?? "").toLowerCase() === AIRMAX_APPLICATION)
        .map((i) => i.name)
        .filter((n): n is string => Boolean(n));
      if (names.length) {
        byDevice.set(device.id, names);
        names.forEach((n) => keys.add(n));
      }
    }
    return { installsByDevice: byDevice, fields: [...keys].sort() };
  }, [devices]);

  const radioIds = useMemo(
    () => deviceIds.filter((id) => installsByDevice.has(id)),
    [deviceIds, installsByDevice],
  );

  const { aggregatesByAgent, query } = useMultiAgentAggregates("tag_values", radioIds, {
    fields,
  });
  const { aggregatesByAgent: connections } = useMultiAgentAggregates(
    "doover_connection",
    radioIds,
  );

  const radios = useMemo<Radio[]>(() => {
    const byId = new Map(devices.map((d) => [d.id, d]));
    const now = Date.now();
    const out: Radio[] = [];

    for (const [deviceId, appKeys] of installsByDevice) {
      const aggregate = aggregatesByAgent[deviceId];
      const device = byId.get(deviceId);
      const lastUpdated = toEpochMs(aggregate?.last_updated ?? null);
      const agentOnline = agentIsOnline(connections[deviceId]);

      for (const key of appKeys) {
        const tags = (aggregate?.data as Record<string, any> | undefined)?.[key];
        if (!tags) continue;
        out.push({
          id: `${deviceId}:${key}`,
          agentId: deviceId,
          appKey: key,
          deviceName: device?.display_name || device?.name || deviceId,
          groupName: str((device?.group as { name?: string } | undefined)?.name),

          radioMac: str(tags.radio_mac),
          apMac: str(tags.ap_mac),
          uplinkMac: str(tags.uplink_mac),
          stations: parseStations(tags.stations_json),

          online: tags.online === true,
          agentOnline,
          stale: lastUpdated !== null && now - lastUpdated > staleAfterMs,

          hostname: str(tags.hostname),
          model: str(tags.model),
          wirelessMode: str(tags.wireless_mode),
          essid: str(tags.essid),
          frequencyMhz: num(tags.frequency),

          signalDbm: num(tags.signal),
          noiseDbm: num(tags.noise_floor),
          snrDb: num(tags.snr),
          ccqPct: num(tags.ccq),
          txRateMbps: num(tags.tx_rate),
          rxRateMbps: num(tags.rx_rate),
          txThroughputKbps: num(tags.tx_throughput),
          rxThroughputKbps: num(tags.rx_throughput),
          lastUpdated,
        });
      }
    }
    return out;
  }, [devices, installsByDevice, aggregatesByAgent, connections, staleAfterMs]);

  return {
    radios,
    devicesGranted: devices.length,
    isLoading: isLoading || query.isLoading,
    hasDeviceMap,
  };
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
      <span className="font-medium">Link SNR</span>
      {(["excellent", "good", "fair", "poor", "unknown"] as const).map((health) => (
        <span key={health} className="flex items-center gap-1">
          <span
            className="inline-block h-[3px] w-5 rounded-full"
            style={{ background: HEALTH_COLOUR[health] }}
          />
          {health}
        </span>
      ))}
      <span className="ml-2">· thickness = throughput · dashed = declared or LAN</span>
    </div>
  );
}

function DetailPanel({ radio, onClose }: { radio: Radio; onClose: () => void }) {
  const rows: Array<[string, string | null]> = [
    ["Device", radio.deviceName],
    ["Install", radio.appKey],
    ["Status", STATE_LABEL[radioState(radio)]],
    ["Hostname", radio.hostname],
    ["Model", radio.model],
    ["Mode", radio.wirelessMode],
    ["SSID", radio.essid],
    ["Radio MAC", radio.radioMac],
    ["Peer (ap_mac)", radio.apMac ?? radio.uplinkMac],
    ["Frequency", radio.frequencyMhz ? `${Math.round(radio.frequencyMhz)} MHz` : null],
    ["Signal", radio.signalDbm !== null ? `${radio.signalDbm} dBm` : null],
    ["Noise floor", radio.noiseDbm !== null ? `${radio.noiseDbm} dBm` : null],
    ["SNR", radio.snrDb !== null ? `${radio.snrDb} dB` : null],
    ["CCQ", radio.ccqPct !== null ? `${radio.ccqPct}%` : null],
    ["TX / RX rate", radio.txRateMbps !== null ? `${radio.txRateMbps} / ${radio.rxRateMbps} Mbps` : null],
    ["Stations", radio.stations.length ? String(radio.stations.length) : null],
  ];

  return (
    <div className="absolute right-3 top-3 z-10 w-64 rounded-lg border border-border bg-card p-3 shadow-lg">
      <div className="mb-2 flex items-start justify-between gap-2">
        <span className="text-sm font-semibold">{radio.hostname || radio.appKey}</span>
        <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={onClose}>
          Close
        </Button>
      </div>
      <dl className="space-y-1">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="flex justify-between gap-2 text-[11px]">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="truncate text-right font-medium">{value}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}

function RadioTable({ radios }: { radios: Radio[] }) {
  return (
    <Card>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="p-2">Device</th>
              <th className="p-2">Install</th>
              <th className="p-2">Radio MAC</th>
              <th className="p-2">Peer</th>
              <th className="p-2">SNR</th>
              <th className="p-2">Signal</th>
              <th className="p-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {radios.map((radio) => (
              <tr key={radio.id} className="border-t">
                <td className="p-2">{radio.deviceName}</td>
                <td className="p-2">{radio.hostname || radio.appKey}</td>
                <td className="p-2 font-mono text-xs">{radio.radioMac ?? "—"}</td>
                <td className="p-2 font-mono text-xs">{radio.apMac ?? radio.uplinkMac ?? "—"}</td>
                <td className="p-2">{radio.snrDb !== null ? `${radio.snrDb} dB` : "—"}</td>
                <td className="p-2">{radio.signalDbm !== null ? `${radio.signalDbm} dBm` : "—"}</td>
                <td className="p-2">{STATE_LABEL[radioState(radio)]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

/**
 * The graph itself.
 *
 * Rendered either inline or, when expanded, portalled to `document.body` at
 * `position: fixed`. A portal is the only thing that reliably escapes the host:
 * the widget sits inside the customer site's own panel, which establishes
 * stacking and overflow contexts a nested element cannot break out of, so
 * growing the element in place gets clipped to the panel rather than filling
 * the screen. Both other Doover dashboard widgets do the same.
 */
function GraphCanvas({
  nodes,
  edges,
  expanded,
  onToggleExpanded,
  selectedRadio,
  onSelect,
}: {
  nodes: unknown[];
  edges: unknown[];
  expanded: boolean;
  onToggleExpanded: () => void;
  selectedRadio: Radio | null;
  onSelect: (id: string | null) => void;
}) {
  const canvas = (
    <div
      className={
        expanded
          ? "fixed inset-0 z-[9999] bg-background"
          : // Sized against the viewport rather than a fixed pixel height, so
            // the diagram uses the space the host actually gives it.
            "relative h-[min(68vh,640px)] w-full overflow-hidden rounded-xl border border-border bg-background"
      }
    >
      <ReactFlow
        // Remounted on expand so `fitView` re-runs against the new size —
        // React Flow fits on mount, not on container resize.
        key={expanded ? "expanded" : "inline"}
        nodes={nodes as never}
        edges={edges as never}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.1}
        maxZoom={2}
        nodesConnectable={false}
        onNodeClick={(_, node) => onSelect(String(node.id))}
        onPaneClick={() => onSelect(null)}
      >
        <Background gap={18} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
      </ReactFlow>

      <button
        type="button"
        onClick={onToggleExpanded}
        title={expanded ? "Exit full screen (Esc)" : "Full screen"}
        className="absolute right-3 top-3 z-10 rounded-md border border-border bg-card p-1.5 text-muted-foreground shadow-sm hover:text-foreground"
      >
        {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
      </button>

      {selectedRadio && (
        <DetailPanel radio={selectedRadio} onClose={() => onSelect(null)} />
      )}
    </div>
  );

  return expanded ? createPortal(canvas, document.body) : canvas;
}

function NetworkOverviewWidgetInner({ uiElement }: { uiElement?: UiRemoteComponentOverview }) {
  const params = useRemoteParams();
  const agentId = params?.agentId;
  const appKey = uiElement?.app_key ?? "";

  const [view, setView] = useState<"diagram" | "table">("diagram");
  const [selected, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  // Escape leaves full screen. Registered unconditionally — hooks cannot sit
  // behind the early returns below.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const { radios, devicesGranted, isLoading, hasDeviceMap } = useRadios(agentId, appKey);
  const topology = useMemo(() => buildTopology(radios), [radios]);
  const { nodes, edges } = useMemo(() => layoutTopology(topology), [topology]);

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">Loading radios…</div>;
  }
  if (!agentId || !appKey) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        This dashboard could not identify itself
        {!agentId && " (no agent)"}
        {!appKey && " (no app key)"}. Try reloading; if it persists the app's UI
        schema needs re-exporting.
      </div>
    );
  }
  if (!hasDeviceMap) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No device list has reached this dashboard yet. Grant it access under{" "}
        <strong>Devices</strong> in this app's configuration — by group, by device,
        or by <strong>Apps Installed</strong> — then redeploy so the platform
        rebuilds its device list.
      </div>
    );
  }
  if (!radios.length) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        {devicesGranted} device{devicesGranted === 1 ? "" : "s"} granted, but none is
        running the Ubiquiti AirMax app.
      </div>
    );
  }

  const online = radios.filter((r) => radioState(r) === "ok").length;
  const wireless = topology.links.filter((l) => l.kind === "wireless");
  const worst = wireless
    .map((l) => l.snrDb)
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b)[0];
  const selectedRadio = radios.find((r) => r.id === selected) ?? null;

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{radios.length} radios</Badge>
        <Badge>{online} online</Badge>
        <Badge>{wireless.length} links</Badge>
        {worst !== undefined && (
          <Badge style={{ background: HEALTH_COLOUR[healthOf(worst)], color: "white" }}>
            worst link {Math.round(worst)} dB
          </Badge>
        )}
        {topology.unplaceable.length > 0 && (
          <Badge variant="destructive">
            {topology.unplaceable.length} without radio_mac — redeploy AirMax
          </Badge>
        )}
        <span className="ml-auto inline-flex overflow-hidden rounded-md border border-border">
          {(["diagram", "table"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setView(mode)}
              className={`px-2 py-1 text-xs capitalize ${
                view === mode ? "bg-primary text-primary-foreground" : "bg-card text-muted-foreground"
              }`}
            >
              {mode}
            </button>
          ))}
        </span>
      </div>

      {view === "diagram" ? (
        <>
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            expanded={expanded}
            onToggleExpanded={() => setExpanded((value) => !value)}
            selectedRadio={selectedRadio}
            onSelect={setSelected}
          />
          <Legend />
          {topology.danglingPeers.length > 0 && (
            <p className="text-[11px] text-muted-foreground">
              {topology.danglingPeers.length} radio
              {topology.danglingPeers.length === 1 ? " is" : "s are"} associated to a
              peer this dashboard cannot see — it is outside the granted devices, or
              not running the AirMax app.
            </p>
          )}
          {topology.unplaceable.length > 0 && (
            <div className="rounded-lg border border-dashed border-border p-3">
              <p className="mb-2 text-[11px] text-muted-foreground">
                Not on the diagram — these installs publish no <code>radio_mac</code>,
                so there is no identity to place them by. They need an AirMax release
                that publishes the topology tags.
              </p>
              <div className="flex flex-wrap gap-1">
                {topology.unplaceable.map((radio) => (
                  <Badge key={radio.id} variant="secondary">
                    {radio.deviceName} · {radio.appKey}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <RadioTable radios={radios} />
      )}
    </div>
  );
}

// doover-js is bundled rather than shared, so the host's own <DooverProvider>
// is invisible to this widget's context. peekDooverClient() returns the live
// client the host keeps on globalThis — same socket, same auth, same gateway.
const NetworkOverviewWidget = (props: any) => {
  const client = peekDooverClient();
  if (!client) return null;
  return (
    <RemoteComponentWrapper>
      <DooverProvider client={client}>
        <NetworkOverviewWidgetInner {...props} />
      </DooverProvider>
    </RemoteComponentWrapper>
  );
};

export default NetworkOverviewWidget;
