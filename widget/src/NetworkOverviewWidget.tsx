import "./styles.css";

import { useMemo } from "react";

import RemoteComponentWrapper from "customer_site/RemoteComponentWrapper";
import { useRemoteParams } from "customer_site/useRemoteParams";

import {
  DooverProvider,
  useDeviceMap,
  useMultiAgentAggregates,
  type DeviceMapEntry,
} from "doover-js/react";
import { peekDooverClient } from "doover-js";

import { Card, CardContent } from "./components/ui/card";
import { Badge } from "./components/ui/badge";

// The AirMax app's name in the app registry. A device may run several installs
// of it (an uplink and a downlink on the same Doovit is the normal case), each
// publishing its tags under its own app key.
const AIRMAX_APPLICATION = "ubiquiti_airmax";

interface OverviewDeviceEntry extends DeviceMapEntry {
  app_installs?: Array<{ name?: string | null; application_name?: string | null }>;
}

/** One radio: an AirMax install on a device, and the tags it publishes. */
export interface RadioNode {
  /** Stable across renders and unique fleet-wide — a device may hold several. */
  id: string;
  agentId: string;
  appKey: string;
  deviceName: string;
  /** Identity and topology, straight off the tags. */
  radioMac: string | null;
  apMac: string | null;
  uplinkMac: string | null;
  stations: StationRecord[];
  online: boolean;
  hostname: string | null;
  signal: number | null;
  snr: number | null;
  lastUpdated: number | null;
}

export interface StationRecord {
  mac: string | null;
  hostname?: string | null;
  signal_dbm?: number | null;
  ccq_pct?: number | null;
  tx_rate_mbps?: number | null;
  rx_rate_mbps?: number | null;
}

/** `stations_json` is a JSON string in a string tag, and may be absent. */
function parseStations(raw: unknown): StationRecord[] {
  if (typeof raw !== "string" || !raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Every AirMax install across the fleet, joined to the tags it publishes.
 *
 * The app keys come from DEVICE_MAP rather than being guessed from the shape of
 * the `tag_values` aggregate: an install's key is operator-chosen
 * (`airmax_upstream`, not `ubiquiti_airmax_1`), so prefix-matching would miss
 * exactly the multi-radio devices this view exists to draw.
 */
function useRadios(agentId: string | undefined, appKey: string | undefined) {
  const { devices, deviceIds, isLoading, hasDeviceMap } =
    useDeviceMap<OverviewDeviceEntry>(agentId, appKey);

  // The union of AirMax app keys in the fleet, used to project the tag fetch
  // down to the subtrees this widget actually reads. `tag_values` holds every
  // app on the device; without this a Doovit running a dozen apps ships all of
  // them on every poll.
  const { installsByDevice, fields } = useMemo(() => {
    const byDevice = new Map<string, string[]>();
    const keys = new Set<string>();
    for (const device of devices) {
      const names = (device.app_installs ?? [])
        .filter(
          (install) =>
            (install.application_name ?? "").toLowerCase() === AIRMAX_APPLICATION,
        )
        .map((install) => install.name)
        .filter((name): name is string => Boolean(name));
      if (names.length) {
        byDevice.set(device.id, names);
        names.forEach((name) => keys.add(name));
      }
    }
    return { installsByDevice: byDevice, fields: [...keys].sort() };
  }, [devices]);

  const radioIds = useMemo(
    () => deviceIds.filter((id) => installsByDevice.has(id)),
    [deviceIds, installsByDevice],
  );

  const { aggregatesByAgent, query } = useMultiAgentAggregates(
    "tag_values",
    radioIds,
    { fields },
  );

  const radios = useMemo<RadioNode[]>(() => {
    const byId = new Map(devices.map((device) => [device.id, device]));
    const out: RadioNode[] = [];
    for (const [deviceId, appKeys] of installsByDevice) {
      const aggregate = aggregatesByAgent[deviceId];
      const device = byId.get(deviceId);
      for (const key of appKeys) {
        const tags = (aggregate?.data as Record<string, any> | undefined)?.[key];
        if (!tags) continue;
        out.push({
          id: `${deviceId}:${key}`,
          agentId: deviceId,
          appKey: key,
          deviceName: device?.display_name || device?.name || deviceId,
          radioMac: str(tags.radio_mac),
          apMac: str(tags.ap_mac),
          uplinkMac: str(tags.uplink_mac),
          stations: parseStations(tags.stations_json),
          online: tags.online === true,
          hostname: str(tags.hostname),
          signal: num(tags.signal),
          snr: num(tags.snr),
          lastUpdated: aggregate?.last_updated ?? null,
        });
      }
    }
    return out.sort((a, b) => a.deviceName.localeCompare(b.deviceName));
  }, [devices, installsByDevice, aggregatesByAgent]);

  return { radios, isLoading: isLoading || query.isLoading, hasDeviceMap };
}

function NetworkOverviewWidgetInner() {
  const params = useRemoteParams();
  const { radios, isLoading, hasDeviceMap } = useRadios(
    params.agent_id,
    params.app_key,
  );

  if (isLoading) {
    return <div className="p-4 text-sm text-muted-foreground">Loading radios…</div>;
  }

  if (!hasDeviceMap) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No devices granted yet — set <strong>Apps Installed</strong> to the
        Ubiquiti AirMax app in this dashboard's configuration.
      </div>
    );
  }

  const online = radios.filter((radio) => radio.online).length;
  const linked = radios.filter((radio) => radio.apMac || radio.uplinkMac).length;
  const unidentified = radios.filter((radio) => !radio.radioMac).length;

  return (
    <div className="p-4 space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{radios.length} radios</Badge>
        <Badge>{online} online</Badge>
        <Badge>{linked} with a peer</Badge>
        {unidentified > 0 && (
          <Badge variant="destructive">
            {unidentified} without radio_mac — redeploy AirMax
          </Badge>
        )}
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="p-2">Device</th>
                <th className="p-2">Install</th>
                <th className="p-2">Radio MAC</th>
                <th className="p-2">Uplink (ap_mac)</th>
                <th className="p-2">Stations</th>
                <th className="p-2">Signal</th>
                <th className="p-2">Online</th>
              </tr>
            </thead>
            <tbody>
              {radios.map((radio) => (
                <tr key={radio.id} className="border-t">
                  <td className="p-2">{radio.deviceName}</td>
                  <td className="p-2">{radio.hostname || radio.appKey}</td>
                  <td className="p-2 font-mono text-xs">{radio.radioMac ?? "—"}</td>
                  <td className="p-2 font-mono text-xs">
                    {radio.apMac ?? radio.uplinkMac ?? "—"}
                  </td>
                  <td className="p-2">{radio.stations.length || "—"}</td>
                  <td className="p-2">
                    {radio.signal !== null ? `${radio.signal} dBm` : "—"}
                  </td>
                  <td className="p-2">{radio.online ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

// doover-js is bundled rather than shared, so the host's own <DooverProvider>
// is invisible to this widget's context. peekDooverClient() returns the live
// client the host keeps on globalThis, which is then re-provided here — same
// socket, same auth, same gateway.
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
