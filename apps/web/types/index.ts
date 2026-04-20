/**
 * Coordinate type
 */
export interface Coordinate {
  lat: number;
  lng: number;
}

/**
 * Bounding box type
 */
export interface BoundingBox {
  minLng: number;
  minLat: number;
  maxLng: number;
  maxLat: number;
}

/**
 * Data source types
 */
export type DataSource = "batnas" | "gebco" | "blend" | "dem" | "synthetic";

/**
 * Depth query response
 */
export interface DepthResponse {
  depth: number | null;
  source: DataSource;
  error?: string;
}

/**
 * Grid depth query response
 */
export interface GridDepthResponse {
  depths: number[][];
  cols: number;
  rows: number;
  bbox: BoundingBox;
}

/**
 * Path depth profile point
 */
export interface PathDepthPoint {
  lat: number;
  lng: number;
  depth: number | null;
  source: DataSource;
  distance: number; // distance from start in km
}

/**
 * Vector layer types
 */
export type LayerGeometryType = "Point" | "LineString" | "Polygon" | "MultiPoint" | "MultiLineString" | "MultiPolygon";

/**
 * Vector layer metadata
 */
export interface VectorLayer {
  id: string;
  name: string;
  type: LayerGeometryType;
  style: LayerStyle;
  features?: GeoJSON.FeatureCollection;
}

/**
 * Layer style configuration
 */
export interface LayerStyle {
  color: string;
  fillColor?: string;
  weight: number;
  opacity: number;
  fillOpacity: number;
  dashArray?: string;
}

/**
 * Fault information
 */
export interface Fault {
  id: string;
  name: string;
  type: string; // PUSGEN type string
  coordinates: Coordinate[];
  bbox: BoundingBox;
  strike?: number;
  dip?: number;
  rake?: number;
}

/**
 * Megathrust zone
 */
export interface MegathrustZone {
  id: string;
  name: string;
  bounds: BoundingBox;
  defaultMagnitude: number;
}

/**
 * Simulation parameters
 */
export interface SimulationParameters {
  faultId: string | null;
  megathrustId: string | null;
  magnitude: number;
  depth: number; // in km
  dip: number; // in degrees
  rake: number; // in degrees
  strike: number; // in degrees
  length: number; // in km
  width: number; // in km
  slip: number; // in meters
}

/**
 * Simulation results
 */
export interface SimulationResults {
  id: string;
  timestamp: string;
  parameters: SimulationParameters;
  maxWaveHeight: number;
  arrivalTimes: number[]; // in minutes
  waveHeights: number[]; // in meters
  timeSeries: {
    time: number; // in minutes
    height: number; // in meters
    location: Coordinate;
  }[];
  evaquationData?: {
    totalAgents: number;
    evacuatedAgents: number;
    avgEvacuationTime: number;
    routes?: EvacuationRoute[];
  };
}

/**
 * Evacuation route
 */
export interface EvacuationRoute {
  agentId: number;
  path: Coordinate[];
  evacuationTime: number; // in minutes
  success: boolean;
}

/**
 * Server status
 */
export interface ServerStatus {
  server: string;
  version: string;
  masking: {
    layer1_threshold: string;
    layer2_sanity: string;
    layer3_coastline: boolean;
    coastline_bbox?: [number, number, number, number];
  };
  precompute_status: string;
  batnas: {
    tiles_loaded: number;
    coverage: [number, number, number, number];
    reader: string;
    stats: {
      valid_hits: number;
      masked_land: number;
      masked_value: number;
    };
  };
  gebco: {
    enabled: boolean;
    cache_entries: number;
    mode: string;
  };
  dem: {
    tiles_loaded: number;
    coverage: [number, number, number, number];
  };
  vector: {
    dir: string;
    active: boolean;
  };
}

/**
 * Tile information
 */
export interface TileInfo {
  tiles_loaded: number;
  coverage: BoundingBox;
  reader?: string;
}

/**
 * Simulation request
 */
export interface SimulateRequest {
  fault: {
    id: string;
    type: string;
    coordinates: Coordinate[][];
  };
  parameters: {
    magnitude: number;
    depth: number;
    dip: number;
    rake: number;
    strike: number;
  };
  gridResolution?: number;
}

/**
 * Health check response
 */
export interface HealthResponse {
  status: "ok" | "error";
  timestamp: string;
}
