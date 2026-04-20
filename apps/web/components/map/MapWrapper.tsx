"use client";

import { useEffect } from "react";
import dynamic from "next/dynamic";
import { useServerStore } from "@/stores";

// Dynamic import untuk LeafletMap (client-side only)
const LeafletMap = dynamic(
  () => import("./LeafletMap").then((mod) => ({ default: mod.LeafletMap })),
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-full flex items-center justify-center bg-[#060d1b]">
        <div className="text-muted text-sm">Loading map...</div>
      </div>
    ),
  }
);

export function MapWrapper() {
  const { checkStatus } = useServerStore();

  // Check server status on mount
  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, [checkStatus]);

  return <LeafletMap />;
}
