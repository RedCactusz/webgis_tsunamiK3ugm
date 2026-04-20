"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useMapStore } from "@/stores";

// Fix for default marker icons in Leaflet with Next.js
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png",
  iconUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png",
  shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
});

export function LeafletMap() {
  const mapRef = useRef<L.Map | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const isInitializingRef = useRef(true);
  const { center, zoom, setClickedCoordinate } = useMapStore();

  // Initialize map (client-side only)
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    // Create map
    const map = L.map(mapContainerRef.current, {
      center: [center.lat, center.lng],
      zoom,
      zoomControl: true,
    });

    // Add tile layer (OpenStreetMap for now, will be replaced with custom tiles)
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(map);

    // Handle click events
    map.on("click", (e) => {
      const { lat, lng } = e.latlng;
      setClickedCoordinate({ lat, lng });
    });

    mapRef.current = map;
    isInitializingRef.current = false;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []); // Empty deps - only run once on mount

  // Update map center/zoom (but not during initialization)
  useEffect(() => {
    if (!mapRef.current || isInitializingRef.current) return;

    const currentCenter = mapRef.current.getCenter();
    const currentZoom = mapRef.current.getZoom();

    // Only update if significantly different
    if (
      Math.abs(currentCenter.lat - center.lat) > 0.0001 ||
      Math.abs(currentCenter.lng - center.lng) > 0.0001 ||
      Math.abs(currentZoom - zoom) > 0.5
    ) {
      mapRef.current.setView([center.lat, center.lng], zoom);
    }
  }, [center.lat, center.lng, zoom]);

  if (!mapContainerRef.current) {
    return (
      <div
        ref={mapContainerRef}
        className="w-full h-full"
        style={{ background: "#060d1b" }}
      />
    );
  }

  return (
    <div
      ref={mapContainerRef}
      className="w-full h-full"
      style={{ background: "#060d1b" }}
    />
  );
}
