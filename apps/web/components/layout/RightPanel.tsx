"use client";

import { useState } from "react";
import Image from "next/image";
import {
  Footprints,
  Bike,
  Car,
  MapPin,
  Play,
  Download,
  FileText,
  Database,
} from "lucide-react";

type TransportMode = "foot" | "motor" | "car";
type RPMode = "network" | "abm";

interface TESItem {
  id: string;
  name: string;
  capacity: number;
  active: boolean;
}

const mockTES: TESItem[] = [
  { id: "srv-0", name: "TES Masjid Al Huda", capacity: 500, active: false },
  { id: "srv-1", name: "TES BPP Srandakan", capacity: 750, active: false },
  { id: "srv-2", name: "TES SD Muh Gunturgeni", capacity: 400, active: false },
  { id: "srv-3", name: "TES Masjid Al Firdaus", capacity: 600, active: false },
  { id: "srv-4", name: "TES SD Koripan", capacity: 350, active: false },
];

export function RightPanel() {
  const [rpMode, setRPMode] = useState<RPMode>("network");
  const [transportMode, setTransportMode] = useState<TransportMode>("foot");
  const [selectedTES, setSelectedTES] = useState<string>("");
  const [originPicking, setOriginPicking] = useState(false);
  const [safeWeight, setSafeWeight] = useState(25);

  return (
    <aside className="w-[295px] flex-shrink-0 bg-[#0a1628] border-l border-[rgba(56,189,248,0.14)] overflow-y-auto flex flex-col">
      {/* HEADER PANEL */}
      <div className="p-3.5 border-b border-[rgba(52,211,153,0.18)] bg-[rgba(3,46,25,0.25)]">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-gradient-to-br from-[#004d20] to-[#00cc66] flex items-center justify-center text-xs">
            🏠
          </div>
          <div className="flex-1">
            <div className="text-[10px] font-bold text-[#44ff88] tracking-wider uppercase">
              RUTE EVAKUASI
            </div>
            <div className="text-[10px] text-[var(--muted)]">
              Analisis Jaringan · DEM + Lereng
            </div>
          </div>
          {/* Tab switcher */}
          <div className="flex gap-0.5 ml-auto">
            <button
              onClick={() => setRPMode("network")}
              className={`text-[9px] px-2 py-1 flex-none rounded transition-all ${
                rpMode === "network"
                  ? "bg-[rgba(56,189,248,0.18)] text-[var(--accent)] border border-[var(--accent)]"
                  : "bg-[rgba(0,18,45,0.6)] text-[var(--muted)] border border-[var(--border)] hover:text-[var(--text2)]"
              }`}
            >
              🛣 Rute
            </button>
            <button
              onClick={() => setRPMode("abm")}
              className={`text-[9px] px-2 py-1 flex-none rounded transition-all ${
                rpMode === "abm"
                  ? "bg-[rgba(56,189,248,0.18)] text-[var(--accent)] border border-[var(--accent)]"
                  : "bg-[rgba(0,18,45,0.6)] text-[var(--muted)] border border-[var(--border)] hover:text-[var(--text2)]"
              }`}
            >
              🤖 ABM
            </button>
          </div>
        </div>
      </div>

      {rpMode === "network" ? <NetworkPanel /> : <ABMPanel />}
    </aside>
  );
}

function NetworkPanel() {
  const [transportMode, setTransportMode] = useState<TransportMode>("foot");
  const [safeWeight, setSafeWeight] = useState(25);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Status OSM roads */}
      <div className="p-3.5 pb-1 border-b border-[rgba(56,189,248,0.08)]">
        <div className="flex items-center gap-1.5 px-2 py-1 bg-[rgba(0,18,45,0.6)] rounded-md border border-[var(--border2)] text-[10px]">
          <div className="w-1.5 h-1.5 rounded-full bg-[#34d399] shadow-[0_0_6px_#34d399]" />
          <span className="flex-1 text-[var(--muted)]">Data jalan OSM dimuat</span>
          <span className="text-[9px] text-[var(--ok)] font-semibold">12.4 km</span>
        </div>
      </div>

      {/* ORIGIN DESTINATION */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-3">
          Titik Asal & Tujuan
        </div>

        {/* Origin */}
        <div className="mb-3">
          <div className="text-[9px] text-[var(--muted)] mb-1.5">📍 Titik Asal (Zona Bahaya)</div>
          <button className="w-full flex items-center gap-2 bg-[rgba(56,189,248,0.12)] border-[1.5px] dashed border-[var(--accent)] rounded-md text-[var(--accent)] py-2 px-2.5 text-[11px] hover:bg-[rgba(56,189,248,0.2)] transition-all">
            <Footprints className="w-4 h-4" />
            <span>Klik peta untuk tentukan titik asal</span>
          </button>
        </div>

        {/* Destination */}
        <div>
          <div className="text-[9px] text-[var(--muted)] mb-1.5">🏁 Titik Tujuan (TES)</div>
          <select className="w-full bg-[rgba(0,18,45,0.7)] border border-[var(--border)] rounded-md text-[var(--text)] py-1.5 px-2 text-[11px] focus:outline-none focus:border-[var(--accent)]">
            <option value="">— Pilih TES —</option>
            <option value="srv-0">TES-01 — TES Masjid Al Huda</option>
            <option value="srv-1">TES-02 — TES BPP Srandakan</option>
            <option value="srv-2">TES-03 — TES SD Muh Gunturgeni</option>
            <option value="srv-3">TES-04 — TES Masjid Al Firdaus</option>
            <option value="srv-4">TES-05 — TES SD Koripan</option>
          </select>
        </div>
      </div>

      {/* MODA TRANSPORTASI */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-2">
          Moda Transportasi
        </div>
        <div className="grid grid-cols-3 gap-1.5 mb-2">
          <TransportButton mode="foot" selected={transportMode === "foot"} onSelect={() => setTransportMode("foot")} label="Jalan Kaki" speed="~4 km/j" icon="/asset/Icon/Icon Jalan Kaki.png" iconType="lucide" />
          <TransportButton mode="motor" selected={transportMode === "motor"} onSelect={() => setTransportMode("motor")} label="Motor" speed="~30 km/j" icon="/asset/Icon/Icon Motor.png" iconType="lucide" />
          <TransportButton mode="car" selected={transportMode === "car"} onSelect={() => setTransportMode("car")} label="Mobil" speed="~40 km/j" icon="/asset/Icon/Icon Mobil.png" iconType="lucide" />
        </div>
        <div className="text-[10px] text-[var(--muted)] px-2 py-1.5 bg-[rgba(0,20,40,0.5)] rounded-md border border-[var(--border)] leading-relaxed">
          🚶 <span className="text-[var(--accent)] font-semibold">Jalan Kaki</span> — Kecepatan ~4 km/j. Jalur kaki lebih fleksibel.
        </div>
      </div>

      {/* METODE ANALISIS */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-2">
          Metode Analisis
        </div>

        <div className="px-2.5 py-2 bg-[rgba(56,189,248,0.07)] rounded-md border border-[rgba(56,189,248,0.2)] mb-2">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-base">🛣</span>
            <span className="text-[11px] font-bold text-[var(--accent)]">Jalur Terpendek + DEM</span>
            <span className="text-[9px] px-1.5 py-0.5 bg-[rgba(74,222,128,0.15)] text-[#4ade80] rounded-full border border-[rgba(74,222,128,0.3)] ml-auto">
              ✓ Aktif
            </span>
          </div>
          <div className="text-[9.5px] text-[var(--muted)] leading-relaxed">
            Network analysis dengan <span className="text-[var(--text)] font-semibold">composite cost</span>: bobot jarak, waktu tempuh, <span className="text-[#34d399] font-semibold">elevasi DEM</span>, dan <span className="text-[#a78bfa] font-semibold">kemiringan lereng</span> — menghasilkan rute evakuasi yang memprioritaskan jalur naik ke zona aman.
          </div>
          <div className="flex gap-1 flex-wrap mt-2">
            <WeightBadge label="⏱ Waktu 30%" color="bg-[rgba(56,189,248,0.12)] text-[var(--accent)]" />
            <WeightBadge label="📏 Jarak 30%" color="bg-[rgba(251,146,60,0.12)] text-[#fb923c]" />
            <WeightBadge label="⛰ Elevasi 25%" color="bg-[rgba(52,211,153,0.12)] text-[#34d399]" />
            <WeightBadge label="📐 Lereng 15%" color="bg-[rgba(167,139,250,0.12)] text-[#a78bfa]" />
          </div>
        </div>

        <div className="mb-2">
          <div className="flex flex-col gap-1 mb-1">
            <div className="flex justify-between items-center text-[11px] text-[var(--text2)]">
              <span>
                ⛰ Prioritas Elevasi & Slope
                <span className="text-[8px] text-[var(--muted)]"> (semakin kanan = hindari zona rendah)</span>
              </span>
              <span className="font-bold text-[var(--accent)]" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                {safeWeight}%
              </span>
            </div>
          </div>
          <input
            type="range"
            min="0"
            max="60"
            value={safeWeight}
            onChange={(e) => setSafeWeight(parseInt(e.target.value))}
            className="w-full"
            style={{ accentColor: "#4ade80" }}
          />
        </div>

        <button className="w-full py-3 rounded-md bg-gradient-to-br from-[#064e3b] to-[#059669] text-white text-[11px] font-bold tracking-wider uppercase shadow-[0_4px_18px_rgba(5,150,105,0.3)] hover:-translate-y-px hover:shadow-[0_6px_24px_rgba(16,185,129,0.4)] transition-all">
          🛣 ANALISIS RUTE EVAKUASI
        </button>
      </div>

      {/* TES LIST */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="flex justify-between items-center mb-2">
          <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase">
            Titik Evakuasi Sementara (TES)
          </div>
          <div className="text-[10px] text-[var(--ok)] font-bold">16 TES</div>
        </div>
        <div className="flex flex-col gap-0.5 max-h-[160px] overflow-y-auto">
          {mockTES.map((tes) => (
            <div
              key={tes.id}
              className="px-2.5 py-1.5 rounded border border-[rgba(56,189,248,0.08)] bg-[rgba(0,15,40,0.4)] hover:border-[rgba(52,211,153,0.35)] hover:bg-[rgba(52,211,153,0.06)] cursor-pointer transition-all"
            >
              <div className="text-[11px] font-semibold text-[var(--text)]">{tes.name}</div>
              <div className="text-[10px] text-[var(--muted)] mt-0.5">
                Kapasitas: <span className="text-[var(--ok)] font-bold">{tes.capacity} jiwa</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* EXPORT */}
      <div className="p-3.5">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-2">
          Export Data
        </div>
        <div className="flex gap-1.5">
          <ExportButton label="🗺 GeoJSON" color="text-[#60c8ff]" />
          <ExportButton label="📄 Shapefile" color="text-[#a8ccee]" />
          <ExportButton label="📊 Laporan" color="text-[#fb923c]" />
        </div>
      </div>
    </div>
  );
}

function ABMPanel() {
  return (
    <div className="flex-1 overflow-y-auto p-3.5">
      <div className="text-center py-8 text-[var(--muted)] text-[11px]">
        <div className="text-4xl mb-3">🤖</div>
        <div className="font-semibold text-[var(--text)] mb-2">Agent-Based Modeling</div>
        <div className="leading-relaxed">
          Simulasi evakuasi multi-agent akan tersedia di update berikutnya.
        </div>
      </div>
    </div>
  );
}

function TransportButton({
  mode,
  selected,
  onSelect,
  label,
  speed,
  icon,
  iconType,
}: {
  mode: TransportMode;
  selected: boolean;
  onSelect: () => void;
  label: string;
  speed: string;
  icon: string;
  iconType: "lucide" | "image";
}) {
  return (
    <button
      onClick={onSelect}
      className={`p-2 rounded-md transition-all text-center ${
        selected
          ? "bg-[rgba(56,189,248,0.14)] border border-[var(--accent)] text-[var(--accent)]"
          : "bg-[rgba(0,15,40,0.5)] border border-[var(--border)] text-[var(--muted)] hover:border-[rgba(56,189,248,0.3)] hover:text-[var(--text2)] hover:bg-[rgba(56,189,248,0.06)]"
      }`}
    >
      <div className="flex justify-center mb-1">
        {iconType === "lucide" ? (
          mode === "foot" ? (
            <Footprints className="w-8 h-8" />
          ) : mode === "motor" ? (
            <Bike className="w-8 h-8" />
          ) : (
            <Car className="w-8 h-8" />
          )
        ) : (
          <Image src={icon} alt={label} width={32} height={32} className="object-contain" />
        )}
      </div>
      <div className="text-[11px] font-bold">{label}</div>
      <div className="text-[7px] opacity-60 mt-0.5">{speed}</div>
    </button>
  );
}

function WeightBadge({
  label,
  color,
}: {
  label: string;
  color: string;
}) {
  return (
    <span className={`text-[9px] px-2 py-0.5 rounded-full ${color}`}>
      {label}
    </span>
  );
}

function ExportButton({ label, color }: { label: string; color: string }) {
  return (
    <button className="flex-1 py-2 rounded-md border border-[var(--border)] bg-[rgba(0,18,45,0.6)] text-[var(--muted)] text-[10px] font-semibold hover:border-[var(--accent)] hover:text-[var(--accent)] hover:bg-[rgba(56,189,248,0.07)] transition-all">
      {label}
    </button>
  );
}
