"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useServerStore } from "@/stores";

export function ServerPanel() {
  const { status, isOnline, isConnecting, checkStatus } = useServerStore();

  return (
    <div className="p-3.5 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Status Server</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Connection Status */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">Status</span>
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  isOnline
                    ? "bg-[#34d399] shadow-[0_0_6px_#34d399] animate-blink"
                    : "bg-[#2a3a4a]"
                }`}
              />
              <span className={`text-xs font-semibold ${isOnline ? "text-[#34d399]" : "text-muted"}`}>
                {isOnline ? "Online" : "Offline"}
              </span>
            </div>
          </div>

          {/* Server Info */}
          {status && (
            <div className="space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-muted">Server</span>
                <span className="font-mono text-text2">{status.server}</span>
              </div>
              {status.batnas && (
                <div className="flex justify-between">
                  <span className="text-muted">BATNAS Tiles</span>
                  <span className="font-mono text-accent">{status.batnas.tiles_loaded}</span>
                </div>
              )}
              {status.gebco && (
                <div className="flex justify-between">
                  <span className="text-muted">GEBCO</span>
                  <span className={`font-semibold ${status.gebco.enabled ? "text-[#38bdf8]" : "text-muted"}`}>
                    {status.gebco.enabled ? "Active" : "Inactive"}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Refresh Button */}
          <button
            onClick={checkStatus}
            disabled={isConnecting}
            className="w-full py-2 px-3 rounded text-xs font-semibold bg-[rgba(56,189,248,0.12)] border border-[rgba(56,189,248,0.3)] text-accent hover:bg-[rgba(56,189,248,0.25)] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isConnecting ? "Checking..." : "Refresh Status"}
          </button>
        </CardContent>
      </Card>
    </div>
  );
}
