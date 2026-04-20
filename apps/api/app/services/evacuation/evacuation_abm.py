"""
evacuation_abm.py — Modul Analisis Rute Evakuasi + ABM Tsunami
===============================================================
Modul ini memisahkan logika rute evakuasi dan Agent-Based Model (ABM)
dari server utama, mengikuti pola swe_solver.py.

Komponen utama:
  - haversine_m()        : hitung jarak antar koordinat (meter)
  - build_graph()        : bangun graph jalan berbobot DEM + slope
  - nearest_node()       : cari node graph terdekat ke koordinat
  - dijkstra()           : shortest path (Dijkstra)
  - astar()              : shortest path (A* heuristik haversine)
  - _build_road_cache()  : load SHP jalan -> GeoJSON + road dicts
  - _build_desa_cache()  : load SHP administrasi desa -> list desa
  - _build_tes_cache()   : load SHP TES -> list titik evakuasi
  - EvacuationABMSolver  : entry point untuk server

Integrasi:
  - DEMManager dioper dari server (Opsi A) -> DEM tidak dibaca dua kali
  - SWE results (opsional) dipakai untuk blokir rute tergenang
  - Output ABM: posisi agen per timestep + statistik ringkasan

Cara pakai di server:
  from evacuation_abm import EvacuationABMSolver, build_graph

  solver = EvacuationABMSolver(
      vektor_dir = VEKTOR_DIR,
      dem_mgr    = dem_manager,   # dari server (DEMManager instance)
  )
  solver.build_caches()

  # Oper SWE results untuk integrasi (opsional)
  solver.set_swe_results(swe_output)

  # Hitung rute
  result = solver.compute_route(origin, destination, method, transport, weight, roads)

  # Jalankan ABM
  result = solver.run_abm(body_dict)
"""

import os, math, heapq, random, json
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

# ── Optional: geopandas untuk konversi SHP ────────────────────
try:
    import geopandas as gpd
    USE_GPD = True
except ImportError:
    USE_GPD = False
    print("  geopandas tidak ada - konversi SHP tidak tersedia")

try:
    from shapely.geometry import shape as _sh
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


# ═══════════════════════════════════════════════════════════════
# HELPER GEOMETRI
# ═══════════════════════════════════════════════════════════════

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Hitung jarak haversine antara dua titik koordinat (meter)."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ═══════════════════════════════════════════════════════════════
# SHAPEFILE -> GEOJSON CONVERTER
# ═══════════════════════════════════════════════════════════════

def shp_to_geojson(shp_path: str, simplify: bool = True, max_pts: int = 400) -> Optional[dict]:
    """
    Konversi shapefile ke GeoJSON menggunakan geopandas.
    CRS otomatis ditransformasi ke WGS84 (EPSG:4326).
    """
    if not USE_GPD:
        print(f"  [FAIL] geopandas tidak tersedia - tidak bisa konversi {shp_path}")
        return None
    try:
        gdf = gpd.read_file(shp_path)
        if gdf is None or gdf.empty:
            return None
        if hasattr(gdf, "crs") and gdf.crs is not None:
            try:
                # Check EPSG safely
                if gdf.crs.to_epsg() != 4326:
                    print(f"  [CRS] Transformasi {shp_path} ke EPSG:4326...")
                    gdf = gdf.to_crs("EPSG:4326")
            except:
                pass
        
        bbox = [round(x, 5) for x in list(gdf.total_bounds)]
        return {
            "features": json.loads(gdf.to_json())["features"],
            "bbox": bbox,
        }
    except Exception as e:
        print(f"  [FAIL] Gagal membaca {shp_path}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# CACHE BUILDER: JALAN, DESA, TES
# ═══════════════════════════════════════════════════════════════

SPEED_MAP = {
    "primary": 60, "secondary": 50, "tertiary": 40,
    "residential": 30, "unclassified": 25, "service": 20,
    "track": 15, "path": 8, "footway": 5,
}

ROAD_KEYWORDS = [
    "jalan", "road", "street", "way", "jaringan",
    "transport", "line", "ruas", "jalur", "ln_",
]

DESA_KEYWORDS = [
    "administrasi_desa", "desa", "kelurahan", "kel_", "village",
]

TES_KEYWORDS = [
    "tes_", "tes_bantul", "koordinat_tes", "evakuasi", "shelter",
    "titik_kumpul", "assembly",
]


def _build_road_cache(vektor_dir: str) -> Optional[dict]:
    """
    Scan vektor_dir untuk shapefile jalan, konversi ke GeoJSON,
    parse ke list road dicts siap pakai untuk build_graph().
    Dipanggil sekali saat startup.

    Return:
      {geojson, roads, source_file, feature_count, bbox}
      atau None jika tidak ditemukan.
    """
    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            fn_lower = fn.lower()
            if not fn_lower.endswith(".shp"):
                continue
            if not any(k in fn_lower for k in ROAD_KEYWORDS):
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n[INFO] Konversi shapefile jalan: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=False, max_pts=50000)
                if not gj or not gj.get("features"):
                    print(f"  [WARN] {fn}: kosong, skip")
                    continue

                # Filter hanya LineString / MultiLineString
                line_feats = [
                    f for f in gj["features"]
                    if f.get("geometry", {}).get("type", "") in ("LineString", "MultiLineString")
                ]
                if not line_feats:
                    print(f"  [WARN] {fn}: tidak ada geometri LineString, skip")
                    continue
                gj["features"] = line_feats

                # Parse ke road dicts
                roads = []
                for feat in line_feats:
                    props = feat.get("properties", {}) or {}
                    geom  = feat.get("geometry", {})
                    if not geom:
                        continue

                    hw = (props.get("highway") or props.get("HIGHWAY") or
                          props.get("jenis")   or props.get("JENIS")   or
                          props.get("REMARK")  or props.get("fclass")  or
                          props.get("type")    or "residential")
                    hw = str(hw).lower().strip()

                    name   = (props.get("name") or props.get("NAMA") or
                              props.get("nama")  or props.get("NAME") or "")
                    oneway = str(props.get("oneway", "no")).lower() in ("yes", "1", "true")

                    coords = []
                    if geom["type"] == "LineString":
                        coords = [[c[1], c[0]] for c in geom["coordinates"]]
                    elif geom["type"] == "MultiLineString":
                        for seg in geom["coordinates"]:
                            coords += [[c[1], c[0]] for c in seg]

                    if len(coords) < 2:
                        continue

                    roads.append({
                        "id":        props.get("osm_id") or props.get("ID") or id(feat),
                        "highway":   hw,
                        "name":      name,
                        "oneway":    "yes" if oneway else "no",
                        "speed_kmh": SPEED_MAP.get(hw, 25),
                        "capacity":  int(props.get("lanes", 1) or 1) * 1000,
                        "coords":    coords,
                    })

                if not roads:
                    print(f"  [WARN] {fn}: tidak ada road valid setelah parsing")
                    continue

                all_lons = [c[1] for r in roads for c in r["coords"]]
                all_lats = [c[0] for r in roads for c in r["coords"]]
                bbox = [
                    round(min(all_lons), 5), round(min(all_lats), 5),
                    round(max(all_lons), 5), round(max(all_lats), 5),
                ]

                print(f"  [OK] {fn}: {len(line_feats)} fitur -> {len(roads)} road dicts, bbox={bbox}")

                return {
                    "geojson":       gj,
                    "roads":         roads,
                    "source_file":   fn,
                    "feature_count": len(line_feats),
                    "bbox":          bbox,
                }

            except Exception as e:
                print(f"  [FAIL] Error konversi {fn}: {e}")

    print("  Tidak ada shapefile jalan ditemukan")
    return None

