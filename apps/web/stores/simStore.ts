import { create } from "zustand";
import type {
  Fault,
  SimulationParameters,
  SimulationResults,
  MegathrustZone,
} from "@/types";

/**
 * Simulation State Management
 */
interface SimState {
  // Fault selection
  selectedFault: Fault | null;
  selectedMegathrust: MegathrustZone | null;

  // Parameters
  parameters: SimulationParameters;

  // Simulation state
  isRunning: boolean;
  isComplete: boolean;
  error: string | null;
  progress: number; // 0-100

  // Results
  results: SimulationResults | null;

  // Presets
  presets: string[];

  // Actions
  setSelectedFault: (fault: Fault | null) => void;
  setSelectedMegathrust: (zone: MegathrustZone | null) => void;
  setParameter: <K extends keyof SimulationParameters>(
    key: K,
    value: SimulationParameters[K]
  ) => void;
  setParameters: (params: Partial<SimulationParameters>) => void;
  setIsRunning: (isRunning: boolean) => void;
  setIsComplete: (isComplete: boolean) => void;
  setError: (error: string | null) => void;
  setProgress: (progress: number) => void;
  setResults: (results: SimulationResults | null) => void;
  resetResults: () => void;
  reset: () => void;
}

const defaultParameters: SimulationParameters = {
  faultId: null,
  megathrustId: null,
  magnitude: 7.5,
  depth: 15,
  dip: 45,
  rake: 90,
  strike: 270,
  length: 100,
  width: 50,
  slip: 5,
};

export const useSimStore = create<SimState>((set) => ({
  // Initial state
  selectedFault: null,
  selectedMegathrust: null,
  parameters: defaultParameters,
  isRunning: false,
  isComplete: false,
  error: null,
  progress: 0,
  results: null,
  presets: ["2006 Pangandaran", "2010 Mentawai", "2011 Tohoku"],

  // Actions
  setSelectedFault: (fault) =>
    set((state) => ({
      selectedFault: fault,
      selectedMegathrust: null,
      parameters: {
        ...state.parameters,
        faultId: fault?.id || null,
        megathrustId: null,
      },
    })),

  setSelectedMegathrust: (zone) =>
    set((state) => ({
      selectedMegathrust: zone,
      selectedFault: null,
      parameters: {
        ...state.parameters,
        megathrustId: zone?.id || null,
        faultId: null,
      },
    })),

  setParameter: (key, value) =>
    set((state) => ({
      parameters: {
        ...state.parameters,
        [key]: value,
      },
    })),

  setParameters: (params) =>
    set((state) => ({
      parameters: {
        ...state.parameters,
        ...params,
      },
    })),

  setIsRunning: (isRunning) => set({ isRunning }),
  setIsComplete: (isComplete) => set({ isComplete }),
  setError: (error) => set({ error }),
  setProgress: (progress) => set({ progress }),
  setResults: (results) => set({ results, isComplete: true, isRunning: false }),

  resetResults: () =>
    set({
      results: null,
      isComplete: false,
      error: null,
      progress: 0,
    }),

  reset: () =>
    set({
      selectedFault: null,
      selectedMegathrust: null,
      parameters: defaultParameters,
      isRunning: false,
      isComplete: false,
      error: null,
      progress: 0,
      results: null,
    }),
}));
