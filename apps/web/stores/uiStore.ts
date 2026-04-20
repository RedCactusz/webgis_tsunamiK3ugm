import { create } from "zustand";

/**
 * UI State Management
 */
interface UIState {
  // Sidebar
  sidebarOpen: boolean;
  sidebarWidth: number;

  // Active tab
  activeTab: "simulation" | "layers" | "server";

  // Modals
  settingsModalOpen: boolean;
  aboutModalOpen: boolean;

  // Notifications
  notifications: Notification[];

  // Loading states
  globalLoading: boolean;

  // Actions
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setSidebarWidth: (width: number) => void;
  setActiveTab: (tab: "simulation" | "layers" | "server") => void;
  setSettingsModalOpen: (open: boolean) => void;
  setAboutModalOpen: (open: boolean) => void;
  addNotification: (notification: Omit<Notification, "id">) => void;
  removeNotification: (id: string) => void;
  clearNotifications: () => void;
  setGlobalLoading: (loading: boolean) => void;
}

interface Notification {
  id: string;
  type: "info" | "success" | "warning" | "error";
  title: string;
  message: string;
  duration?: number; // auto-dismiss after ms
}

export const useUIStore = create<UIState>((set) => ({
  // Initial state
  sidebarOpen: true,
  sidebarWidth: 315,
  activeTab: "simulation",
  settingsModalOpen: false,
  aboutModalOpen: false,
  notifications: [],
  globalLoading: false,

  // Actions
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarWidth: (width) => set({ sidebarWidth: width }),
  setActiveTab: (tab) => set({ activeTab: tab }),
  setSettingsModalOpen: (open) => set({ settingsModalOpen: open }),
  setAboutModalOpen: (open) => set({ aboutModalOpen: open }),

  addNotification: (notification) =>
    set((state) => ({
      notifications: [
        ...state.notifications,
        { ...notification, id: crypto.randomUUID() },
      ],
    })),

  removeNotification: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    })),

  clearNotifications: () => set({ notifications: [] }),
  setGlobalLoading: (loading) => set({ globalLoading: loading }),
}));