def get_valid_land_point(poly, dem_mgr=None) -> Tuple[float, float]:
    """
    Finds a point inside the polygon that is on land (elevation > 0).
    Same logic as in server.py for consistency.
    """
    try:
        # 1. Try Centroid
        c = poly.centroid
        if dem_mgr:
            elev, _ = dem_mgr.query(c.x, c.y)
            if elev is not None and elev > 0:
                return c.x, c.y
        else:
            return c.x, c.y

        # 2. Try Representative Point
        rp = poly.representative_point()
        if dem_mgr:
            elev, _ = dem_mgr.query(rp.x, rp.y)
            if elev is not None and elev > 0:
                return rp.x, rp.y
        else:
            return rp.x, rp.y

        return rp.x, rp.y
    except Exception:
        try: return poly.centroid.x, poly.centroid.y
        except: return 0.0, 0.0


def _build_desa_cache(vektor_dir: str) -> Optional[dict]:
    """
    Load shapefile administrasi desa.
    Ekstrak: nama desa, jumlah penduduk, centroid koordinat.

    Return:
      {geojson, desa: [{name, penduduk, lat, lon}], source_file, count}
      atau None.
    """
    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            fn_lower = fn.lower()
            if not fn_lower.endswith(".shp"):
                continue
            if not any(k in fn_lower for k in DESA_KEYWORDS):
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n[INFO] Konversi shapefile desa: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=True)
                if not gj or not gj.get("features"):
                    print(f"  [WARN] {fn}: kosong, skip")
                    continue

                desa_list = []
                for feat in gj["features"]:
                    props = feat.get("properties", {}) or {}
                    geom  = feat.get("geometry", {})

                    # Nama desa — coba berbagai field
                    name = ""
                    for fld in ["NAMOBJ", "WADMKD", "namobj", "NAMA_OBJ", "nama_obj", "DESA", "desa", "NAMA_DESA", "nama_desa",
                                "KELURAHAN", "kelurahan", "NAMA", "nama",
                                "NAME", "name", "VILLAGE"]:
                        v = props.get(fld)
                        if v and str(v).strip() not in ("", "None"):
                            name = str(v).strip()
                            break
                    if not name:
                        name = f"Desa-{len(desa_list)+1:03d}"

                    # Populasi — coba berbagai field
                    penduduk = 1000
                    for fld in ["Penduduk", "PENDUDUK", "Jumlah_Pen", "JUMLAH_PEN", "Population", "POPULATION",
                                "JIWA", "jiwa"]:
                        try:
                            v = props.get(fld)
                            if v:
                                penduduk = int(float(str(v)))
                                break
                        except Exception:
                            pass

                    # Centroid koordinat
                    lat_c = lon_c = None
                    try:
                        gt = geom.get("type", "")
                        if gt == "Polygon":
                            cs = geom["coordinates"][0]
                        elif gt == "MultiPolygon":
                            # Ambil ring terpanjang dari MultiPolygon
                            cs = max(
                                (p[0] for p in geom["coordinates"]),
                                key=len
                            )
                        else:
                            cs = geom["coordinates"][0]
                        lon_c = sum(c[0] for c in cs) / len(cs)
                        lat_c = sum(c[1] for c in cs) / len(cs)
                    except Exception:
                        pass

                    if lat_c is None or lon_c is None:
                        continue

                    desa_list.append({
                        "name":     name,
                        "penduduk": penduduk,
                        "lat":      round(lat_c, 6),
                        "lon":      round(lon_c, 6),
                        "geom":     geom,  # Include full geometry for polygon rendering
                        "props":    {k: v for k, v in list(props.items())[:8]},
                    })

                if not desa_list:
                    print(f"  [WARN] {fn}: tidak ada desa valid")
                    continue

                print(f"  [OK] {fn}: {len(desa_list)} desa di-cache")
                return {
                    "geojson":     gj,
                    "desa":        desa_list,
                    "source_file": fn,
                    "count":       len(desa_list),
                }

            except Exception as e:
                print(f"  [FAIL] Error konversi desa {fn}: {e}")

    print("  [WARN] Tidak ada shapefile administrasi desa ditemukan")
    return None


