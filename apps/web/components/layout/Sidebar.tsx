"use client";

import { useUIStore } from "@/stores";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { SimulationPanel } from "@/components/simulation/SimulationPanel";
import { LayersPanel } from "@/components/map/LayersPanel";
import { ServerPanel } from "@/components/server/ServerPanel";

export function Sidebar() {
  const { sidebarOpen, activeTab, setActiveTab } = useUIStore();

  if (!sidebarOpen) return null;

  return (
    <aside className="w-[315px] flex-shrink-0 bg-[#0a1628] border-r border-[rgba(56,189,248,0.14)] overflow-y-auto flex flex-col">
      <Tabs defaultValue="simulation" value={activeTab} onValueChange={(val) => setActiveTab(val as any)} className="flex-1">
        <div className="p-3.5 border-b border-[rgba(56,189,248,0.08)]">
          <TabsList className="w-full">
            <TabsTrigger value="simulation">Simulasi</TabsTrigger>
            <TabsTrigger value="layers">Layers</TabsTrigger>
            <TabsTrigger value="server">Server</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="simulation" className="m-0 p-0">
          <SimulationPanel />
        </TabsContent>

        <TabsContent value="layers" className="m-0 p-0">
          <LayersPanel />
        </TabsContent>

        <TabsContent value="server" className="m-0 p-0">
          <ServerPanel />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
