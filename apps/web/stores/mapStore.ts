import { create } from "zustand";
import type { Coordinate, BoundingBox } from "@/types";

/**
 * Map State Management with Zustand
 */
interface MapState {
  // Map view state
  center: Coordinate;
  zoom: number;
  bounds: BoundingBox | null;

  // Layer state
  activeLayers: string[];
  visibleLayers: Set<string>;
  selectedLayer: string | null;

  // Interaction state
  hoveredCoordinate: Coordinate | null;
  clickedCoordinate: Coordinate | null;
  isDrawing: boolean;

  // Actions
  setCenter: (center: Coordinate) => void;
  setZoom: (zoom: number) => void;
  setBounds: (bounds: BoundingBox) => void;
  toggleLayer: (layerId: string) => void;
  showLayer: (layerId: string) => void;
  hideLayer: (layerId: string) => void;
  setSelectedLayer: (layerId: string | null) => void;
  setHoveredCoordinate: (coord: Coordinate | null) => void;
  setClickedCoordinate: (coord: Coordinate | null) => void;
  setIsDrawing: (isDrawing: boolean) => void;
  reset: () => void;
}

const defaultCenter: Coordinate = { lat: -8.022, lng: 110.298 }; // Parangtritis, Bantul

export const useMapStore = create<MapState>((set) => ({
  // Initial state
  center: defaultCenter,
  zoom: 10,
  bounds: null,
  activeLayers: [],
  visibleLayers: new Set<string>(),
  selectedLayer: null,
  hoveredCoordinate: null,
  clickedCoordinate: null,
  isDrawing: false,

  // Actions
  setCenter: (center) => set({ center }),
  setZoom: (zoom) => set({ zoom }),
  setBounds: (bounds) => set({ bounds }),

  toggleLayer: (layerId) =>
    set((state) => {
      const newVisible = new Set(state.visibleLayers);
      if (newVisible.has(layerId)) {
        newVisible.delete(layerId);
      } else {
        newVisible.add(layerId);
      }
      return { visibleLayers: newVisible };
    }),

  showLayer: (layerId) =>
    set((state) => {
      const newVisible = new Set(state.visibleLayers);
      newVisible.add(layerId);
      return { visibleLayers: newVisible };
    }),

  hideLayer: (layerId) =>
    set((state) => {
      const newVisible = new Set(state.visibleLayers);
      newVisible.delete(layerId);
      return { visibleLayers: newVisible };
    }),

  setSelectedLayer: (layerId) => set({ selectedLayer: layerId }),
  setHoveredCoordinate: (coord) => set({ hoveredCoordinate: coord }),
  setClickedCoordinate: (coord) => set({ clickedCoordinate: coord }),
  setIsDrawing: (isDrawing) => set({ isDrawing }),

  reset: () =>
    set({
      center: defaultCenter,
      zoom: 10,
      bounds: null,
      activeLayers: [],
      visibleLayers: new Set<string>(),
      selectedLayer: null,
      hoveredCoordinate: null,
      clickedCoordinate: null,
      isDrawing: false,
    }),
}));
