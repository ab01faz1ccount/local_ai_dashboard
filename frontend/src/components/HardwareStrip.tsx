import type { HardwareSnapshot } from "../api";
import { formatMb, formatPercent } from "../format";

export function HardwareStrip({ snapshot }: { snapshot: HardwareSnapshot | null }) {
  return (
    <div className="hardware-strip">
      <div className="panel hardware-tile">
        <div className="hardware-tile-label">CPU</div>
        <div className="hardware-tile-value">{formatPercent(snapshot?.cpu.percent)}</div>
        <div className="hardware-tile-sub">{snapshot?.cpu.core_count ?? "—"} cores</div>
      </div>
      <div className="panel hardware-tile">
        <div className="hardware-tile-label">Memory</div>
        <div className="hardware-tile-value">{formatPercent(snapshot?.memory.percent)}</div>
        <div className="hardware-tile-sub">
          {formatMb(snapshot?.memory.used_mb)} / {formatMb(snapshot?.memory.total_mb)}
        </div>
      </div>
      <div className="panel hardware-tile">
        <div className="hardware-tile-label">GPU</div>
        {snapshot?.gpu.available ? (
          <>
            <div className="hardware-tile-value">{formatPercent(snapshot.gpu.utilization_percent)}</div>
            <div className="hardware-tile-sub">
              {snapshot.gpu.name} · {formatMb(snapshot.gpu.vram_used_mb)} / {formatMb(snapshot.gpu.vram_total_mb)}
            </div>
          </>
        ) : (
          <>
            <div className="hardware-tile-value muted">N/A</div>
            <div className="hardware-tile-sub">no NVIDIA GPU detected</div>
          </>
        )}
      </div>
    </div>
  );
}