def _build_tes_cache(vektor_dir: str) -> Optional[dict]:
    """
    Load shapefile Titik Evakuasi Sementara (TES).
    Ekstrak: nama TES, kapasitas, koordinat.

    Return:
      {geojson, tes: [{name, kapasitas, lat, lon}], source_file, count}
      atau None.
    """
    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            fn_lower = fn.lower()
            if not fn_lower.endswith(".shp"):
                continue
            if not any(k in fn_lower for k in TES_KEYWORDS):
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n[INFO] Konversi shapefile TES: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=False)
                if not gj or not gj.get("features"):
                    print(f"  [WARN] {fn}: kosong, skip")
                    continue

                tes_list = []
                for feat in gj["features"]:
                    props = feat.get("properties", {}) or {}
                    geom  = feat.get("geometry", {})

                    # Nama TES
                    name = ""
                    for fld in ["NAMA", "nama", "NAME", "name", "TES", "tes",
                                "LOKASI", "lokasi", "TEMPAT", "tempat"]:
                        v = props.get(fld)
                        if v and str(v).strip() not in ("", "None"):
                            name = str(v).strip()
                            break
                    if not name:
                        name = f"TES-{len(tes_list)+1:02d}"

                    # Kapasitas
                    kapasitas = 500
                    for fld in ["KAPASITAS", "kapasitas", "CAP", "cap", "CAPACITY"]:
                        try:
                            v = props.get(fld)
                            if v:
                                kapasitas = int(float(str(v)))
                                break
                        except Exception:
                            pass

                    # Koordinat
                    lat_c = lon_c = None
                    try:
                        gt = geom.get("type", "")
                        if gt == "Point":
                            lon_c, lat_c = geom["coordinates"]
                        elif gt == "Polygon":
                            cs = geom["coordinates"][0]
                            lon_c = sum(c[0] for c in cs) / len(cs)
                            lat_c = sum(c[1] for c in cs) / len(cs)
                        elif gt == "MultiPoint":
                            lon_c, lat_c = geom["coordinates"][0]
                    except Exception:
                        pass

                    tes_list.append({
                        "name":      name,
                        "kapasitas": kapasitas,
                        "lat":       round(lat_c, 6) if lat_c else None,
                        "lon":       round(lon_c, 6) if lon_c else None,
                        "props":     {k: v for k, v in list(props.items())[:10]},
                    })

                if not tes_list:
                    print(f"  [WARN] {fn}: tidak ada TES valid")
                    continue

                print(f"  [OK] {fn}: {len(tes_list)} TES di-cache")
                return {
                    "geojson":     gj,
                    "tes":         tes_list,
                    "source_file": fn,
                    "count":       len(tes_list),
                }

            except Exception as e:
                print(f"  [FAIL] Error konversi TES {fn}: {e}")

    print("  [WARN] Tidak ada shapefile TES ditemukan")
    return None


# ═══════════════════════════════════════════════════════════════
# GRAPH BUILDER
# ═══════════════════════════════════════════════════════════════

def build_graph(roads: list, dem_mgr=None) -> dict:
    """
    Bangun adjacency graph dari daftar jalan.
    Jika dem_mgr tersedia, setiap edge mendapat bobot slope & elevation penalty.

    Node : idx dalam nodes_list -> (lat, lon, elev_m)
    Edge : (neighbor_idx, dist_m, time_min, hw, capacity, composite_cost, slope_pct, src_elev)

    composite_cost = w_dist*dist_norm + w_time*time_norm + w_elev*elev_pen + w_slope*slope_pen
      - elev_pen  : node asal rendah (dekat laut) -> penalty besar -> Dijkstra pilih rute naik
      - slope_pen : makin curam -> lebih lambat

    Bobot (dapat disesuaikan):
      W_DIST=0.30, W_TIME=0.30, W_ELEV=0.25, W_SLOPE=0.15
    """
    W_DIST  = 0.30
    W_TIME  = 0.30
    W_ELEV  = 0.25
    W_SLOPE = 0.15

    ELEV_DANGER_MAX = 20.0   # elevasi ≥ 20 m -> tidak ada penalty
    SLOPE_MAX_PCT   = 40.0   # slope ≥ 40% -> penalty maksimal

    nodes_list: List[Tuple] = []
    nodes_idx:  Dict        = {}

    def get_or_add(lat, lon):
        key = (round(lat, 5), round(lon, 5))
        if key not in nodes_idx:
            elev = 0.0
            if dem_mgr:
                e, _ = dem_mgr.query(lon, lat)
                if e is not None:
                    elev = float(e)
            nodes_idx[key] = len(nodes_list)
            nodes_list.append((lat, lon, elev))
        return nodes_idx[key]

    edges: Dict[int, list] = {}

    for road in roads:
        coords = road["coords"]
        speed  = road.get("speed_kmh", 20)
        hw     = road.get("highway", "residential")
        cap    = road.get("capacity", 1000)
        oneway = road.get("oneway", "no") in ("yes", "true", "1")

        prev_idx = None
        for lat, lon in coords:
            idx = get_or_add(lat, lon)
            if prev_idx is not None:
                plat, plon, pelev = nodes_list[prev_idx]
                clat, clon, celev = nodes_list[idx]
                dist  = haversine_m(plat, plon, clat, clon)
                t_min = (dist / 1000) / speed * 60 if speed > 0 else 999

                # Slope
                slope_pct = (abs(celev - pelev) / dist * 100.0) if dist > 0 else 0.0

                # Elevation penalty (node asal)
                elev_pen = min(1.0, max(0.0, 1.0 - pelev / ELEV_DANGER_MAX))

                # Slope penalty
                slope_pen = min(1.0, slope_pct / SLOPE_MAX_PCT)

                # Composite cost (normalized)
                dist_norm = (dist / 1000) / 10.0   # asumsi max 10 km
                time_norm = t_min / 60.0            # asumsi max 60 mnt
                composite = (W_DIST  * dist_norm +
                             W_TIME  * time_norm  +
                             W_ELEV  * elev_pen   +
                             W_SLOPE * slope_pen)

                edges.setdefault(prev_idx, []).append(
                    (idx, dist, t_min, hw, cap, composite, slope_pct, pelev)
                )
                if not oneway:
                    elev_pen2  = min(1.0, max(0.0, 1.0 - celev / ELEV_DANGER_MAX))
                    composite2 = (W_DIST  * dist_norm +
                                  W_TIME  * time_norm  +
                                  W_ELEV  * elev_pen2  +
                                  W_SLOPE * slope_pen)
                    edges.setdefault(idx, []).append(
                        (prev_idx, dist, t_min, hw, cap, composite2, slope_pct, celev)
                    )

            prev_idx = idx

    return {"nodes": nodes_list, "edges": edges}


# ═══════════════════════════════════════════════════════════════
# GRAPH UTILITIES: NEAREST NODE, DIJKSTRA, A*
# ═══════════════════════════════════════════════════════════════

def nearest_node(nodes_list: list, lat: float, lon: float) -> Tuple[int, float]:
    """Cari index node terdekat ke koordinat (lat, lon). Return (idx, dist_m)."""
    best_idx, best_d = 0, 1e18
    for i, node in enumerate(nodes_list):
        d = haversine_m(lat, lon, node[0], node[1])
        if d < best_d:
            best_d, best_idx = d, i
    return best_idx, best_d


