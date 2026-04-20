import { create } from "zustand";
import type { ServerStatus } from "@/types";

/**
 * Server State Management
 */
interface ServerState {
  // Connection
  url: string;
  isOnline: boolean;
  isConnecting: boolean;
  lastChecked: Date | null;

  // Server status
  status: ServerStatus | null;

  // Status badges
  serverStatus: "online" | "offline" | "warning";
  precomputeStatus: "online" | "offline" | "warning";
  vectorStatus: "online" | "offline" | "warning";

  // Error
  connectionError: string | null;

  // Actions
  setUrl: (url: string) => void;
  setOnline: (online: boolean) => void;
  setConnecting: (connecting: boolean) => void;
  setStatus: (status: ServerStatus | null) => void;
  setConnectionError: (error: string | null) => void;
  setServerStatus: (status: "online" | "offline" | "warning") => void;
  setPrecomputeStatus: (status: "online" | "offline" | "warning") => void;
  setVectorStatus: (status: "online" | "offline" | "warning") => void;
  checkStatus: () => Promise<void>;
  reset: () => void;
}

const DEFAULT_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const useServerStore = create<ServerState>((set, get) => ({
  // Initial state
  url: DEFAULT_URL,
  isOnline: false,
  isConnecting: false,
  lastChecked: null,
  status: null,
  serverStatus: "offline",
  precomputeStatus: "offline",
  vectorStatus: "offline",
  connectionError: null,

  // Actions
  setUrl: (url) => set({ url }),
  setOnline: (isOnline) => set({ isOnline }),
  setConnecting: (isConnecting) => set({ isConnecting }),
  setStatus: (status) => set({ status }),
  setConnectionError: (error) => set({ connectionError: error }),
  setServerStatus: (serverStatus) => set({ serverStatus }),
  setPrecomputeStatus: (precomputeStatus) => set({ precomputeStatus }),
  setVectorStatus: (vectorStatus) => set({ vectorStatus }),

  checkStatus: async () => {
    const { url } = get();
    set({ isConnecting: true, connectionError: null });

    try {
      const response = await fetch(`${url}/health`);
      if (response.ok) {
        set({
          isOnline: true,
          lastChecked: new Date(),
          serverStatus: "online",
          precomputeStatus: "online",
          vectorStatus: "online",
        });

        // Get detailed status
        try {
          const statusResponse = await fetch(`${url}/status`);
          if (statusResponse.ok) {
            const status = await statusResponse.json();
            set({ status });
          }
        } catch {
          // Status endpoint might fail, but we're online
        }
      } else {
        set({
          isOnline: false,
          connectionError: "Server returned error",
          serverStatus: "offline",
        });
      }
    } catch (error) {
      set({
        isOnline: false,
        connectionError: error instanceof Error ? error.message : "Connection failed",
        serverStatus: "offline",
        precomputeStatus: "offline",
        vectorStatus: "offline",
      });
    } finally {
      set({ isConnecting: false });
    }
  },

  reset: () =>
    set({
      url: DEFAULT_URL,
      isOnline: false,
      isConnecting: false,
      lastChecked: null,
      status: null,
      serverStatus: "offline",
      precomputeStatus: "offline",
      vectorStatus: "offline",
      connectionError: null,
    }),
}));
