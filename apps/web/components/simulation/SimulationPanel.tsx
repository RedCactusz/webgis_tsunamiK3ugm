"use client";

import { useState } from "react";

export function SimulationPanel() {
  const [mode, setMode] = useState<"fault" | "mega" | "custom">("fault");
  const [magnitude, setMagnitude] = useState(7.5);
  const [faultType, setFaultType] = useState<"vertical" | "horizontal">("vertical");

  return (
    <div>
      {/* HEADER PANEL KIRI — PEMODELAN TSUNAMI */}
      <div
        className="p-3 border-b border-[rgba(56,189,248,0.18)]"
        style={{ background: "rgba(0,30,60,0.35)" }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0"
            style={{ background: "linear-gradient(135deg,#003060,#0077cc)" }}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="15"
              height="15"
              viewBox="0 0 64 64"
              className="block"
            >
              <path
                d="M4,36 Q12,24 20,36 Q28,48 36,36 Q44,24 52,36 Q58,44 62,36"
                stroke="white"
                strokeWidth="5"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M4,48 Q12,36 20,48 Q28,60 36,48 Q44,36 52,48 Q58,56 62,48"
                stroke="rgba(255,255,255,0.45)"
                strokeWidth="3.5"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <div>
            <div
              className="text-[10px] font-bold tracking-wider"
              style={{ color: "#38bdf8", letterSpacing: "2px" }}
            >
              PEMODELAN TSUNAMI
            </div>
            <div className="text-[10px] text-[var(--muted)]">
              Simulasi Gelombang · Zona Inundasi
            </div>
          </div>
        </div>
      </div>

      {/* SOURCE SELECTOR */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-3">
          Sumber Gempa
        </div>
        <div className="flex gap-0.5 bg-[rgba(0,20,50,0.5)] rounded-md p-0.5 mb-3">
          {["fault", "mega", "custom"].map((m) => (
            <button
              key={m}
              onClick={() => setMode(m as any)}
              className={`flex-1 py-1.5 text-center rounded-md text-[11px] font-semibold transition-all ${
                mode === m
                  ? "bg-[rgba(56,189,248,0.18)] text-[var(--accent)] shadow-[inset_0_0_0_1px_rgba(56,189,248,0.3)]"
                  : "text-[var(--muted)] hover:text-[var(--text2)]"
              }`}
            >
              {m === "fault" ? "Patahan" : m === "mega" ? "Megathrust" : "Custom"}
            </button>
          ))}
        </div>

        {mode === "fault" && (
          <div className="flex flex-col gap-1 max-h-[165px] overflow-y-auto pr-0.5">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="px-3 py-2 rounded-md border cursor-pointer font-medium text-[12px] transition-all hover:border-[rgba(56,189,248,0.35)] hover:bg-[rgba(56,189,248,0.07)]"
                style={{
                  background: "rgba(0,20,50,0.4)",
                  borderColor: "rgba(56,189,248,0.08)",
                }}
              >
                Fault Zone {i}
                <div className="text-[10px] text-[var(--muted)] mt-1">
                  Mw 7.2 · Dip 15° · Strike 290°
                </div>
              </div>
            ))}
          </div>
        )}

        {mode === "custom" && (
          <div
            className="text-[10px] text-[var(--muted)] p-2 rounded-md leading-relaxed"
            style={{
              background: "rgba(0,18,45,0.5)",
              border: "1px solid var(--border)",
            }}
          >
            📍 Klik di peta untuk menentukan episentrum
            <br />
            <span className="text-[var(--accent)]">Belum dipilih</span>
          </div>
        )}
      </div>

      {/* DEPTH PROBE */}
      <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-3">
          Probe Kedalaman (Hover Peta)
        </div>
        <div
          className="p-3 rounded-md"
          style={{ background: "rgba(0,18,45,0.55)", border: "1px solid var(--border)" }}
        >
          <div className="flex justify-between items-center mb-1.5">
            <span className="text-[9px] text-[var(--muted)]">Koordinat</span>
            <span className="text-[10px] text-[var(--accent)]">—</span>
          </div>
          <div className="flex justify-between items-center mb-1.5">
            <span className="text-[9px] text-[var(--muted)]">Kedalaman</span>
            <div className="flex items-center gap-1.5">
              <span className="text-[14px] font-bold text-[var(--accent)]">—</span>
              <span
                className="text-[9px] px-1.5 py-0.5 rounded"
                style={{ background: "rgba(80,80,80,0.2)", color: "#666" }}
              >
                —
              </span>
            </div>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[9px] text-[var(--muted)]">Kec. gelombang</span>
            <span className="text-[11px] text-[var(--text)]">—</span>
          </div>
        </div>
      </div>

      {/* PARAMS */}
      <div className="p-3.5">
        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-3">
          Parameter Gempa
        </div>

        <div className="mb-2.5">
          <div className="flex justify-between items-center text-[11px] text-[var(--text2)] mb-1.5">
            <span>Magnitudo</span>
            <span className="text-[14px] font-bold text-[var(--accent)]" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {magnitude} Mw
            </span>
          </div>
          <input
            type="range"
            min="5"
            max="9.5"
            step="0.1"
            value={magnitude}
            onChange={(e) => setMagnitude(parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="flex gap-1 flex-wrap mt-1.5">
            {[6, 6.5, 7, 7.5, 8, 8.5, 9].map((val) => (
              <button
                key={val}
                onClick={() => setMagnitude(val)}
                className="px-2 py-0.5 rounded border text-[11px] font-semibold transition-all hover:border-[var(--accent)] hover:text-[var(--accent)] hover:bg-[rgba(56,189,248,0.07)]"
                style={{ background: "transparent", borderColor: "var(--border)", color: "var(--muted)" }}
              >
                {val}
              </button>
            ))}
          </div>
        </div>

        <div className="text-[10px] font-bold text-[var(--muted)] tracking-wider uppercase mb-2">
          Jenis Sesar
        </div>
        <div className="grid grid-cols-2 gap-2 mb-2">
          <button
            onClick={() => setFaultType("vertical")}
            className={`p-2.5 rounded-md text-center text-[11px] font-semibold transition-all ${
              faultType === "vertical"
                ? "bg-[rgba(56,189,248,0.14)] border border-[var(--accent)] text-[var(--accent)]"
                : "border hover:border-[rgba(56,189,248,0.3)] hover:text-[var(--text2)] hover:bg-[rgba(56,189,248,0.06)]"
            }`}
            style={{ background: faultType !== "vertical" ? "rgba(0,15,40,0.5)" : "" }}
          >
            <span className="text-base block mb-0.5">↕</span>
            Vertikal
            <br />
            <span className="text-[10px] opacity-60">Thrust / Normal</span>
          </button>
          <button
            onClick={() => setFaultType("horizontal")}
            className={`p-2.5 rounded-md text-center text-[11px] font-semibold transition-all ${
              faultType === "horizontal"
                ? "bg-[rgba(56,189,248,0.14)] border border-[var(--accent)] text-[var(--accent)]"
                : "border hover:border-[rgba(56,189,248,0.3)] hover:text-[var(--text2)] hover:bg-[rgba(56,189,248,0.06)]"
            }`}
            style={{ background: faultType !== "horizontal" ? "rgba(0,15,40,0.5)" : "" }}
          >
            <span className="text-base block mb-0.5">↔</span>
            Horizontal
            <br />
            <span className="text-[10px] opacity-60">Strike-slip</span>
          </button>
        </div>

        <div
          className="text-[9px] text-[var(--muted)] p-1.5 rounded mb-2 leading-relaxed"
          style={{ background: "rgba(0,20,40,0.5)" }}
        >
          ⚡ Vertikal → perpindahan lantai laut lebih besar → potensi tsunami lebih
          tinggi
        </div>

        <button
          className="w-full py-3 rounded-md text-[13px] font-bold uppercase tracking-wider shadow-lg transition-all hover:-translate-y-px hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed"
          style={{
            background: "linear-gradient(135deg,#005c99,#0088cc)",
            color: "#fff",
            boxShadow: "0 4px 20px rgba(14,165,233,0.3)",
          }}
          disabled
        >
          ▶ JALANKAN SIMULASI (SWE NUMERIK)
        </button>
        <div className="text-[10px] text-[var(--muted)] mt-1.5 leading-relaxed"></div>
      </div>
    </div>
  );
}