def dijkstra(graph: dict, start_idx: int, end_idx: int,
             weight: str = "composite") -> Tuple[Optional[float], list]:
    """
    Dijkstra shortest path.
    weight: 'composite' | 'time' | 'distance'
    Return: (cost, [(lat,lon), ...]) atau (None, []) jika tidak ditemukan.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    dist  = {start_idx: 0}
    prev  = {}
    pq    = [(0, start_idx)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18):
            continue
        if u == end_idx:
            break
        for edge in edges.get(u, []):
            v      = edge[0]
            dist_m = edge[1]
            t_min  = edge[2]
            comp   = edge[5] if len(edge) > 5 else t_min / 60.0

            w = {"time": t_min, "distance": dist_m / 1000}.get(weight, comp)

            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if end_idx not in prev and end_idx != start_idx:
        return None, []

    path = []
    cur  = end_idx
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
    path.append(start_idx)
    path.reverse()
    return dist.get(end_idx, 1e18), [(nodes[i][0], nodes[i][1]) for i in path]


def astar(graph: dict, start_idx: int, end_idx: int,
          weight: str = "composite",
          transport_speed_kmh: float = 30) -> Tuple[Optional[float], list]:
    """
    A* shortest path dengan heuristik haversine.
    weight: 'composite' | 'time' | 'distance'
    Return: (cost, [(lat,lon), ...]) atau (None, []) jika tidak ditemukan.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    elat, elon = nodes[end_idx][0], nodes[end_idx][1]

    def heuristic(idx):
        d = haversine_m(nodes[idx][0], nodes[idx][1], elat, elon)
        if weight == "time":
            return (d / 1000) / transport_speed_kmh * 60
        elif weight == "distance":
            return d / 1000
        else:
            return (d / 1000) / transport_speed_kmh

    g    = {start_idx: 0}
    prev = {}
    pq   = [(heuristic(start_idx), 0, start_idx)]

    while pq:
        _, gn, u = heapq.heappop(pq)
        if u == end_idx:
            break
        if gn > g.get(u, 1e18):
            continue
        for edge in edges.get(u, []):
            v      = edge[0]
            dist_m = edge[1]
            t_min  = edge[2]
            comp   = edge[5] if len(edge) > 5 else t_min / 60.0

            w  = {"time": t_min, "distance": dist_m / 1000}.get(weight, comp)
            ng = gn + w
            if ng < g.get(v, 1e18):
                g[v]    = ng
                prev[v] = u
                heapq.heappush(pq, (ng + heuristic(v), ng, v))

    if end_idx not in prev and end_idx != start_idx:
        return None, []

    path = []
    cur  = end_idx
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
    path.append(start_idx)
    path.reverse()
    return g.get(end_idx, 1e18), [(nodes[i][0], nodes[i][1]) for i in path]


# ═══════════════════════════════════════════════════════════════
# EVACUATION ABM SOLVER — entry point untuk server
# ═══════════════════════════════════════════════════════════════

