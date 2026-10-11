import type { HardwareSnapshot } from "../api";
import { formatMb } from "../format";
import { percentOf } from "../gauge-utils";
import { Gauge } from "./Gauge";

/** The machine at a glance: CPU as the headline gauge, memory and GPU beside it. */
export function HardwareStrip({ snapshot }: { snapshot: HardwareSnapshot | null }) {
  const gpu = snapshot?.gpu;
  const hasGpu = !!gpu?.available;
  return (
    <div className="panel hardware-strip" aria-label="Hardware">
      <Gauge
        size="lg"
        label="CPU"
        value={snapshot?.cpu.percent}
        detail={snapshot ? `${snapshot.cpu.core_count} cores` : undefined}
        emptyText="—"
      />
      <div className="hardware-minor">
        <Gauge
          label="Memory"
          value={snapshot?.memory.percent}
          detail={snapshot ? `${formatMb(snapshot.memory.used_mb)} / ${formatMb(snapshot.memory.total_mb)}` : undefined}
          emptyText="—"
        />
        <Gauge
          label="GPU"
          value={hasGpu ? gpu!.utilization_percent : null}
          detail={hasGpu ? (gpu!.name ?? undefined) : snapshot ? "no NVIDIA GPU detected" : undefined}
        />
        <Gauge
          label="VRAM"
          value={hasGpu ? percentOf(gpu!.vram_used_mb, gpu!.vram_total_mb) : null}
          detail={hasGpu ? `${formatMb(gpu!.vram_used_mb)} / ${formatMb(gpu!.vram_total_mb)}` : undefined}
        />
      </div>
    </div>
  );
}
