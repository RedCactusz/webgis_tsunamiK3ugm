"use client";

import Image from "next/image";
import { useUIStore, useServerStore } from "@/stores";
import { Settings, Info } from "lucide-react";

export function Header() {
  const { toggleSidebar } = useUIStore();
  const { serverStatus, precomputeStatus, vectorStatus } = useServerStore();

  return (
    <header className="flex items-center gap-3.5 px-4.5 py-2.5 border-b border-[rgba(56,189,248,0.14)] bg-gradient-to-r from-[rgba(6,13,27,0.98)] to-[rgba(10,22,44,0.98)] shadow-lg z-[1000] flex-shrink-0">
      {/* Logos */}
      <div className="flex gap-2 items-center" style={{ background: "none", padding: "0", width: "auto", height: "auto", display: "flex", gap: "8px" }}>
        <Image
          src="/asset/Icon/Icon Risiko Bencana.png"
          alt="Risiko Bencana"
          width={38}
          height={38}
          className="rounded-md object-contain"
        />
        <Image
          src="/asset/Logo/Logo UGM.png"
          alt="Logo UGM"
          width={38}
          height={38}
          className="object-contain"
        />
      </div>

      {/* Title */}
      <div className="flex flex-col">
        <h1 className="text-xs font-extrabold text-white tracking-wide">
          WebGIS Simulasi Bencana Tsunami
        </h1>
        <p className="text-[10px] text-muted font-medium mt-0.5">
          Mini Project Komputasi Geospasial Kelompok 3
        </p>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Status Badges */}
      <div className="flex gap-2">
        <StatusBadge
          dotId="batnas-dot"
          label="SERVER DATA"
          status={serverStatus}
          title="Status Koneksi Server Bathymetry & DEM"
        />
        <StatusBadge
          dotId="precompute-dot"
          label="PRE-LOADING"
          status={precomputeStatus}
          title="Status Pre-computasi Grid (GEBCO/BATNAS/DEM)"
        />
        <StatusBadge
          dotId="vektor-dot"
          label="VEKTOR"
          status={vectorStatus}
          title="Status Layar Vektor (SHP)"
        />
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={toggleSidebar}
          className="px-3 py-1.5 text-xs font-bold rounded border border-[rgba(56,189,248,0.3)] bg-[rgba(56,189,248,0.12)] text-accent hover:bg-[rgba(56,189,248,0.25)] transition-all"
        >
          ☰
        </button>
      </div>
    </header>
  );
}

function StatusBadge({
  dotId,
  label,
  status,
  title,
}: {
  dotId: string;
  label: string;
  status: "online" | "offline" | "warning";
  title: string;
}) {
  const statusColor = {
    online: "bg-[#34d399] shadow-[0_0_8px_#34d399] animate-pulse",
    offline: "bg-[#666666]",
    warning: "bg-[#fbbf24] shadow-[0_0_6px_#fbbf24]",
  };

  return (
    <div
      className="px-3 py-1.5 rounded-md border border-[rgba(56,189,248,0.2)] bg-[rgba(10,22,40,0.95)] backdrop-blur-sm shadow-[0_2px_8px_rgba(0,0,0,0.3)] hover:border-[rgba(56,189,248,0.4)] hover:-translate-y-px hover:shadow-[0_4px_12px_rgba(0,0,0,0.4)] transition-all duration-200 flex items-center gap-1.5"
      title={title}
    >
      <div
        id={dotId}
        className={`w-2 h-2 rounded-full transition-all duration-300 ${statusColor[status]}`}
      />
      <span className="text-[10px] font-bold tracking-wider text-[#a8ccee] uppercase">
        {label}
      </span>
    </div>
  );
}