class EvacuationABMSolver:
    """
    Solver gabungan rute evakuasi + ABM.
    DEMManager dioper dari server (Opsi A) — tidak dibaca ulang.

    Alur:
      1. build_caches()         -> load jalan, desa, TES dari vektor_dir
      2. set_swe_results()      -> (opsional) integrasi hasil SWE
      3. compute_route()        -> hitung rute titik-ke-titik
      4. run_abm()              -> jalankan simulasi ABM evakuasi

    Integrasi SWE:
      - Zona tergenang (flood_zones) memblokir rute secara dinamis
      - Agen yang rutunya tergenang di-reroute otomatis
      - Agen yang tidak bisa reroute -> status "stranded"
    """

    # Kecepatan rata-rata lari manusia (km/h)
    # Referensi: rata-rata manusia dewasa bisa berlari ~12 km/h, jalan cepat ~6 km/h
    # Untuk evakuasi massal diasumsikan ~8 km/h (campuran berjalan cepat dan lari kecil)
    SPEED_DEFAULTS = {
        "foot":  8,    # berjalan/lari kecil (evakuasi massal)
        "motor": 30,   # kendaraan motor
        "car":   50,   # kendaraan roda empat
    }

    def __init__(self, vektor_dir: Optional[str] = None, dem_mgr=None):
        """
        Parameters
        ----------
        vektor_dir : str
            Direktori berisi shapefile (jalan, desa, TES).
        dem_mgr : DEMManager
            Instance DEMManager dari server — dipakai untuk bobot elevasi & slope.
        """
        self.vektor_dir = vektor_dir
        self.dem_mgr    = dem_mgr

        # Cache data — diisi oleh build_caches()
        self.road_cache:  Optional[dict] = None
        self.graph_cache: Optional[dict] = None
        self.desa_cache:  Optional[dict] = None
        self.tes_cache:   Optional[dict] = None

        # SWE integration — diisi oleh set_swe_results()
        self._swe_results:   Optional[dict] = None
        self._flood_zones:   list = []   # [{lat, lon, radius_m, depth_m, t_min}]
        self._wave_arrival:  dict = {}   # {(lat_r, lon_r): t_arrival_min}

    # ── Cache management ─────────────────────────────────────────

    def build_caches(self):
        """
        Load semua data vektor dari vektor_dir dan bangun graph jalan.
        Dipanggil sekali saat startup server.
        """
        if not self.vektor_dir or not os.path.isdir(self.vektor_dir):
            print("  [WARN] EvacuationABMSolver: vektor_dir tidak valid - cache tidak dibangun")
            return

        # Jalan
        self.road_cache = _build_road_cache(self.vektor_dir)
        if self.road_cache:
            print(f"\n🔧 Membangun road graph ({self.road_cache['feature_count']} ruas)...")
            self.graph_cache = build_graph(self.road_cache["roads"], dem_mgr=self.dem_mgr)
            n_nodes = len(self.graph_cache["nodes"])
            n_edges = sum(len(v) for v in self.graph_cache["edges"].values())
            print(f"  [OK] Road graph: {n_nodes} node, {n_edges} edge")
        else:
            print("  [WARN] Road cache kosong")

        # Desa
        self.desa_cache = _build_desa_cache(self.vektor_dir)
        if not self.desa_cache:
            print("  [WARN] Desa cache kosong")

        # TES
        self.tes_cache = _build_tes_cache(self.vektor_dir)
        if not self.tes_cache:
            print("  [WARN] TES cache kosong")

    def cache_info(self) -> dict:
        """Ringkasan status semua cache — untuk endpoint /admin/info."""
        return {
            "road": {
                "cached":        bool(self.road_cache),
                "source_file":   self.road_cache["source_file"]   if self.road_cache else None,
                "feature_count": self.road_cache["feature_count"] if self.road_cache else 0,
                "graph_nodes":   len(self.graph_cache["nodes"])   if self.graph_cache else 0,
                "graph_edges":   (sum(len(v) for v in self.graph_cache["edges"].values())
                                  if self.graph_cache else 0),
                "dem_integrated": self.dem_mgr is not None,
            },
            "desa": {
                "cached":      bool(self.desa_cache),
                "source_file": self.desa_cache["source_file"] if self.desa_cache else None,
                "count":       self.desa_cache["count"]        if self.desa_cache else 0,
            },
            "tes": {
                "cached":      bool(self.tes_cache),
                "source_file": self.tes_cache["source_file"] if self.tes_cache else None,
                "count":       self.tes_cache["count"]        if self.tes_cache else 0,
            },
        }

    # ── SWE integration ──────────────────────────────────────────

    def set_swe_results(self, swe_output: dict):
        """
        Terima hasil simulasi SWE untuk integrasi ke rute evakuasi & ABM.
        Menggunakan grid-based lookup untuk performa tinggi.
        """
        if not swe_output or not isinstance(swe_output, dict):
            return

        self._swe_results = swe_output
        self._flood_grids  = {}      # t_min -> set((j, i))
        self._wave_arrival = {}      # (j, i) -> t_arrival_min
        self._grid_meta    = swe_output.get("grid_meta", {})

        FLOOD_THRESHOLD_M = 0.1   # ketinggian air minimal dianggap tergenang (meter)

        # ── FORMAT BARU: wave_frames + grid_meta (swe_solver.py) ──────────────
        wave_frames = swe_output.get("wave_frames", [])
        grid_meta   = self._grid_meta

        if wave_frames and grid_meta:
            ny = grid_meta.get("ny", 1)
            nx = grid_meta.get("nx", 1)

            if ny < 2 or nx < 2:
                print("  [WARN] grid_meta tidak lengkap, SWE integration dilewati")
                return

            for frame in wave_frames:
                t_min   = frame.get("t_min", 0)
                eta_flat = frame.get("eta_flat", [])
                if not eta_flat: continue
                
                flooded_indices = set()
                for idx, h in enumerate(eta_flat):
                    if abs(h) < FLOOD_THRESHOLD_M:
                        continue
                    
                    j = idx // nx
                    i = idx % nx
                    flooded_indices.add((j, i))
                    
                    if (j, i) not in self._wave_arrival:
                        self._wave_arrival[(j, i)] = t_min
                
                self._flood_grids[t_min] = flooded_indices
            
            print(f"  [OK] SWE integration: {len(self._flood_grids)} time frames, "
                  f"{len(self._wave_arrival)} titik wave arrival")

    def _is_flooded(self, lat: float, lon: float, t_min: float) -> bool:
        """
        Cek apakah titik (lat, lon) tergenang pada waktu t_min menggunakan lookup grid.
        O(1) complexity per check.
        """
        if not self._grid_meta or not self._flood_grids:
            return False

        gm = self._grid_meta
        lat_min, lat_max = gm.get("lat_min"), gm.get("lat_max")
        lon_min, lon_max = gm.get("lon_min"), gm.get("lon_max")
        ny, nx = gm.get("ny"), gm.get("nx")

        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            return False

        # Map current lat/lon to grid indices
        j = int((lat - lat_min) / (lat_max - lat_min) * (ny - 1))
        i = int((lon - lon_min) / (lon_max - lon_min) * (nx - 1))
        j = max(0, min(ny - 1, j))
        i = max(0, min(nx - 1, i))

        # Cari frame terdekat yang sudah lewat (t <= t_min)
        available_times = sorted([t for t in self._flood_grids.keys() if t <= t_min], reverse=True)
        if not available_times:
            return False
        
        target_t = available_times[0]
        return (j, i) in self._flood_grids[target_t]

    def _wave_arrival_at(self, lat: float, lon: float) -> Optional[float]:
        """Waktu kedatangan gelombang (menit) menggunakan lookup grid."""
        if not self._grid_meta or not self._wave_arrival:
            return None

        gm = self._grid_meta
        lat_min, lat_max = gm.get("lat_min"), gm.get("lat_max")
        lon_min, lon_max = gm.get("lon_min"), gm.get("lon_max")
        ny, nx = gm.get("ny"), gm.get("nx")

        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            return None

        j = int((lat - lat_min) / (lat_max - lat_min) * (ny - 1))
        i = int((lon - lon_min) / (lon_max - lon_min) * (nx - 1))
        j = max(0, min(ny - 1, j))
        i = max(0, min(nx - 1, i))

        return self._wave_arrival.get((j, i), None)

    # ── Route computation ─────────────────────────────────────────

    def compute_route(self, origin: dict, destination: dict,
                      method: str = "network", transport: str = "car",
                      weight: str = "composite", roads: list = None) -> dict:
        """
        Hitung rute evakuasi dari origin ke destination.

        Parameters
        ----------
        origin      : {lat, lon}
        destination : {lat, lon}
        method      : 'network' (3 rute) | 'dijkstra' | 'astar'
        transport   : 'foot' | 'motor' | 'car'
        weight      : 'composite' | 'time' | 'distance'
        roads       : list road dari frontend (opsional, fallback ke cache)

        Return dict siap pakai sebagai response endpoint.
        """
        try:
            speed_kmh = self.SPEED_DEFAULTS.get(transport, 30)

            # Filter roads berdasarkan transport
            filtered_roads = []
            src_roads = roads or (self.road_cache["roads"] if self.road_cache else [])
            for r in src_roads:
                hw = r.get("highway", "")
                if transport in ("motor", "car") and hw in ("footway", "path", "steps"):
                    continue
                adjusted = dict(r)
                adjusted["speed_kmh"] = min(r.get("speed_kmh", 20), speed_kmh)
                filtered_roads.append(adjusted)

            if not filtered_roads:
                return {"error": "Tidak ada data jalan tersedia"}

            # Pilih graph: gunakan cache jika tersedia dan roads tidak dikirim dari luar
            if roads and len(roads) >= 50:
                graph = build_graph(filtered_roads, dem_mgr=self.dem_mgr)
            elif self.graph_cache:
                graph = self.graph_cache
            else:
                graph = build_graph(filtered_roads, dem_mgr=self.dem_mgr)

            if not graph or not graph["nodes"]:
                return {"error": "Graph kosong — data jalan tidak valid"}

            nodes = graph["nodes"]
            olat, olon = origin.get("lat"), origin.get("lon")
            dlat, dlon = destination.get("lat"), destination.get("lon")

            start_idx, start_dist = nearest_node(nodes, olat, olon)
            end_idx,   end_dist   = nearest_node(nodes, dlat, dlon)

            # ── Helpers ──────────────────────────────────────────
            def get_elev_profile(path_coords):
                profile = []
                for lat, lon in path_coords:
                    if self.dem_mgr:
                        e, _ = self.dem_mgr.query(lon, lat)
                        profile.append(round(float(e), 1) if e is not None else 0.0)
                    else:
                        profile.append(0.0)
                return profile

            def slope_stats(path_coords):
                slopes = []
                for i in range(len(path_coords) - 1):
                    a, b = path_coords[i], path_coords[i + 1]
                    d = haversine_m(a[0], a[1], b[0], b[1])
                    if d < 1:
                        continue
                    ea = eb = 0.0
                    if self.dem_mgr:
                        v, _ = self.dem_mgr.query(a[1], a[0]); ea = float(v) if v else 0.0
                        v, _ = self.dem_mgr.query(b[1], b[0]); eb = float(v) if v else 0.0
                    slopes.append(abs(eb - ea) / d * 100)
                return {
                    "avg_slope_pct": round(sum(slopes) / len(slopes), 1) if slopes else 0.0,
                    "max_slope_pct": round(max(slopes), 1)               if slopes else 0.0,
                }

            def path_metrics(path_coords):
                total_dist = sum(
                    haversine_m(path_coords[i][0], path_coords[i][1],
                                path_coords[i + 1][0], path_coords[i + 1][1])
                    for i in range(len(path_coords) - 1)
                )
                t_min = (total_dist / 1000) / speed_kmh * 60
                return total_dist, t_min

            def build_route_dict(coords, label, method_str, color, badge):
                dist_m, t_min = path_metrics(coords)
                elev_profile  = get_elev_profile(coords)
                s_stats       = slope_stats(coords)
                min_e = min(elev_profile) if elev_profile else 0
                max_e = max(elev_profile) if elev_profile else 0

                # Tandai segmen rute yang tergenang (integrasi SWE)
                flooded_segments = []
                if self._flood_zones:
                    for i, (lat, lon) in enumerate(coords):
                        if self._is_flooded(lat, lon, t_min=0):
                            flooded_segments.append(i)

                return {
                    "label":          label,
                    "method":         method_str,
                    "color":          color,
                    "badge":          badge,
                    "path":           coords,
                    "distance_m":     round(dist_m),
                    "distance_km":    round(dist_m / 1000, 2),
                    "time_min":       round(t_min, 1),
                    "time_str":       (f"{int(t_min // 60)}j {int(t_min % 60)} mnt"
                                      if t_min >= 60 else f"{round(t_min)} mnt"),
                    "node_count":     len(coords),
                    "elevation_profile": elev_profile[::max(1, len(elev_profile) // 50)],
                    "min_elevation_m": round(min_e, 1),
                    "max_elevation_m": round(max_e, 1),
                    "elev_gain_m":    round(max_e - min_e, 1),
                    "flooded_segments": flooded_segments,
                    "has_flood_risk":   len(flooded_segments) > 0,
                    **s_stats,
                }

            # ── Jalankan algoritma ────────────────────────────────
            routes_out = []

            def try_dijkstra(w, label, color, badge):
                _, coords = dijkstra(graph, start_idx, end_idx, weight=w)
                if coords:
                    routes_out.append(build_route_dict(coords, label, f"dijkstra_{w}", color, badge))

            def try_astar(w, label, color, badge):
                _, coords = astar(graph, start_idx, end_idx, weight=w,
                                  transport_speed_kmh=speed_kmh)
                if coords:
                    routes_out.append(build_route_dict(coords, label, f"astar_{w}", color, badge))

            if method == "network":
                try_dijkstra("composite", "Rute Optimal (DEM+Slope)", "#4ade80", "badge-opt")
                try_dijkstra("time",      "Rute Tercepat",            "#facc15", "badge-alt")
                try_astar("distance",     "Rute Terpendek (A*)",      "#60a5fa", "badge-bpbd")
            elif method == "astar":
                try_astar(weight, "A* — Rute Heuristik", "#60a5fa", "badge-bpbd")
            else:
                try_dijkstra(weight, "Dijkstra — Jalur Optimal", "#4ade80", "badge-opt")

            if not routes_out:
                return {"error": "Rute tidak ditemukan — origin/destination mungkin di luar jaringan jalan"}

            best = routes_out[0]
            return {
                "ok":           True,
                "method":       method,
                "transport":    transport,
                "weight":       weight,
                "routes":       routes_out,
                # Backward-compat fields
                "path":             best["path"],
                "distance_m":       best["distance_m"],
                "distance_km":      best["distance_km"],
                "time_min":         best["time_min"],
                "time_str":         best["time_str"],
                "node_count":       best["node_count"],
                "elevation_profile":  best["elevation_profile"],
                "min_elevation_m":    best["min_elevation_m"],
                "max_elevation_m":    best["max_elevation_m"],
                "elev_gain_m":        best["elev_gain_m"],
                "avg_slope_pct":      best["avg_slope_pct"],
                "snap_origin_dist_m": round(start_dist),
                "snap_dest_dist_m":   round(end_dist),
                "graph_nodes":        len(nodes),
                "dem_available":      self.dem_mgr is not None,
                "swe_integrated":     bool(self._swe_results),
            }

        except Exception as e:
            import traceback
            return {"error": str(e), "trace": traceback.format_exc()}

    # ── ABM simulation ───────────────────────────────────────────

    def run_abm(self, body: dict) -> dict:
        """
        Simulasi Agent-Based Model evakuasi tsunami.

        Setiap agen merepresentasikan sekelompok penduduk dari satu desa.
        Distribusi agen proporsional terhadap jumlah penduduk per desa.
        Kecepatan dasar: rata-rata lari manusia (~8 km/h untuk evakuasi massal).

        Parameters (dari body dict)
        ---------------------------
        desa_list          : [{name, penduduk, lat, lon}, ...]
        tes_list           : [{name, lat, lon, kapasitas}, ...]
        roads              : [...] dari /network/roads (opsional)
        transport          : 'foot' | 'motor' | 'car'
        inundation_runup_m : ketinggian banjir dari SWE (meter)
        warning_time_min   : waktu peringatan dini (menit)
        sim_duration_min   : durasi simulasi total (menit)
        dt_min             : time step (menit)

        Return
        ------
        {ok, summary, agents, timeline, bottlenecks, arrived_by_desa}
        """
        try:
            desa_list   = body.get("desa_list", [])
            tes_list    = body.get("tes_list",  [])
            roads       = body.get("roads",     [])
            transport   = body.get("transport", "foot")
            runup_m     = body.get("inundation_runup_m", 5.0)
            warning_min = body.get("warning_time_min", 20)
            sim_dur     = body.get("sim_duration_min", 120)
            dt_min      = body.get("dt_min", 1)

            speed_kmh = self.SPEED_DEFAULTS.get(transport, 8)

            if not desa_list:
                return {"error": "desa_list kosong"}
            if not tes_list:
                tes_list = [{"name": "TES Default", "lat": -7.99,
                             "lon": 110.28, "kapasitas": 99999}]

            # ── Pilih graph ───────────────────────────────────────
            graph = None
            if self.graph_cache:
                graph = self.graph_cache
                print(f"  ✓ ABM: pakai graph_cache ({len(self.graph_cache['nodes'])} node)")
            elif roads and len(roads) >= 50:
                graph = build_graph(roads, dem_mgr=self.dem_mgr)
            elif self.vektor_dir:
                # Fallback: muat shapefile lokal langsung
                rc = _build_road_cache(self.vektor_dir)
                if rc:
                    graph = build_graph(rc["roads"], dem_mgr=self.dem_mgr)
                    print(f"  ✓ ABM fallback graph dari shapefile: {len(rc['roads'])} ruas")

            # ── Buat agen per desa ────────────────────────────────
            agents = []
            for desa in desa_list:
                if not desa.get("lat") or not desa.get("lon"):
                    continue
                pop  = desa.get("penduduk", 1000)
                dlat = desa["lat"]
                dlon = desa["lon"]

                # Cek terdampak inundasi via DEM
                is_affected = True
                if self.dem_mgr and self.dem_mgr.tiles:
                    elev, _ = self.dem_mgr.query(dlon, dlat)
                    if elev is not None:
                        is_affected = float(elev) <= runup_m + 2.0

                # Cek juga via SWE jika tersedia (lebih akurat)
                if self._swe_results and not is_affected:
                    is_affected = self._is_flooded(dlat, dlon, t_min=warning_min)

                if not is_affected:
                    continue

                # Cari TES terdekat
                best_tes, best_d = None, 1e18
                for tes in tes_list:
                    if not tes.get("lat") or not tes.get("lon"):
                        continue
                    d = haversine_m(dlat, dlon, tes["lat"], tes["lon"])
                    if d < best_d:
                        best_d, best_tes = d, tes

                if not best_tes:
                    continue

                # Hitung rute ke TES
                route_path    = None
                route_dist_m  = best_d
                route_time_min = (best_d / 1000) / speed_kmh * 60

                if graph and graph.get("nodes"):
                    nodes_latlon = [(n[0], n[1]) for n in graph["nodes"]]
                    si, _ = nearest_node(nodes_latlon, dlat, dlon)
                    ei, _ = nearest_node(nodes_latlon, best_tes["lat"], best_tes["lon"])
                    _, path = dijkstra(graph, si, ei, weight="composite")
                    if path:
                        route_path = path
                        dist_m = sum(
                            haversine_m(path[i][0], path[i][1], path[i+1][0], path[i+1][1])
                            for i in range(len(path) - 1)
                        )
                        route_dist_m   = dist_m
                        route_time_min = (dist_m / 1000) / speed_kmh * 60

                        # ── SWE: cek apakah rute tergenang, reroute jika perlu ──
                        if self._flood_zones and path:
                            route_flooded = any(
                                self._is_flooded(p[0], p[1], t_min=warning_min)
                                for p in path[::5]   # sample setiap 5 titik untuk efisiensi
                            )
                            if route_flooded:
                                # Coba rute dengan bobot 'time' (lebih agresif menghindari area)
                                _, alt_path = dijkstra(graph, si, ei, weight="time")
                                if alt_path:
                                    alt_flooded = any(
                                        self._is_flooded(p[0], p[1], t_min=warning_min)
                                        for p in alt_path[::5]
                                    )
                                    if not alt_flooded:
                                        route_path = alt_path
                                        dist_m = sum(
                                            haversine_m(
                                                alt_path[i][0], alt_path[i][1],
                                                alt_path[i+1][0], alt_path[i+1][1]
                                            )
                                            for i in range(len(alt_path) - 1)
                                        )
                                        route_dist_m   = dist_m
                                        route_time_min = (dist_m / 1000) / speed_kmh * 60

                # ── Pembagian agen per desa ───────────────────────
                # Maks 10 agen per desa, 1 agen per 500 penduduk
                n_agents     = min(10, max(1, pop // 500))
                pop_per_agent = pop // n_agents

                for ag_i in range(n_agents):
                    # Scatter posisi awal (~200m radius)
                    jitter_lat = dlat + random.gauss(0, 0.001)
                    jitter_lon = dlon + random.gauss(0, 0.001)

                    # Response delay: 0–15 menit setelah peringatan
                    response_delay = max(0, min(15, random.gauss(5, 3)))

                    # Kecepatan individu: variasi ±30% dari kecepatan dasar
                    # Kemiringan juga mempengaruhi kecepatan (slope penalty dari graph)
                    ind_speed = speed_kmh * random.uniform(0.7, 1.1)

                    # Waktu kedatangan gelombang di titik agen (dari SWE)
                    wave_t = self._wave_arrival_at(jitter_lat, jitter_lon)

                    agents.append({
                        "id":              f"{desa['name']}_{ag_i}",
                        "desa":            desa["name"],
                        "population":      pop_per_agent,
                        "start_lat":       jitter_lat,
                        "start_lon":       jitter_lon,
                        "target_tes":      best_tes["name"],
                        "target_lat":      best_tes["lat"],
                        "target_lon":      best_tes["lon"],
                        "route_path":      route_path or [
                            [jitter_lat, jitter_lon],
                            [best_tes["lat"], best_tes["lon"]]
                        ],
                        "route_dist_m":    route_dist_m,
                        "route_time_min":  route_time_min,
                        "speed_kmh":       ind_speed,
                        "depart_min":      warning_min + response_delay,
                        "arrive_min":      warning_min + response_delay + route_time_min,
                        "wave_arrival_min": wave_t,
                        "status":          "waiting",   # waiting | moving | arrived | stranded
                    })

            if not agents:
                return {"error": "Tidak ada agen yang dibuat — cek data desa dan zona inundasi"}

            # ── Simulasi time-step ────────────────────────────────
            t_steps    = list(range(0, sim_dur + 1, dt_min))
            timeline   = []
            bottlenecks: Dict[str, int] = {}

            for t in t_steps:
                moved = arrived = stranded = 0
                positions = []

                for ag in agents:
                    # Cek apakah agen terjebak gelombang (wave arrives before agent)
                    if (ag["wave_arrival_min"] is not None
                            and ag["status"] not in ("arrived",)
                            and t >= ag["wave_arrival_min"]
                            and t < ag.get("arrive_min", 1e18)):
                        ag["status"] = "stranded"

                    if ag["status"] == "stranded":
                        stranded += 1
                        positions.append({
                            "id": ag["id"], "lat": ag["start_lat"],
                            "lon": ag["start_lon"], "status": "stranded",
                            "pop": ag["population"],
                        })
                        continue

                    if t < ag["depart_min"]:
                        positions.append({
                            "id": ag["id"], "lat": ag["start_lat"],
                            "lon": ag["start_lon"], "status": "waiting",
                            "pop": ag["population"],
                        })
                        continue

                    elapsed       = t - ag["depart_min"]
                    dist_covered  = (ag["speed_kmh"] / 60) * elapsed * 1000

                    if ag["status"] == "arrived" or dist_covered >= ag["route_dist_m"]:
                        ag["status"] = "arrived"
                        arrived += 1
                        positions.append({
                            "id": ag["id"], "lat": ag["target_lat"],
                            "lon": ag["target_lon"], "status": "arrived",
                            "pop": ag["population"],
                        })
                        continue

                    # Interpolasi posisi sepanjang rute
                    progress  = min(1.0, dist_covered / max(ag["route_dist_m"], 1))
                    path      = ag["route_path"]
                    idx_f     = progress * (len(path) - 1)
                    i0        = min(int(idx_f), len(path) - 2)
                    frac      = idx_f - i0
                    p1, p2    = path[i0], path[min(i0 + 1, len(path) - 1)]
                    cur_lat   = p1[0] + frac * (p2[0] - p1[0])
                    cur_lon   = p1[1] + frac * (p2[1] - p1[1])

                    # Cek posisi agen saat ini tergenang (SWE runtime check)
                    if self._flood_zones and self._is_flooded(cur_lat, cur_lon, t_min=t):
                        ag["status"] = "stranded"
                        stranded += 1
                        positions.append({
                            "id": ag["id"], "lat": cur_lat, "lon": cur_lon,
                            "status": "stranded", "pop": ag["population"],
                        })
                        continue

                    ag["status"] = "moving"
                    moved += 1

                    # Bottleneck: hitung kepadatan per segmen jalan
                    seg_key = f"{round(cur_lat, 3)},{round(cur_lon, 3)}"
                    bottlenecks[seg_key] = bottlenecks.get(seg_key, 0) + ag["population"]

                    positions.append({
                        "id": ag["id"], "lat": cur_lat, "lon": cur_lon,
                        "status": "moving", "pop": ag["population"],
                    })

                # Stranded tambahan: masih waiting terlalu lama
                stranded += sum(
                    1 for ag in agents
                    if ag["status"] == "waiting" and t > warning_min + 30
                )

                # Sample setiap 5 menit untuk efisiensi payload
                if t % 5 == 0 or t == sim_dur:
                    timeline.append({
                        "t_min":    t,
                        "moving":   moved,
                        "arrived":  sum(1 for ag in agents if ag["status"] == "arrived"),
                        "waiting":  sum(1 for ag in agents if ag["status"] == "waiting"),
                        "stranded": stranded,
                        "positions": positions[:200],   # max 200 posisi per frame
                    })

            # ── Statistik ringkasan ───────────────────────────────
            total_pop      = sum(ag["population"] for ag in agents)
            final_arrived  = sum(ag["population"] for ag in agents if ag["status"] == "arrived")
            final_stranded = sum(ag["population"] for ag in agents if ag["status"] == "stranded")
            avg_time       = sum(ag["route_time_min"] for ag in agents) / len(agents)

            arrived_by_desa: Dict[str, int] = {}
            for ag in agents:
                if ag["status"] == "arrived":
                    arrived_by_desa[ag["desa"]] = (
                        arrived_by_desa.get(ag["desa"], 0) + ag["population"]
                    )

            bottleneck_list = sorted(
                [
                    {"lat": float(k.split(",")[0]), "lon": float(k.split(",")[1]), "count": v}
                    for k, v in bottlenecks.items()
                ],
                key=lambda x: -x["count"],
            )[:20]

            return {
                "ok": True,
                "summary": {
                    "total_agents":      len(agents),
                    "total_population":  total_pop,
                    "arrived_pop":       final_arrived,
                    "stranded_pop":      final_stranded,
                    "arrival_rate":      round(final_arrived / max(total_pop, 1) * 100, 1),
                    "stranded_rate":     round(final_stranded / max(total_pop, 1) * 100, 1),
                    "avg_time_min":      round(avg_time, 1),
                    "max_time_min":      round(max(ag["route_time_min"] for ag in agents), 1),
                    "warning_time_min":  warning_min,
                    "tes_count":         len(tes_list),
                    "desa_count":        len(desa_list),
                    "transport":         transport,
                    "speed_base_kmh":    speed_kmh,
                    "swe_integrated":    bool(self._swe_results),
                },
                "agents": [
                    {
                        "id":           ag["id"],
                        "desa":         ag["desa"],
                        "population":   ag["population"],
                        "start":        [ag["start_lat"], ag["start_lon"]],
                        "target":       [ag["target_lat"], ag["target_lon"]],
                        "target_tes":   ag["target_tes"],
                        "route_path":   ag["route_path"][:50],   # max 50 titik per agen
                        "dist_km":      round(ag["route_dist_m"] / 1000, 2),
                        "time_min":     round(ag["route_time_min"], 1),
                        "depart_min":   round(ag["depart_min"], 1),
                        "arrive_min":   round(ag["arrive_min"], 1),
                        "wave_arrival_min": ag.get("wave_arrival_min"),
                        "status":       ag["status"],
                    }
                    for ag in agents
                ],
                "timeline":        timeline,
                "bottlenecks":     bottleneck_list,
                "arrived_by_desa": arrived_by_desa,
            }

        except Exception as e:
            import traceback
            return {"error": str(e), "trace": traceback.format_exc()}
