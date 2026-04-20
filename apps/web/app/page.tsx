import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { MainContent } from "@/components/layout/MainContent";
import { MapWrapper } from "@/components/map/MapWrapper";
import { BottomBar } from "@/components/layout/BottomBar";
import { RightPanel } from "@/components/layout/RightPanel";

export default function Home() {
  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <MainContent>
          <div className="flex flex-col h-full">
            <div className="flex-1 flex overflow-hidden">
              <MapWrapper />
              <RightPanel />
            </div>
            <BottomBar />
          </div>
        </MainContent>
      </div>
    </div>
  );
}
