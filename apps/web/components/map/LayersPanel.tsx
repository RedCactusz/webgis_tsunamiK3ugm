"use client";

import Image from "next/image";
import { useState } from "react";

interface Layer {
  id: string;
  name: string;
  icon: string;
  iconType: "image" | "custom";
  checked: boolean;
  customIcon?: React.ReactNode;
}

const staticLayers: Layer[] = [
  {
    id: "patahan",
    name: "Patahan Aktif (PUSGEN)",
    icon: "/asset/Logo/Logo PusGEN.png",
    iconType: "image",
    checked: true,
  },
  {
    id: "megathrust",
    name: "Megathrust INA",
    icon: "/asset/Logo/Logo PusGEN.png",
    iconType: "image",
    checked: true,
  },
  {
    id: "pantai",
    name: "Garis Pantai (BIG)",
    icon: "/asset/Logo/Logo BIG.png",
    iconType: "image",
    checked: true,
  },
  {
    id: "desa",
    name: "Administrasi Desa",
    icon: "square",
    iconType: "custom",
    checked: true,
    customIcon: (
      <div className="w-[18px] h-[18px] border-[1.5px] border-black bg-black rounded-[2px] inline-flex items-center justify-center mr-2" />
    ),
  },
  {
    id: "jalan",
    name: "Jaringan Jalan Bantul",
    icon: "line",
    iconType: "custom",
    checked: true,
    customIcon: (
      <div className="w-[18px] h-[18px] inline-flex items-center justify-center mr-2">
        <div className="w-[14px] h-[2px] bg-[#60a5fa] rounded-[1px]" />
      </div>
    ),
  },
  {
    id: "tes",
    name: "Tempat Evakuasi (TES)",
    icon: "/asset/Icon/Icon Titik Kumpul.png",
    iconType: "image",
    checked: true,
  },
];

export function LayersPanel() {
  const [layers, setLayers] = useState<Record<string, boolean>>(
    Object.fromEntries(staticLayers.map((l) => [l.id, l.checked]))
  );

  const toggleLayer = (id: string) => {
    setLayers((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="p-3.5">
      <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-3">
        Layer Peta
      </div>

      {/* Static Layers */}
      <div className="mb-3">
        {staticLayers.map((layer) => (
          <div
            key={layer.id}
            className="flex justify-between items-center py-1.5 border-b border-[rgba(56,189,248,0.05)] last:border-0"
          >
            <span className="flex-1 text-[11px] font-medium text-[var(--text2)] flex items-center">
              {layer.iconType === "image" ? (
                <span className="w-[18px] h-[18px] inline-flex items-center justify-center mr-2">
                  <Image
                    src={layer.icon}
                    alt={layer.name}
                    width={18}
                    height={18}
                    className="object-contain"
                    style={{
                      filter: "drop-shadow(0 0 4px rgba(56,189,248,0.3))",
                    }}
                  />
                </span>
              ) : (
                layer.customIcon
              )}
              {layer.name}
            </span>
            <label className="relative inline-block w-9 h-5 cursor-pointer flex-shrink-0">
              <input
                type="checkbox"
                checked={layers[layer.id] || false}
                onChange={() => toggleLayer(layer.id)}
                className="opacity-0 w-0 h-0"
              />
              <span
                className={`absolute cursor-pointer inset-0 rounded-full transition-all duration-300 ${
                  layers[layer.id]
                    ? "bg-[var(--accent)]"
                    : "bg-[rgba(148,200,240,0.2)]"
                }`}
                style={{ border: "1px solid var(--border)" }}
              />
              <span
                className={`absolute h-[14px] w-[14px] rounded-full transition-all duration-300 bottom-0.5 left-0.5 ${
                  layers[layer.id] ? "bg-white translate-x-4" : "bg-[#666]"
                }`}
              />
            </label>
          </div>
        ))}
      </div>

      {/* Disclaimer */}
      <div
        className="text-[10px] leading-relaxed rounded p-2 mt-2"
        style={{
          background: "rgba(30,15,0,0.4)",
          border: "1px solid rgba(251,191,36,0.18)",
          color: "rgba(255,185,110,0.65)",
        }}
      >
        ⚠️ Pastikan layer aktif sebelum menjalankan simulasi. Layer DEM dan BATNAS
        dimuat otomatis dari server.
      </div>

      {/* Legend */}
      <div className="mt-3">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-2">
          Legenda
        </div>
        <div className="flex flex-wrap gap-2">
          <LegendItem label="Zona Sangat Tinggi" color="#f87171" />
          <LegendItem label="Zona Tinggi" color="#fb923c" />
          <LegendItem label="Zona Sedang" color="#fac21e" />
          <LegendItem label="Zona Rendah" color="#ffe650" />
        </div>
      </div>
    </div>
  );
}

function LegendItem({ label, color }: { label: string; color: string }) {
  return (
    <div className="flex items-center gap-1 text-[10px] text-[var(--muted)] font-medium">
      <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
      {label}
    </div>
  );
}
