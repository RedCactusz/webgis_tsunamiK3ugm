/**
 * API Client for WebGIS Tsunami
 * Handles all communication with Python FastAPI backend
 */

import type {
  DepthResponse,
  GridDepthResponse,
  PathDepthPoint,
  VectorLayer,
  ServerStatus,
  TileInfo,
  SimulateRequest,
  SimulationResults,
  HealthResponse,
} from "@/types";

class ApiError extends Error {
  constructor(
    public statusCode: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * API Client configuration
 */
const DEFAULT_CONFIG = {
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  timeout: 30000, // 30 seconds
};

/**
 * Fetch wrapper with error handling
 */
async function fetcher<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${DEFAULT_CONFIG.baseURL}${endpoint}`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_CONFIG.timeout);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: response.statusText }));
      throw new ApiError(response.status, error.message || "API request failed");
    }

    return response.json();
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Request timeout");
    }
    throw error;
  }
}

/**
 * API Client methods
 */
export { ApiError };
export { api as default };

/**
 * API Client methods
 */
export const api = {
  /**
   * Health check
   */
  health: () => fetcher<HealthResponse>("/health"),

  /**
   * Get server status
   */
  getStatus: () => fetcher<ServerStatus>("/status"),

  /**
   * Get BATNAS tile info
   */
  getTilesInfo: () => fetcher<TileInfo>("/tiles/info"),

  /**
   * Get DEM tile info
   */
  getDemInfo: () => fetcher<TileInfo>("/dem/info"),

  /**
   * Query depth at single point
   */
  getDepth: (lat: number, lon: number) =>
    fetcher<DepthResponse>(`/depth?lat=${lat}&lon=${lon}`),

  /**
   * Query depth grid
   */
  getDepthGrid: (params: {
    lon_min: number;
    lat_min: number;
    lon_max: number;
    lat_max: number;
    cols: number;
    rows: number;
  }) => {
    const searchParams = new URLSearchParams(params as unknown as Record<string, string>);
    return fetcher<GridDepthResponse>(`/depth/grid?${searchParams}`);
  },

  /**
   * Query depth along path
   */
  getDepthPath: (params: {
    from_lat: number;
    from_lon: number;
    to_lat: number;
    to_lon: number;
    steps: number;
  }) => {
    const searchParams = new URLSearchParams(params as unknown as Record<string, string>);
    return fetcher<PathDepthPoint[]>(`/depth/path?${searchParams}`);
  },

  /**
   * List all vector layers
   */
  getLayers: () => fetcher<VectorLayer[]>("/layers"),

  /**
   * Get specific layer GeoJSON
   */
  getLayer: (layerId: string) =>
    fetcher<GeoJSON.FeatureCollection>(`/layers/${layerId}`),

  /**
   * Get elevation from DEM
   */
  getElevation: (lat: number, lon: number) =>
    fetcher<{ elevation: number | null }>(`/dem/elevation?lat=${lat}&lon=${lon}`),

  /**
   * Run tsunami simulation
   */
  simulate: (request: SimulateRequest) =>
    fetcher<SimulationResults>("/simulate", {
      method: "POST",
      body: JSON.stringify(request),
    }),
};
