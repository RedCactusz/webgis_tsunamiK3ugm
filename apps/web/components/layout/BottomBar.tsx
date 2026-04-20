"use client";

import { useEffect, useRef } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";
import { Bar, Doughnut } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend
);

interface VillageData {
  name: string;
  population: number;
  affected: number;
  percentage: number;
  zone: "ST" | "T" | "S" | "R";
}

const mockData: VillageData[] = [
  { name: "Parangtritis", population: 12450, affected: 11205, percentage: 90, zone: "ST" },
  { name: "Parangkusumo", population: 8930, affected: 8037, percentage: 90, zone: "ST" },
  { name: "Girikarto", population: 15234, affected: 12187, percentage: 80, zone: "T" },
  { name: "Panggang", population: 11567, affected: 9254, percentage: 80, zone: "T" },
  { name: "Srigading", population: 9876, affected: 5926, percentage: 60, zone: "S" },
];

export function BottomBar() {
  const barChartRef = useRef<ChartJS>(null);
  const donutChartRef = useRef<ChartJS>(null);

  // Prepare bar chart data
  const barChartData = {
    labels: mockData.map((d) => d.name),
    datasets: [
      {
        label: "Penduduk",
        data: mockData.map((d) => d.population),
        backgroundColor: "rgba(56, 189, 248, 0.6)",
        borderColor: "rgba(56, 189, 248, 1)",
        borderWidth: 1,
      },
      {
        label: "Terdampak",
        data: mockData.map((d) => d.affected),
        backgroundColor: "rgba(248, 113, 113, 0.6)",
        borderColor: "rgba(248, 113, 113, 1)",
        borderWidth: 1,
      },
    ],
  };

  // Prepare donut chart data
  const donutData = {
    labels: ["Sangat Tinggi", "Tinggi", "Sedang", "Rendah"],
    datasets: [
      {
        data: [19242, 21441, 11340, 5222],
        backgroundColor: [
          "rgba(248, 113, 113, 0.8)",
          "rgba(251, 146, 60, 0.8)",
          "rgba(250, 204, 21, 0.8)",
          "rgba(255, 230, 80, 0.8)",
        ],
        borderColor: [
          "rgba(248, 113, 113, 1)",
          "rgba(251, 146, 60, 1)",
          "rgba(250, 204, 21, 1)",
          "rgba(255, 230, 80, 1)",
        ],
        borderWidth: 0,
      },
    ],
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "rgba(6, 13, 27, 0.95)",
        titleColor: "#a8ccee",
        bodyColor: "#ddeeff",
        borderColor: "rgba(56, 189, 248, 0.2)",
        borderWidth: 1,
        padding: 10,
        displayColors: true,
        callbacks: {
          label: (context: any) => `${context.dataset.label}: ${context.raw.toLocaleString()}`,
        },
      },
    },
    scales: {
      x: {
        stacked: true,
        ticks: { color: "#666", font: { size: 8 }, maxTicksLimit: 8 },
        grid: { color: "rgba(56, 189, 248, 0.05)" },
      },
      y: {
        stacked: true,
        ticks: { color: "#666", font: { size: 9 } },
        grid: { color: "rgba(56, 189, 248, 0.05)" },
      },
    },
  };

  const donutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: "62%",
    plugins: {
      legend: {
        position: "right" as const,
        labels: {
          color: "#a8ccee",
          font: { size: 11 },
          boxWidth: 12,
          padding: 8,
        },
      },
      tooltip: {
        backgroundColor: "rgba(6, 13, 27, 0.95)",
        titleColor: "#a8ccee",
        bodyColor: "#ddeeff",
        borderColor: "rgba(56, 189, 248, 0.2)",
        borderWidth: 1,
        padding: 10,
        callbacks: {
          label: (context: any) => ` ${context.label}: ${context.raw.toLocaleString()} jiwa`,
        },
      },
    },
  };

  return (
    <div className="h-[215px] flex-shrink-0 bg-[rgba(6,13,27,0.98)] border-t border-[rgba(56,189,248,0.14)] flex overflow-hidden">
      {/* SECTION 1: Tabel Penduduk */}
      <div className="flex-[1.4] p-[11px_14px] border-r border-[rgba(56,189,248,0.08)] overflow-hidden flex flex-col">
        <div className="text-[10px] font-bold tracking-[0.8px] text-[var(--muted)] mb-2 uppercase flex-shrink-0">
          🏘 Data Penduduk Terdampak per Desa
          <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-[rgba(0,180,255,0.1)] text-[var(--accent)] border border-[rgba(0,180,255,0.2)]">
            Menunggu Simulasi
          </span>
        </div>
        <div className="flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-[11px]" id="pop-table">
            <thead>
              <tr>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-left text-[9px] tracking-wider uppercase">
                  Desa/Kelurahan
                </th>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-right text-[9px] tracking-wider uppercase">
                  Penduduk
                </th>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-right text-[9px] tracking-wider uppercase">
                  Terdampak
                </th>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-right text-[9px] tracking-wider uppercase">
                  %
                </th>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-center text-[9px] tracking-wider uppercase">
                  Zona
                </th>
                <th className="text-[var(--muted)] font-bold p-1 border-b border-[rgba(56,189,248,0.14)] text-center text-[9px] tracking-wider uppercase">
                  Proporsi
                </th>
              </tr>
            </thead>
            <tbody>
              {mockData.map((village, idx) => (
                <tr
                  key={idx}
                  className="hover:bg-[rgba(56,189,248,0.04)] transition-colors"
                >
                  <td className="p-1 text-[var(--text2)] border-b border-[rgba(56,189,248,0.04)]">
                    {village.name}
                  </td>
                  <td className="p-1 text-[var(--text2)] border-b border-[rgba(56,189,248,0.04)] text-right">
                    {village.population.toLocaleString()}
                  </td>
                  <td className="p-1 text-[var(--accent)] border-b border-[rgba(56,189,248,0.04)] text-right font-semibold">
                    {village.affected.toLocaleString()}
                  </td>
                  <td className="p-1 text-[var(--text2)] border-b border-[rgba(56,189,248,0.04)] text-right">
                    {village.percentage}%
                  </td>
                  <td className="p-1 border-b border-[rgba(56,189,248,0.04)] text-center">
                    <ZoneBadge zone={village.zone} />
                  </td>
                  <td className="p-1 border-b border-[rgba(56,189,248,0.04)]">
                    <div className="w-16 inline-block align-middle">
                      <div className="h-1 bg-[rgba(56,189,248,0.1)] rounded overflow-hidden">
                        <div
                          className="h-full rounded transition-all duration-700"
                          style={{
                            width: `${village.percentage}%`,
                            background: "linear-gradient(90deg, #0284c7, #38bdf8)",
                          }}
                        />
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* SECTION 2: Bar Chart */}
      <div className="flex-1 p-[11px_14px] border-r border-[rgba(56,189,248,0.08)] overflow-hidden flex flex-col">
        <div className="text-[10px] font-bold tracking-[0.8px] text-[var(--muted)] mb-2 uppercase flex-shrink-0">
          📊 Grafik Perbandingan
        </div>
        <div className="flex-1 min-h-0 relative">
          <Bar data={barChartData} options={chartOptions} ref={barChartRef as any} />
        </div>
      </div>

      {/* SECTION 3: Summary Cards */}
      <div className="flex-[1.2] p-[11px_14px] overflow-hidden flex flex-col">
        <div className="text-[10px] font-bold tracking-[0.8px] text-[var(--muted)] mb-2 uppercase flex-shrink-0">
          🎯 Ringkasan Zona
        </div>

        <div className="flex-1 min-h-0 relative">
          <Doughnut data={donutData} options={donutOptions} ref={donutChartRef as any} />
        </div>

        <div className="grid grid-cols-2 gap-0.5 mt-0.5 flex-2">
          <SummaryCard value="19.242" label="ZONA SANGAT TINGGI" color="#f87171" />
          <SummaryCard value="21.441" label="ZONA TINGGI" color="#fb923c" />
          <SummaryCard value="11.340" label="ZONA SEDANG" color="#fac21e" />
          <SummaryCard value="5.222" label="ZONA RENDAH" color="#ffe650" />
        </div>

        <div className="flex-[1.2] bg-[rgba(56,189,248,0.07)] border border-[rgba(56,189,248,0.18)] rounded px-2 py-1 mt-1 flex justify-between items-center">
          <div>
            <div className="text-[9px] text-[var(--muted)]">TOTAL TERDAMPAK</div>
            <div className="text-[15px] font-bold text-[var(--accent)]">47.832</div>
          </div>
          <div className="text-right">
            <div className="text-[9px] text-[var(--muted)]">DARI TOTAL</div>
            <div className="text-[11px] font-bold text-[#fb923c]">53.5%</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ZoneBadge({ zone }: { zone: "ST" | "T" | "S" | "R" }) {
  const styles = {
    ST: "bg-[rgba(248,113,113,0.18)] text-[#f87171]",
    T: "bg-[rgba(251,146,60,0.15)] text-[#fb923c]",
    S: "bg-[rgba(250,204,21,0.12)] text-[#fac21e]",
    R: "bg-[rgba(255,230,80,0.15)] text-[#ffe650]",
  };

  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wider ${styles[zone]}`}
    >
      {zone}
    </span>
  );
}

function SummaryCard({ value, label, color }: { value: string; label: string; color: string }) {
  return (
    <div className="bg-[rgba(0,18,45,0.6)] rounded px-1.5 py-1 border border-[rgba(56,189,248,0.08)] flex flex-col justify-center">
      <div className="text-[14px] font-bold" style={{ color, fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      <div className="text-[7px] font-bold text-[var(--muted)] mt-0.5 tracking-wider">
        {label}
      </div>
    </div>
  );
}
