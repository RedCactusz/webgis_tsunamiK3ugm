"""
TsunamiSim   Local Bathymetry Server v2
=========================================
BATNAS (multi-tile GeoTIFF) + GEBCO proxy, dengan masking daratan otomatis.

Masalah: BATNAS belum dipotong   berisi nilai daratan (positif) dan laut (negatif).
Solusi 3-lapis:
  Layer 1   Value threshold  : nilai BATNAS < -0.5 m   laut valid
  Layer 2   Sanity range     : -7500 < val < -0.5      kedalaman valid Java
  Layer 3   Coastline mask   : ray-casting terhadap shapefile garis pantai
             (hanya aktif jika --coastline diberikan, opsional tapi sangat akurat)

Cara pakai:
  pip install fastapi uvicorn rasterio httpx
  
  # Tanpa coastline (Layer 1+2 saja):
  python server_v2.py --batnas ./batnas --port 8000
  
  # Dengan coastline shapefile (Layer 1+2+3, paling akurat):
  python server_v2.py --batnas ./batnas --coastline ./Garis_Pantai_Bantul.shp --port 8000

Endpoint:
  GET /status
  GET /depth?lat=&lon=
  GET /depth/grid?lon_min=&lat_min=&lon_max=&lat_max=&cols=&rows=
  GET /depth/path?from_lat=&from_lon=&to_lat=&to_lon=&steps=
  GET /tiles/info
  GET /mask/test?lat=&lon=      debug endpoint untuk cek masking
"""

import os, sys, glob, struct, math, json, argparse, asyncio
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Any
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

#    Absolute Path Configuration                               
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from shapely.geometry import shape
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

#    Optional dependencies                                       
try:
    import rasterio
    from rasterio.transform import rowcol
    USE_RASTERIO = True
    print("  rasterio tersedia")
except ImportError:
    USE_RASTERIO = False
    print("  rasterio tidak ada - pakai parser manual")

try:
    import httpx
    USE_HTTPX = True
except ImportError:
    USE_HTTPX = False
    print("  httpx tidak ada   GEBCO synthetic only")


#                                                                
# LAYER 3: COASTLINE MASK
# Ray-casting point-in-polygon   tentukan sisi laut
#                                                                
class CoastlineMask:
    """
    Memuat shapefile garis pantai (polyline) dan menyediakan is_ocean(lon, lat).

    Metode: Coast Profile
                         
    Untuk setiap bin longitude (resolusi 0.002 ), kita simpan semua nilai
    latitude dari titik-titik garis pantai. Kemudian untuk query:

      lat < min(coast_lat di bin lon ini)     LAUT   (selatan garis pantai Jawa)
      lat > min(coast_lat di bin lon ini)     DARAT  (utara garis pantai Jawa)

    Metode ini lebih reliable daripada ray-casting untuk polyline (bukan polygon),
    dan sudah divalidasi dengan data Garis_Pantai_Bantul.shp.

    Untuk area di luar bbox coverage coastline   return None
    (masking hanya dari Layer 1+2).
    """

    BIN_SIZE     = 0.002    # resolusi bin lon ~220m
    SEARCH_RANGE = 5        # cari  5 bin (~1.1km) jika bin kosong
    COAST_BUFFER = 0.0005   # ~55m buffer di batas pantai untuk hindari noise

    def __init__(self, shp_path: str):
        self.segments: List[List[Tuple[float,float]]] = []
        self.bbox: Optional[Tuple]  = None
        self._profile: dict         = {}   # lon_bin   [lat, lat, ...]
        self._load(shp_path)

    def _load(self, path: str):
        print(f"\n  Memuat coastline mask: {path}")
        try:
            with open(path, 'rb') as f:
                data = f.read()

            pos = 100
            while pos < len(data) - 8:
                if pos + 8 > len(data): break
                content_len = struct.unpack_from('>I', data, pos+4)[0] * 2
                pos += 8
                if pos + 4 > len(data): break
                stype = struct.unpack_from('<I', data, pos)[0]
                if stype in (3, 5):
                    num_parts  = struct.unpack_from('<I', data, pos+36)[0]
                    num_points = struct.unpack_from('<I', data, pos+40)[0]
                    parts = list(struct.unpack_from(f'<{num_parts}I', data, pos+44))
                    parts.append(num_points)
                    coords = struct.unpack_from(f'<{num_points*2}d', data, pos+44+num_parts*4)
                    for i in range(num_parts):
                        s, e = parts[i], parts[i+1]
                        seg = [(coords[j*2], coords[j*2+1]) for j in range(s, e)]
                        self.segments.append(seg)
                pos += content_len

            if self.segments:
                all_lons = [p[0] for seg in self.segments for p in seg]
                all_lats = [p[1] for seg in self.segments for p in seg]
                if all_lons and all_lats:
                    self.bbox = (min(all_lons), min(all_lats), max(all_lons), max(all_lats))
                else:
                    self.bbox = None
            else:
                self.bbox = None

            # Bangun profil: lon_bin   list of coast latitudes
            for seg in self.segments:
                for lon, lat in seg:
                    key = round(round(lon / self.BIN_SIZE) * self.BIN_SIZE, 6)
                    self._profile.setdefault(key, []).append(lat)

            print(f"    {len(self.segments)} segmen, {sum(len(v) for v in self._profile.values())} sel")
            print(f"    {len(self._profile)} bin longitude (resolusi {self.BIN_SIZE} )")
            if self.bbox:
                print(f"  bbox: lon {self.bbox[0]:.4f} {self.bbox[2]:.4f}, lat {self.bbox[1]:.4f} {self.bbox[3]:.4f}")
            else:
                print(f"    Coastline bbox kosong")

        except Exception as e:
            print(f"    Gagal load coastline: {e}")
            self.segments = []; self.bbox = None

    def in_coverage(self, lon: float, lat: float) -> bool:
        if not self.bbox: return False
        buf = 0.05
        return (self.bbox[0]-buf <= lon <= self.bbox[2]+buf and
                self.bbox[1]-buf <= lat <= self.bbox[3]+buf)

    def _get_coast_lats(self, lon: float) -> Optional[List[float]]:
        """Cari semua coast lat di sekitar longitude ini."""
        lon_bin = round(round(lon / self.BIN_SIZE) * self.BIN_SIZE, 6)
        lats = []
        for i in range(-self.SEARCH_RANGE, self.SEARCH_RANGE+1):
            key = round(lon_bin + i * self.BIN_SIZE, 6)
            if key in self._profile:
                lats.extend(self._profile[key])
        return lats if lats else None

    def is_ocean(self, lon: float, lat: float) -> Optional[bool]:
        """
        Return:
          True    LAUT   (gunakan BATNAS)
          False   DARAT  (skip BATNAS   fallback GEBCO)
          None    di luar coverage shapefile (Layer 1+2 saja)
        """
        if not self.in_coverage(lon, lat):
            return None

        coast_lats = self._get_coast_lats(lon)
        if coast_lats is None:
            return None

        # Untuk Jawa Selatan: laut di selatan garis pantai
        #   lat titik < min(coast_lat) = laut
        min_coast_lat = min(coast_lats)
        return lat < (min_coast_lat + self.COAST_BUFFER)

    def debug_info(self, lon: float, lat: float) -> dict:
        in_cov     = self.in_coverage(lon, lat)
        coast_lats = self._get_coast_lats(lon)
        result     = self.is_ocean(lon, lat)
        return {
            "in_coverage":       in_cov,
            "coast_lats_nearby": len(coast_lats) if coast_lats else 0,
            "min_coast_lat":     min(coast_lats) if coast_lats else None,
            "query_lat":         lat,
            "result":            result,
            "interpretation":    "laut" if result is True else ("daratan" if result is False else "di_luar_coverage")
        }


#                                                                
# 3-LAYER BATNAS MASKING
#                                                                
def is_valid_ocean_depth(val: float,
                         lon: float,
                         lat: float,
                         coast_mask: Optional[CoastlineMask] = None,
                         nodata_values: tuple = (32767, -32768, -9999, 0)) -> Tuple[bool, str]:
    """
    Cek apakah nilai BATNAS merupakan kedalaman laut yang valid.

    Returns: (is_valid: bool, reason: str)

    Layer 1   Threshold  : val harus < -0.5 m
    Layer 2   Sanity     : -7500 < val < -0.5 (kedalaman valid Samudra Hindia)
    Layer 3   Coastline  : konfirmasi posisi laut via shapefile (jika tersedia)
    """
    # Nodata
    if val in nodata_values or val is None:
        return False, "nodata"

    # Layer 1: nilai harus negatif (laut) atau nol (pantai)
    if val > 0.1:
        return False, f"layer1_positive_elevation({val:.1f}m)"

    # Layer 2: sanity range untuk Java region
    if val < -7500:
        return False, f"layer2_too_deep({val:.0f}m)"

    # Layer 3: coastline mask (opsional, hanya jika tersedia)
    if coast_mask is not None:
        ocean_status = coast_mask.is_ocean(lon, lat)
        if ocean_status is False:
            return False, f"layer3_land_by_coastline"
        # ocean_status == None   tidak bisa konfirmasi   lanjut dengan L1+L2

    return True, "valid_ocean"


#                                                                
# TIFF READER (Manual   tanpa rasterio)
#                                                                
class ManualTiffReader:
    def __init__(self, path: str):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        self._parse()

    def _u(self, fmt, off):
        return struct.unpack_from(self.endian + fmt, self.data, off)

    def _parse(self):
        self.endian = '<' if self.data[:2] == b'II' else '>'
        ifd = self._u('I', 4)[0]
        n   = self._u('H', ifd)[0]
        tags = {}
        p = ifd + 2
        for _ in range(n):
            tag, dtype, count, vraw = self._u('HHII', p)
            tags[tag] = (dtype, count, vraw)
            p += 12

        def tv(tag, default=0):
            if tag not in tags: return default
            d, c, v = tags[tag]
            if d == 3:  return struct.unpack_from(self.endian+'H', struct.pack(self.endian+'I', v))[0] if c==1 else v
            if d == 4:  return v
            if d == 5:  a,b = self._u('II', v); return a/b if b else 0
            if d == 12: return self._u('d', struct.pack(self.endian+'I',v)+b'\x00'*4)[0]
            return v

        self.width  = tags[256][2]; self.height = tags[257][2]
        self.bits   = tv(258, 16);  self.tile_w = tv(322, 0)
        self.tile_h = tv(323, 0);   self.tiled  = self.tile_w > 0
        self.sfmt   = 'h' if self.bits==16 else ('f' if self.bits==32 else 'h')

        if self.tiled:
            d,c,o = tags[324]; self.tile_offs = list(self._u(f'{c}I', o))
            d,c,o = tags[325]; self.tile_lens = list(self._u(f'{c}I', o))
        else:
            d,c,o = tags.get(273,(0,1,0)); self.strip_offs = list(self._u(f'{c}I', o)) if c>1 else [o]
            self.rows_per_strip = tv(278, self.height)

        # Geo: scale + tiepoint
        if 33550 in tags and tags[33550][1]>=3:
            sc = self._u('3d', tags[33550][2]); self.dx=sc[0]; self.dy=-sc[1]
        else:
            self.dx=self.dy=None

        if 33922 in tags and tags[33922][1]>=6:
            tp = self._u('6d', tags[33922][2])
            self.lon0=tp[3]-tp[0]*(self.dx or 0)
            self.lat0=tp[4]-tp[1]*(self.dy or 0)
        else:
            self.lon0=self.lat0=None

        # Fallback TFW
        if self.lon0 is None:
            tfw = Path(self.path).with_suffix('.tfw')
            if tfw.exists():
                v=[float(l.strip()) for l in tfw.read_text().splitlines() if l.strip()]
                if len(v)>=6:
                    self.dx=v[0]; self.dy=v[3]; self.lon0=v[4]; self.lat0=v[5]

        self.bbox = (
            self.lon0,
            self.lat0 + self.height*self.dy,
            self.lon0 + self.width*self.dx,
            self.lat0
        ) if self.lon0 else None

    def contains(self, lon, lat):
        if not self.bbox: return False
        return self.bbox[0]<=lon<=self.bbox[2] and self.bbox[1]<=lat<=self.bbox[3]

    def read_value(self, lon, lat):
        if not self.contains(lon, lat): return None
        col = int((lon-self.lon0)/self.dx)
        row = int((lat-self.lat0)/self.dy)
        col = max(0, min(col, self.width-1))
        row = max(0, min(row, self.height-1))

        if self.tiled:
            tx = col//self.tile_w; ty = row//self.tile_h
            tidx = ty*math.ceil(self.width/self.tile_w)+tx
            if tidx>=len(self.tile_offs): return None
            off=self.tile_offs[tidx]; nb=self.tile_lens[tidx]
            if off==0 or nb==0: return None
            tile=self.data[off:off+nb]
            pidx=(row%self.tile_h)*self.tile_w+(col%self.tile_w)
            bpp=self.bits//8
            if pidx*bpp+bpp>len(tile): return None
            return float(struct.unpack_from(self.endian+self.sfmt, tile, pidx*bpp)[0])
        else:
            si=row//self.rows_per_strip
            if si>=len(self.strip_offs): return None
            pidx=(row%self.rows_per_strip)*self.width+col
            bpp=self.bits//8
            return float(struct.unpack_from(self.endian+self.sfmt,
                         self.data, self.strip_offs[si]+pidx*bpp)[0])


#                                                                
# RASTERIO WRAPPER
#                                                                
class RasterioReader:
    def __init__(self, path):
        self.path = path
        self.ds   = rasterio.open(path)
        b = self.ds.bounds
        self.bbox    = (b.left, b.bottom, b.right, b.top)
        self.nodata  = self.ds.nodata

    def contains(self, lon, lat):
        return self.bbox[0]<=lon<=self.bbox[2] and self.bbox[1]<=lat<=self.bbox[3]

    def read_value(self, lon, lat):
        if not self.contains(lon, lat): return None
        try:
            r, c = rowcol(self.ds.transform, lon, lat)
            v = float(self.ds.read(1, window=rasterio.windows.Window(c,r,1,1))[0][0])
            return None if (self.nodata and v==self.nodata) else v
        except: return None


#                                                                
# TILE MANAGER
#                                                                
class BATNASTileManager:
    def __init__(self, tile_dir: str, coast_mask: Optional[CoastlineMask]=None):
        self.coast_mask = coast_mask
        self.tiles: list = []
        self._masked_land  = 0   # counter: berapa kali layer3 memblok daratan
        self._masked_value = 0   # counter: layer1+2
        self._valid_hits   = 0
        self._load(tile_dir)

    def _load(self, d):
        paths = []
        for pat in ['*.tif','*.tiff','*.TIF','*.TIFF']:
            paths.extend(glob.glob(os.path.join(d,'**',pat), recursive=True))
        print(f"\n  BATNAS tiles: {len(paths)} file ditemukan")
        for p in sorted(paths):
            try:
                r = RasterioReader(p) if USE_RASTERIO else ManualTiffReader(p)
                if r.bbox:
                    self.tiles.append(r)
                    print(f"    {Path(p).name:40s} {[round(x,4) for x in r.bbox]}")
                else:
                    print(f"    {Path(p).name}   bbox tidak terbaca")
            except Exception as e:
                print(f"    {Path(p).name}   {e}")
        print(f"\n  {len(self.tiles)} tile siap\n")

    def query(self, lon: float, lat: float) -> Tuple[Optional[float], str]:
        """
        Return: (depth_meters, source_label) atau (None, reason)
        depth_meters positif = laut, None = daratan/nodata
        """
        for tile in self.tiles:
            if not tile.contains(lon, lat):
                continue

            raw = tile.read_value(lon, lat)
            if raw is None:
                continue

            valid, reason = is_valid_ocean_depth(raw, lon, lat, self.coast_mask)

            if valid:
                self._valid_hits += 1
                return abs(raw), f"batnas({Path(tile.path).stem})"
            else:
                if 'layer3' in reason:
                    self._masked_land += 1
                else:
                    self._masked_value += 1
                # Tidak break   coba tile lain jika ada overlap
                continue

        return None, "not_in_batnas"

    def stats(self):
        return {
            "valid_hits":    self._valid_hits,
            "masked_land":   self._masked_land,   # layer 3 blocked
            "masked_value":  self._masked_value,  # layer 1+2 blocked
        }

    def coverage_bbox(self):
        if not self.tiles: return None
        lons = [t.bbox[0] for t in self.tiles]+[t.bbox[2] for t in self.tiles]
        lats = [t.bbox[1] for t in self.tiles]+[t.bbox[3] for t in self.tiles]
        return (min(lons), min(lats), max(lons), max(lats))

    def tile_info(self):
        return [{"file": Path(t.path).name,
                 "bbox": {"lon_min":round(t.bbox[0],6),"lat_min":round(t.bbox[1],6),
                          "lon_max":round(t.bbox[2],6),"lat_max":round(t.bbox[3],6)}}
                for t in self.tiles]




#                                                                
# DEM MANAGER   baca DEMNAS / DEM raster untuk elevasi daratan
# Dipisahkan dari BATNAS agar masking tidak bentrok
#                                                                
class DEMManager:
    """
    Memuat satu atau lebih file DEM (GeoTIFF) untuk elevasi daratan.
    Nilai positif = elevasi mdpl, nilai negatif = abaikan (biarkan BATNAS).
    Dipakai untuk:
      - Zona inundasi (elevasi daratan vs runup)
      - Override kedalaman di daratan agar tidak salah masuk BATNAS
    """

    def __init__(self, dem_dir: str):
        self.tiles: list = []
        self._load(dem_dir)

    def _load(self, d: str):
        paths = []
        for pat in ['*.tif', '*.tiff', '*.TIF', '*.TIFF']:
            paths.extend(glob.glob(os.path.join(d, '**', pat), recursive=True))
        # Hanya file yang namanya mengandung 'dem' atau 'DEM'
        paths = [p for p in paths if 'dem' in Path(p).name.lower()]
        print(f"\n  DEM tiles: {len(paths)} file ditemukan")
        for p in sorted(paths):
            try:
                r = RasterioReader(p) if USE_RASTERIO else ManualTiffReader(p)
                if r.bbox:
                    self.tiles.append(r)
                    print(f"    {Path(p).name:40s} bbox={[round(x,4) for x in r.bbox]}")
                else:
                    print(f"    {Path(p).name}   bbox tidak terbaca")
            except Exception as e:
                print(f"    {Path(p).name}   {e}")
        
        # Urutkan tile dari area terkecil ke terbesar. 
        # Ini menjamin DEM lokal (resolusi tinggi) akan meng-override (didahulukan) 
        # dibandingkan DEM regional/global yang areanya sangat luas.
        self.tiles.sort(key=lambda t: abs((t.bbox[2]-t.bbox[0]) * (t.bbox[3]-t.bbox[1])))
        
        print(f"    {len(self.tiles)} DEM tile siap (diurutkan berdasarkan prioritas lokalitas)")

    def query(self, lon: float, lat: float) -> Tuple[Optional[float], str]:
        """
        Kembalikan (elevasi_mdpl, sumber) atau (None, 'not_in_dem').
        Hanya kembalikan nilai jika tile ada   tidak ada fallback sintetis.
        """
        for tile in self.tiles:
            if not tile.contains(lon, lat):
                continue
            val = tile.read_value(lon, lat)
            if val is None:
                continue
            # Nodata check (DEMNAS biasanya -9999 atau 3.4e38)
            if abs(val) > 9000 or val < -500:
                continue
            return float(val), f"dem({Path(tile.path).stem})"
        return None, "not_in_dem"

    def coverage_bbox(self):
        if not self.tiles: return None
        lons = [t.bbox[0] for t in self.tiles] + [t.bbox[2] for t in self.tiles]
        lats = [t.bbox[1] for t in self.tiles] + [t.bbox[3] for t in self.tiles]
        return (min(lons), min(lats), max(lons), max(lats))

    def tile_info(self):
        return [{"file": Path(t.path).name,
                 "bbox": {"lon_min": round(t.bbox[0],6), "lat_min": round(t.bbox[1],6),
                          "lon_max": round(t.bbox[2],6), "lat_max": round(t.bbox[3],6)}}
                for t in self.tiles]

#                                                                
# GEBCO LOCAL (GeoTIFF)   replaces HTTP proxy for speed
#                                                                
GEBCO_CACHE: dict = {}
_gebco_ds = None
_gebco_band = None

def _init_gebco_local():
    """Try to load local GEBCO GeoTIFF from Raster directory."""
    global _gebco_ds, _gebco_band
    import glob
    raster_base = r"D:\STUDY\S2 Geomatika UGM\MK\Semester 2\KomGeo\Pemodelan Tsunami\Data\Raster"
    patterns = [
        os.path.join(raster_base, "**", "gebco*.tif"),
        os.path.join(raster_base, "**", "GEBCO*.tif"),
    ]
    for pat in patterns:
        files = glob.glob(pat, recursive=True)
        if files:
            p = files[0]
            try:
                if USE_RASTERIO:
                    _gebco_ds = RasterioReader(p)
                    # For fast access in grid building, try to read the whole band if it's small
                    if _gebco_ds.ds.width * _gebco_ds.ds.height < 10000000: # < 10M pixels (~40MB)
                        _gebco_band = _gebco_ds.ds.read(1)
                        print(f"    GEBCO lokal aktif (rasterio + cached band): {Path(p).name}")
                    else:
                        print(f"    GEBCO lokal aktif (rasterio): {Path(p).name}")
                else:
                    _gebco_ds = ManualTiffReader(p)
                    print(f"    GEBCO lokal aktif (manual): {Path(p).name}")
                print(f"     Bounds: {[round(x,4) for x in _gebco_ds.bbox]}")
                return
            except Exception as e:
                print(f"    Gagal memuat GEBCO lokal: {e}")
    print("    GEBCO lokal tidak ditemukan   menggunakan HTTP fallback")

_init_gebco_local()

async def gebco_depth(lon: float, lat: float) -> Tuple[float, str]:
    # 1. Try local GEBCO first (instant)
    if _gebco_ds is not None:
        try:
            if _gebco_ds.contains(lon, lat):
                val = _gebco_ds.read_value(lon, lat)
                if val is not None:
                    # GEBCO uses negative for ocean depth
                    depth = abs(val) if val < 0 else val
                    return depth, "gebco_local"
        except Exception:
            pass

    # 2. Cache check
    key = (round(lon,1), round(lat,1))
    if key in GEBCO_CACHE:
        return GEBCO_CACHE[key]

    # 3. HTTP fallback (slow)
    if USE_HTTPX:
        try:
            delta = 0.05
            url = (
                "https://www.gebco.net/data_and_products/gebco_web_services"
                "/web_coverage_service/mapserv?SERVICE=WCS&VERSION=1.0.0"
                f"&REQUEST=GetCoverage&COVERAGE=gebco_2023_grid"
                f"&BBOX={lon-delta},{lat-delta},{lon+delta},{lat+delta}"
                "&CRS=EPSG:4326&WIDTH=3&HEIGHT=3&FORMAT=aaigrid"
            )
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(url)
                r.raise_for_status()
                val = _parse_aai(r.text)
                if val is not None and val < 0:
                    depth = abs(val)
                    GEBCO_CACHE[key] = (depth, "gebco_online")
                    return depth, "gebco_online"
        except Exception as e:
            pass  # fall through to synthetic

    # 4. Tidak ada fallback sintetis   return no_data
    return 0.0, "no_data"

def _parse_aai(text: str) -> Optional[float]:
    nodata = -9999.0; vals = []
    for l in text.strip().splitlines():
        lt = l.strip().lower()
        if lt.startswith('nodata_value'):
            try: nodata=float(lt.split()[1])
            except: pass
        elif lt and not lt[0].isalpha():
            for v in lt.split():
                try:
                    f=float(v)
                    if f!=nodata: vals.append(f)
                except: pass
    return sorted(vals)[len(vals)//2] if vals else None


def _synthetic(lon: float, lat: float) -> float:
    tr = -9.5-(lon-110)*0.03
    if lat > -7.8:  return -(30+abs(lon-110)*15+(lat+7.8)*40)
    if lat > -8.5:  return -(200+1800*max(0,min(1,(-8.5-lat)/0.7)))
    if lat > -9.5:  return -(2000+3000*max(0,min(1,(-9.5-lat)/1)))
    if lat > -10.5: d=abs(lat-(tr-0.3)); return -(7000+450*max(0,0.5-d))
    return -(5000-(lat+10.5)*300)



#                                                                
# SHAPEFILE   GEOJSON CONVERTER (admin & vektor layers)
#                                                                

import geopandas as gpd

def read_dbf_attrs(path: str) -> List[dict]:
    """Geopandas akan menangani ini secara otomatis, fungsi ini dipertahankan hanya jika ada pemanggilan eksternal."""
    try:
        gdf = gpd.read_file(path)
        return gdf.drop(columns='geometry').to_dict('records')
    except:
        return []

def get_valid_land_point(poly, dem_manager=None) -> Tuple[float, float]:
    """
    Finds a point inside the polygon that is on land (elevation > 0).
    Preference order:
      1. Centroid (if on land)
      2. Representative point (if on land)
      3. First point in the exterior ring that is on land
      4. Fallback to representative point (even if in sea)
    """
    try:
        # 1. Try Centroid
        c = poly.centroid
        if dem_manager:
            elev, _ = dem_manager.query(c.x, c.y)
            if elev is not None and elev > 0:
                return c.x, c.y
        else:
            return c.x, c.y

        # 2. Try Representative Point (guaranteed to be inside)
        rp = poly.representative_point()
        if dem_manager:
            elev, _ = dem_manager.query(rp.x, rp.y)
            if elev is not None and elev > 0:
                return rp.x, rp.y
        else:
            return rp.x, rp.y

        # 3. Try to find any point inside that is on land (sampling)
        # For simplicity, if standard points fail, we return rp as best effort
        return rp.x, rp.y

    except Exception as e:
        print(f"  [DEBUG] Error finding land point: {e}")
        # absolute fallback
        try: return poly.centroid.x, poly.centroid.y
        except: return 0.0, 0.0

def shp_to_geojson(shp_path: str, simplify: bool = True, max_pts: int = 400) -> Optional[dict]:
    """Menggunakan Geopandas untuk konversi Shapefile ke GeoJSON dengan dukungan CRS & PolygonZ."""
    try:
        gdf = gpd.read_file(shp_path)
        if gdf is None or (hasattr(gdf, "empty") and gdf.empty):
            return None

        #    Transformasi CRS ke WGS84 (derajat)   
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
            
        #           # -- Force 2D geometry to prevent Leaflet PolygonZ rendering bugs --
        import shapely.wkb
        try:
            has_z = False
            # check if any geometry has Z
            if hasattr(gdf.geometry, 'has_z'):
                if gdf.geometry.has_z.any():
                    has_z = True
            
            if has_z:
                gdf.geometry = gdf.geometry.apply(
                    lambda geom: shapely.wkb.loads(shapely.wkb.dumps(geom, output_dimension=2)) if geom else geom
                )
        except Exception as ze:
            pass
        
        #    Ambil Bounding Box   
        bbox = list(gdf.total_bounds)
        bbox = [round(x, 5) for x in bbox]

        #    Pastikan format GeoJSON standar   
        return {
            "features": json.loads(gdf.to_json())['features'],
            "bbox": bbox
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def detect_layer_style(filename: str, geom_type: str) -> dict:
    fn = filename.lower(); stem = Path(filename).stem

    #    Label manusiawi berdasarkan stem                          
    LABEL_MAP = {
        "administrasi_desa":    "Administrasi Desa",
        "garis_pantai_selatan": "Garis Pantai Selatan",
        "jalan_bantul":         "Jalan Bantul",
        "koordinat_tes":        "Koordinat TES",
        "tes_bantul":           "TES Bantul",
        "2016_java-faultmodel_v1_2":         "Sesar Jawa (PUSGEN)",
        "ina_megathrust":                    "Megathrust Indonesia",
        "2016-nmaluccufaults-latlong_v1_2":  "Sesar N.Maluku",
        "2016_kalimantanfaultmod_v1_2":      "Sesar Kalimantan",
        "2016_nt-banda-fault_v1.2_simplified": "Sesar NT-Banda",
        "2016_sulawesifaultmod_v1_2":        "Sesar Sulawesi",
        "2016_sum_faultmodel_v1_2":          "Sesar Sumatera",
    }
    label = LABEL_MAP.get(stem.lower(), stem)

    #    Administrasi wilayah                                       
    if any(k in fn for k in ['kecamatan', 'kec_']):
        return {"color":"#ff9900","weight":1.5,"fillOpacity":0.08,"fillColor":"#ff9900","label":"Kecamatan","order":2}
    if any(k in fn for k in ['administrasi_desa','desa','kelurahan','kel_']):
        return {"color":"#ffdd55","weight":1,"fillOpacity":0.07,"fillColor":"#ffdd55","label":"Administrasi Desa","order":3}
    if any(k in fn for k in ['kabupaten','kab_']):
        return {"color":"#ff6600","weight":2.5,"fillOpacity":0.07,"fillColor":"#ff6600","label":"Kabupaten","order":1}
    if any(k in fn for k in ['provinsi','prov_']):
        return {"color":"#ff3300","weight":3,"fillOpacity":0.05,"fillColor":"#ff3300","label":"Provinsi","order":0}

    #    Hidrologi & infrastruktur                                  
    if any(k in fn for k in ['sungai','river','hidrologi']):
        return {"color":"#33aaff","weight":1.5,"fillOpacity":0,"fillColor":"transparent","label":"Sungai","order":10}
    if any(k in fn for k in ['jalan','road']):
        return {"color":"#ffe566","weight":1,"fillOpacity":0,"fillColor":"transparent","label":label,"order":11}

    #    Garis pantai                                               
    if any(k in fn for k in ['pantai','coast','garis_pantai']):
        return {"color":"#00d4ff","weight":2,"fillOpacity":0,"fillColor":"transparent","label":label,"order":5}

    #    TES / Titik Evakuasi                                       
    if any(k in fn for k in ['tes_','tes_bantul','koordinat_tes','evakuasi']):
        return {"color":"#00ff88","weight":2,"fillOpacity":0.9,"fillColor":"#00ff88","label":label,"order":4}

    #    Sesar / Fault (PUSGEN)                                     
    if any(k in fn for k in ['sesar','fault','pusgen','megathrust','java-fault',
                              'sumatera','sulawesi','kalimantan','maluku','nt-banda']):
        return {"color":"#ff4444","weight":1.5,"fillOpacity":0,"fillColor":"transparent","label":label,"order":6}

    #    Default berdasarkan geometri                               
    defaults = {
        "Polygon":         {"color":"#cc88ff","weight":1.5,"fillOpacity":0.08,"fillColor":"#cc88ff","label":label,"order":99},
        "MultiPolygon":    {"color":"#cc88ff","weight":1.5,"fillOpacity":0.08,"fillColor":"#cc88ff","label":label,"order":99},
        "LineString":      {"color":"#aaaacc","weight":1.5,"fillOpacity":0,"fillColor":"transparent","label":label,"order":99},
        "MultiLineString": {"color":"#aaaacc","weight":1.5,"fillOpacity":0,"fillColor":"transparent","label":label,"order":99},
        "Point":           {"color":"#ffffff","weight":1,"fillOpacity":0.85,"fillColor":"#ffffff","label":label,"order":99},
    }
    return defaults.get(geom_type, defaults["Polygon"])


VEKTOR_DIR: Optional[str] = None

#                                                                
# ROAD GEOJSON CACHE
# Shapefile jalan dikonversi ke GeoJSON saat startup, disimpan di
# memory. Endpoint /network/roads langsung serve dari cache ini  
# tidak perlu baca ulang shapefile per-request.
#
# Struktur cache:
#   ROAD_GEOJSON_CACHE = {
#     "geojson":       <FeatureCollection dict>,   # GeoJSON lengkap
#     "roads":         <list of road dicts>,        # siap pakai build_graph
#     "source_file":   "Jalan_Bantul.shp",
#     "feature_count": 1234,
#     "bbox":          [lon_min, lat_min, lon_max, lat_max],
#   }
#   ROAD_GRAPH_CACHE = build_graph result (dibangun setelah DEM siap)
#                                                                
ROAD_GEOJSON_CACHE: Optional[dict] = None
ROAD_GRAPH_CACHE:  Optional[dict]  = None

#    Cache Administrasi Desa                                    
# Struktur: {"geojson": <FC>, "desa": [...], "source_file": str, "count": int}
DESA_CACHE: Optional[dict] = None

#    Cache TES (Titik Evakuasi Sementara)                      
# Struktur: {"geojson": <FC>, "tes": [...], "source_file": str, "count": int}
TES_CACHE: Optional[dict] = None


def _build_road_cache(vektor_dir: str) -> Optional[dict]:
    """
    Scan vektor_dir untuk shapefile jalan, konversi ke GeoJSON,
    lalu parse ke list road dicts siap pakai.
    Dipanggil sekali saat startup.
    """
    ROAD_KEYWORDS = [
        'jalan', 'road', 'street', 'way', 'jaringan',
        'transport', 'line', 'ruas', 'jalur', 'ln_',
    ]
    SPEED_MAP = {
        'primary': 60, 'secondary': 50, 'tertiary': 40,
        'residential': 30, 'unclassified': 25, 'service': 20,
        'track': 15, 'path': 8, 'footway': 5,
    }

    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            fn_lower = fn.lower()
            if not fn_lower.endswith('.shp'):
                continue
            if not any(k in fn_lower for k in ROAD_KEYWORDS):
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n   Konversi shapefile jalan ke GeoJSON cache: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=False, max_pts=50000)
                if not gj or not gj.get('features'):
                    print(f"    {fn}: kosong, skip")
                    continue

                # Filter hanya LineString / MultiLineString
                line_feats = [
                    f for f in gj['features']
                    if f.get('geometry', {}).get('type', '') in
                       ('LineString', 'MultiLineString')
                ]
                if not line_feats:
                    print(f"    {fn}: tidak ada geometri LineString, skip")
                    continue
                gj['features'] = line_feats

                # Parse ke road dicts
                roads = []
                for feat in line_feats:
                    props = feat.get('properties', {}) or {}
                    geom  = feat.get('geometry', {})
                    if not geom:
                        continue

                    # Deteksi field highway / tipe jalan
                    hw = (props.get('highway') or props.get('HIGHWAY') or
                          props.get('jenis')   or props.get('JENIS')   or
                          props.get('REMARK')  or props.get('fclass')  or
                          props.get('type')    or 'residential')
                    hw = str(hw).lower().strip()

                    # Nama jalan
                    name = (props.get('name') or props.get('NAMA') or
                            props.get('nama')  or props.get('NAME') or '')

                    # Oneway
                    oneway = str(props.get('oneway', 'no')).lower() in ('yes', '1', 'true')

                    # Kumpulkan koordinat (GeoJSON: [lon, lat]   ubah ke [lat, lon])
                    coords = []
                    if geom['type'] == 'LineString':
                        coords = [[c[1], c[0]] for c in geom['coordinates']]
                    elif geom['type'] == 'MultiLineString':
                        for seg in geom['coordinates']:
                            coords += [[c[1], c[0]] for c in seg]

                    if len(coords) < 2:
                        continue

                    roads.append({
                        'id':        props.get('osm_id') or props.get('ID') or id(feat),
                        'highway':   hw,
                        'name':      name,
                        'oneway':    'yes' if oneway else 'no',
                        'speed_kmh': SPEED_MAP.get(hw, 25),
                        'capacity':  int(props.get('lanes', 1) or 1) * 1000,
                        'coords':    coords,
                    })

                if not roads:
                    print(f"    {fn}: tidak ada road valid setelah parsing")
                    continue

                # Hitung bbox dari semua koordinat
                all_lons = [c[1] for r in roads for c in r['coords']]
                all_lats = [c[0] for r in roads for c in r['coords']]
                bbox = [
                    round(min(all_lons), 5), round(min(all_lats), 5),
                    round(max(all_lons), 5), round(max(all_lats), 5),
                ]

                print(f"    {fn}: {len(line_feats)} fitur   {len(roads)} road dicts")
                print(f"     bbox: {bbox}")

                return {
                    "geojson":       gj,
                    "roads":         roads,
                    "source_file":   fn,
                    "feature_count": len(roads),
                    "bbox":          bbox,
                }

            except Exception as e:
                import traceback
                print(f"    Error konversi {fn}: {e}")
                traceback.print_exc()

    print("    Tidak ada shapefile jalan ditemukan di folder Vektor")
    return None


def _build_desa_cache(vektor_dir: str) -> Optional[dict]:
    """
    Scan vektor_dir untuk shapefile administrasi desa,
    konversi ke GeoJSON + parse ke list desa dicts.
    Dipanggil sekali saat startup.
    """
    DESA_KEYWORDS = ['admin', 'desa', 'kelurahan', 'kecamatan', 'adm', 'penduduk']

    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            if not fn.lower().endswith('.shp'):
                continue
            
            is_match = any(k in fn.lower() for k in DESA_KEYWORDS)
            if not is_match:
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n   Konversi shapefile desa ke cache: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=True, max_pts=800)
                if not gj or not gj.get('features'):
                    print(f"    {fn}: kosong, skip")
                    continue

                desa_list = []
                for feat in gj['features']:
                    props = feat.get('properties', {}) or {}
                    geom  = feat.get('geometry', {})

                    # Nama desa
                    name = ""
                    for fld in ['NAMOBJ', 'namobj', 'WADMKD', 'wadmkd', 'DESA', 'desa', 'KELURAHAN', 'kelurahan', 'KALURAHAN',
                                'NAMA', 'nama', 'name', 'NAME']:
                        v = props.get(fld)
                        if v and str(v).strip() not in ('', 'None'):
                            name = str(v).strip(); break
                    if not name:
                        name = f"Desa_{len(desa_list)+1}"

                    # Populasi
                    pop = 0
                    for fld in ["Penduduk", "PENDUDUK", "Jumlah_Pen", "JUMLAH_PEN", "Population", "POPULATION", 'jumlah', 'JUMLAH',
                                'pop', 'POP', 'jiwa', 'JIWA', 'total', 'TOTAL']:
                        try:
                            v = props.get(fld)
                            if v and str(v).strip() not in ('', 'None'):
                                pop = int(float(str(v).replace(',', '.')))
                                if pop > 0: break
                        except: pass

                    # Centroid / Label Location
                    lat_c = lon_c = None
                    try:
                        g = geom
                        if HAS_SHAPELY:
                            from shapely.geometry import shape as _sh
                            s_geom = _sh(g)
                            lon_c, lat_c = get_valid_land_point(s_geom, dem_manager)
                        else:
                            # Fallback lama tanpa shapely
                            if g['type'] == 'Polygon':
                                cs = g['coordinates'][0]
                                lon_c = sum(c[0] for c in cs) / len(cs)
                                lat_c = sum(c[1] for c in cs) / len(cs)
                            elif g['type'] == 'MultiPolygon':
                                cs = g['coordinates'][0][0]
                                lon_c = sum(c[0] for c in cs) / len(cs)
                                lat_c = sum(c[1] for c in cs) / len(cs)
                    except Exception as e:
                        pass

                    desa_list.append({
                        "name":     name,
                        "penduduk": pop or 0,
                        "lat":      round(lat_c, 6) if lat_c else None,
                        "lon":      round(lon_c, 6) if lon_c else None,
                        "geom":     geom,
                        "props":    {k: v for k, v in list(props.items())[:15]},
                        "source":   fn,
                    })

                if not desa_list:
                    print(f"    {fn}: tidak ada fitur valid")
                    continue

                print(f"    {fn}: {len(desa_list)} desa di-cache")
                return {
                    "geojson":     gj,
                    "desa":        desa_list,
                    "source_file": fn,
                    "count":       len(desa_list),
                }

            except Exception as e:
                print(f"    Error konversi desa {fn}: {e}")

    print("    Tidak ada shapefile administrasi desa ditemukan")
    return None


def _build_tes_cache(vektor_dir: str) -> Optional[dict]:
    """
    Scan vektor_dir untuk shapefile TES,
    konversi ke GeoJSON + parse ke list TES dicts.
    Dipanggil sekali saat startup.
    """
    TES_KEYWORDS = ['tes', 'evakuasi', 'shelter', 'pengungsian', 'koordinat_tes']

    for root, _, files in os.walk(vektor_dir):
        for fn in sorted(files):
            if not fn.lower().endswith('.shp'):
                continue
            if not any(k in fn.lower() for k in TES_KEYWORDS):
                continue

            shp_path = os.path.join(root, fn)
            print(f"\n   Konversi shapefile TES ke cache: {fn}")
            try:
                gj = shp_to_geojson(shp_path, simplify=False)
                if not gj or not gj.get('features'):
                    print(f"    {fn}: kosong, skip")
                    continue

                tes_list = []
                for feat in gj['features']:
                    props = feat.get('properties', {}) or {}
                    geom  = feat.get('geometry', {})

                    # Nama TES
                    name = ""
                    for fld in ['NAMA','nama','NAME','name','TES','tes',
                                'LOKASI','lokasi','TEMPAT','tempat']:
                        v = props.get(fld)
                        if v and str(v).strip() not in ('', 'None'):
                            name = str(v).strip(); break
                    if not name:
                        name = f"TES-{len(tes_list)+1:02d}"

                    # Kapasitas
                    kapasitas = 500
                    for fld in ['KAPASITAS','kapasitas','CAP','cap','CAPACITY']:
                        try:
                            v = props.get(fld)
                            if v: kapasitas = int(float(str(v))); break
                        except: pass

                    # Koordinat
                    lat_c = lon_c = None
                    try:
                        if geom['type'] == 'Point':
                            lon_c, lat_c = geom['coordinates']
                        elif geom['type'] == 'Polygon':
                            cs = geom['coordinates'][0]
                            lon_c = sum(c[0] for c in cs) / len(cs)
                            lat_c = sum(c[1] for c in cs) / len(cs)
                        elif geom['type'] == 'MultiPoint':
                            lon_c, lat_c = geom['coordinates'][0]
                    except: pass

                    tes_list.append({
                        "name":      name,
                        "kapasitas": kapasitas,
                        "lat":       round(lat_c, 6) if lat_c else None,
                        "lon":       round(lon_c, 6) if lon_c else None,
                        "props":     {k: v for k, v in list(props.items())[:10]},
                    })

                if not tes_list:
                    print(f"    {fn}: tidak ada TES valid")
                    continue

                print(f"    {fn}: {len(tes_list)} TES di-cache")
                return {
                    "geojson":     gj,
                    "tes":         tes_list,
                    "source_file": fn,
                    "count":       len(tes_list),
                }

            except Exception as e:
                print(f"    Error konversi TES {fn}: {e}")

    print("    Tidak ada shapefile TES ditemukan")
    return None



app     = FastAPI(title="TsunamiSim Bathymetry Server v3")
manager:     Optional[BATNASTileManager] = None
c_mask:      Optional[CoastlineMask]     = None
dem_manager: Optional[DEMManager]        = None

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET","POST"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)

#    Serve static files (Icons, Logos, etc.)                   
app.mount("/asset", StaticFiles(directory=os.path.join(BASE_DIR, "asset")), name="asset")

_precompute_status = "running"

#    SWE Solver import (opsional   aktif jika swe_solver.py ada di folder yang sama)   
import importlib.util as _ilu, os as _os
_SWE_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "swe_solver.py")
if _os.path.exists(_SWE_PATH):
    _spec = _ilu.spec_from_file_location("swe_solver", _SWE_PATH)
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    TsunamiSimulator = _mod.TsunamiSimulator
    SWE_AVAILABLE = True
    print("  swe_solver.py dimuat   endpoint /simulate aktif")
else:
    TsunamiSimulator = None
    SWE_AVAILABLE = False
    print("  swe_solver.py tidak ditemukan   endpoint /simulate tidak aktif")


@app.get("/")
async def read_root():
    """Serve the WebGIS frontend (index.html)."""
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/index.html")
async def read_index():
    """Serve the WebGIS frontend (index.html)."""
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/status")
async def status():
    cov = manager.coverage_bbox() if manager else None
    ms  = manager.stats() if manager else {}
    return {
        "server":   "TsunamiSim Bathymetry Server v3",
        "masking": {
            "layer1_threshold": "val < -0.5 m",
            "layer2_sanity":    "-7500 < val < -0.5 m",
            "layer3_coastline": c_mask is not None,
            "coastline_bbox":   list(c_mask.bbox) if c_mask and c_mask.bbox else None,
        },
        "precompute_status": _precompute_status,
        "batnas": {
            "tiles_loaded":  len(manager.tiles) if manager else 0,
            "coverage":      cov,
            "reader":        "rasterio" if USE_RASTERIO else "manual",
            "stats":         ms,
        },
        "gebco": {
            "enabled":       True,
            "cache_entries": len(GEBCO_CACHE),
            "mode":          "httpx_online" if USE_HTTPX else "synthetic",
        },
        "dem": {
            "tiles_loaded":  len(dem_manager.tiles) if dem_manager else 0,
            "coverage":      dem_manager.coverage_bbox() if dem_manager else None,
        },
        "vektor": {
            "dir":           VEKTOR_DIR,
            "active":        bool(VEKTOR_DIR and os.path.isdir(VEKTOR_DIR)),
        }
    }


@app.get("/tiles/info")
async def tiles_info():
    return {"tiles": manager.tile_info() if manager else []}




@app.get("/dem/info")
async def dem_info():
    """Info DEM tiles yang dimuat (DEMNAS, dll.)"""
    return {
        "tiles":    dem_manager.tile_info() if dem_manager else [],
        "coverage": dem_manager.coverage_bbox() if dem_manager else None,
        "count":    len(dem_manager.tiles) if dem_manager else 0,
    }


@app.get("/dem/elevation")
async def get_elevation(
    lat: float = Query(..., description="Lintang"),
    lon: float = Query(..., description="Bujur"),
):
    """
    Elevasi daratan dari DEMNAS pada koordinat (lat, lon).
    Dipakai WebGIS untuk membedakan daratan vs laut dan hitung zona inundasi.
    """
    if dem_manager:
        elev, src = dem_manager.query(lon, lat)
        if elev is not None:
            return {"lat": lat, "lon": lon, "elevation_m": round(elev, 2),
                    "source": src, "type": "land" if elev >= 0 else "below_sea"}
    # Fallback: coba BATNAS (kedalaman laut)
    depth = None; dsrc = "unknown"
    if manager:
        d, s = manager.query(lon, lat)
        if d: depth, dsrc = d, s
    if depth is None:
        depth, dsrc = await gebco_depth(lon, lat)
    return {"lat": lat, "lon": lon, "elevation_m": round(-depth, 2),
            "source": dsrc, "type": "ocean"}

@app.get("/layers")
async def list_layers():
    """
    Daftar semua shapefile di folder Vektor beserta metadata & style.
    Response: [{id, filename, label, geom_type, feature_count, style, bbox}]
    """
    if not VEKTOR_DIR or not os.path.isdir(VEKTOR_DIR):
        return {"layers": [], "error": "Folder Vektor belum dikonfigurasi. Tambahkan --vektor ke server."}

    result = []
    for root, _, files in os.walk(VEKTOR_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith('.shp'): continue
            shp_path = os.path.join(root, fn)
            try:
                res = shp_to_geojson(shp_path, simplify=True)
                if not res or not res['features']: continue

                gj = res
                geom_type = gj['features'][0]['geometry']['type']
                style     = detect_layer_style(fn, geom_type)

                bbox = res['bbox']

                # Key properties (first 5 non-empty fields)
                sample_props = {}
                if gj['features'][0]['properties']:
                    for k,v in gj['features'][0]['properties'].items():
                        if v and (isinstance(v, str) and v.strip() or not isinstance(v, str)): sample_props[k]=v
                        if len(sample_props)>=5: break

                result.append({
                    "id":            Path(fn).stem,
                    "filename":      fn,
                    "path":          shp_path,
                    "label":         style["label"],
                    "geom_type":     geom_type,
                    "feature_count": len(gj['features']),
                    "style":         style,
                    "bbox":          bbox,
                    "sample_props":  sample_props,
                })
                print(f"    {fn:50s} {geom_type:18s} {len(gj['features']):4d} fitur")

            except Exception as e:
                print(f"    {fn}: {e}")

    result.sort(key=lambda x: x['style'].get('order', 99))
    return {"layers": result, "vektor_dir": VEKTOR_DIR}


@app.get("/layers/{layer_id}")
async def get_layer(layer_id: str,
                    simplify: bool = Query(True),
                    max_pts: int   = Query(400, ge=50, le=2000)):
    """
    GeoJSON untuk layer tertentu (berdasarkan stem nama file).
    Contoh: GET /layers/Batas_Kecamatan_Bantul
    """
    if not VEKTOR_DIR or not os.path.isdir(VEKTOR_DIR):
        return {"error": "Folder Vektor belum dikonfigurasi"}

    shp_path = None
    for root, _, files in os.walk(VEKTOR_DIR):
        for fn in files:
            if fn.lower().endswith('.shp') and Path(fn).stem.lower() == layer_id.lower():
                shp_path = os.path.join(root, fn)
                break
        if shp_path: break

    if not shp_path:
        return {"error": f"Layer '{layer_id}' tidak ditemukan di {VEKTOR_DIR}"}

    gj = shp_to_geojson(shp_path, simplify=simplify, max_pts=max_pts)
    if not gj:
        return {"error": f"Gagal membaca {shp_path}"}

    geom_type = gj['features'][0]['geometry']['type'] if gj['features'] else 'Unknown'
    style     = detect_layer_style(os.path.basename(shp_path), geom_type)

    return {
        "type":          "FeatureCollection",
        "features":      gj['features'],
        "metadata": {
            "id":            layer_id,
            "label":         style["label"],
            "geom_type":     geom_type,
            "feature_count": len(gj['features']),
            "style":         style,
        }
    }



@app.get("/mask/test")
async def mask_test(
    lat: float = Query(...),
    lon: float = Query(...)
):
    """
    Debug endpoint: lihat bagaimana setiap layer masking memutuskan
    untuk koordinat tertentu.
    """
    result = {"lat": lat, "lon": lon, "layers": {}}

    # Raw BATNAS value
    raw_val = None
    if manager:
        for tile in manager.tiles:
            if tile.contains(lon, lat):
                raw_val = tile.read_value(lon, lat)
                result["batnas_raw"] = {"value": raw_val, "tile": Path(tile.path).name}
                break

    if raw_val is not None:
        # Layer 1
        l1 = raw_val < -0.5
        result["layers"]["layer1_threshold"] = {
            "pass": l1,
            "reason": f"val={raw_val:.2f} {'<' if l1 else '>='} -0.5"
        }
        # Layer 2
        l2 = -7500 < raw_val < -0.5
        result["layers"]["layer2_sanity"] = {
            "pass": l2,
            "reason": f"-7500 < {raw_val:.0f} < -0.5   {'OK' if l2 else 'FAIL'}"
        }
        # Layer 3
        if c_mask:
            dbg = c_mask.debug_info(lon, lat)
            result["layers"]["layer3_coastline"] = dbg
        else:
            result["layers"]["layer3_coastline"] = {"pass": None, "reason": "coastline mask not loaded"}

    # Final decision
    depth, src = None, "unknown"
    if manager:
        d, s = manager.query(lon, lat)
        depth, src = d, s
    if depth is None:
        depth_val, src = await gebco_depth(lon, lat)
        depth = depth_val

    result["final"] = {
        "depth_m": round(depth, 2) if depth else None,
        "source":  src,
        "wave_speed_kmh": round(math.sqrt(9.81*depth)*3.6, 2) if depth else None
    }
    return result


@app.get("/depth")
async def get_depth(
    lat: float = Query(...),
    lon: float = Query(...),
    source: str = Query("auto")
):
    depth = None; src = "unknown"; is_land = False; elevation = None

    # 0. Prioritas 1: Cek DEMNAS/DEM untuk daratan
    if dem_manager and source in ("auto", "dem"):
        elev, esrc = dem_manager.query(lon, lat)
        if elev is not None and elev >= 0:
            return {
                "lat": lat, "lon": lon,
                "depth_m":        0.0,
                "elevation_m":    round(elev, 2),
                "source":         esrc,
                "is_land":        True,
                "wave_speed_kmh": 0.0
            }

    # 1. Prioritas 2: BATNAS (Kedalaman Laut)
    if manager and source in ("auto", "batnas"):
        d, s = manager.query(lon, lat)
        if d is not None:
            depth, src = d, s
        else:
            # Jika BATNAS deteksi darat (val > -0.5), coba ambil nilai aslinya untuk info
            for tile in manager.tiles:
                if tile.contains(lon, lat):
                    v = tile.read_value(lon, lat)
                    if v is not None and v >= -0.5:
                        # Ini daratan menurut BATNAS
                        elevation = v
                        src = f"batnas({Path(tile.path).stem})"
                        is_land = True
                        break

    if is_land:
        return {
            "lat": lat, "lon": lon,
            "depth_m": 0.0, "elevation_m": round(elevation, 2),
            "source": src, "is_land": True, "wave_speed_kmh": 0.0
        }

    # 2. Fallback: GEBCO
    if depth is None and source in ("auto", "gebco"):
        d, s = await gebco_depth(lon, lat)
        depth, src = d, s

    # Final result
    depth = max(depth or 0.0, 0.0)
    return {
        "lat": lat, "lon": lon,
        "depth_m":        round(depth, 2),
        "elevation_m":    round(-depth, 2),
        "source":         src,
        "is_land":        depth <= 0,
        "wave_speed_kmh": round(math.sqrt(9.81*max(0.1, depth))*3.6, 2)
    }


@app.get("/depth/grid")
async def get_depth_grid(
    lon_min: float=Query(106.0), lat_min: float=Query(-12.0),
    lon_max: float=Query(116.0), lat_max: float=Query(-6.0),
    cols: int=Query(60,ge=5,le=300), rows: int=Query(40,ge=5,le=200)
):
    dlon=(lon_max-lon_min)/(cols-1); dlat=(lat_max-lat_min)/(rows-1)
    grid=[]; b_hits=0; g_hits=0

    for j in range(rows):
        lat=lat_min+j*dlat
        for i in range(cols):
            lon=lon_min+i*dlon
            depth=None; src="d"

            if manager:
                d,s=manager.query(lon,lat)
                if d: depth,src=d,"b"; b_hits+=1

            if depth is None:
                d,s=await gebco_depth(lon,lat)
                if d: depth,src=d,"g"; g_hits+=1

            grid.append([round(lon,4),round(lat,4),round(depth or 4000,1)])

    return {"cols":cols,"rows":rows,
            "bbox":[lon_min,lat_min,lon_max,lat_max],
            "batnas_hits":b_hits,"gebco_hits":g_hits,"points":grid}


@app.get("/depth/path")
async def get_depth_path(
    from_lat:float=Query(...), from_lon:float=Query(...),
    to_lat:float=Query(...),   to_lon:float=Query(...),
    steps:int=Query(20,ge=5,le=100)
):
    R=6371.0; profile=[]; total_dist=0; total_time=0
    prev_lat,prev_lon=from_lat,from_lon

    for i in range(steps+1):
        t=i/steps; lat=from_lat+t*(to_lat-from_lat); lon=from_lon+t*(to_lon-from_lon)
        res=await get_depth(lat=lat,lon=lon)
        depth=res["depth_m"]; speed=res["wave_speed_kmh"]

        dlat=(lat-prev_lat)*math.pi/180; dlon=(lon-prev_lon)*math.pi/180
        a=(math.sin(dlat/2)**2+math.cos(prev_lat*math.pi/180)*
           math.cos(lat*math.pi/180)*math.sin(dlon/2)**2)
        seg=2*R*math.asin(math.sqrt(max(0,a)))
        if i>0: total_dist+=seg; total_time+=seg/speed if speed>0 else 0

        profile.append({"lat":round(lat,5),"lon":round(lon,5),"depth_m":depth,
                        "wave_speed_kmh":speed,"source":res["source"],
                        "dist_from_start_km":round(total_dist,2),
                        "travel_time_min":round(total_time*60,1)})
        prev_lat,prev_lon=lat,lon

    return {"total_dist_km":round(total_dist,2),
            "total_time_min":round(total_time*60,1), "profile":profile}


#                                                                
# SIMULATE   SWE Numerical Solver
#                                                                
from fastapi import HTTPException
from pydantic import BaseModel

#    Global bathy & roughness grid cache                       
_bathy_grid_cache = None
_roughness_grid_cache = None

@app.on_event("startup")
async def startup_event():
    """Luncurkan pre-compute di thread terpisah agar server uvicorn segera responsif."""
    import functools
    async def _run_precompute():
        global _bathy_grid_cache, _roughness_grid_cache, _precompute_status, ROAD_GEOJSON_CACHE, ROAD_GRAPH_CACHE, DESA_CACHE, TES_CACHE, evac_solver
        print("\n  [Startup] Memulai optimasi background processing (Threaded)...")
        loop = asyncio.get_event_loop()
        try:
            # 1. Bathymetry Grid (CPU-bound task in separate thread)
            sim = TsunamiSimulator()
            print("  [Startup] Membangun bathy grid (Threaded)...")
            _bathy_grid_cache = await loop.run_in_executor(None, _build_bathy_grid_sync, sim)
            
            # --- NEW: Manning's Roughness Grid via OSM ---
            try:
                print("  [Startup] Mengambil data landuse OSM untuk Manning's n...")
                # Bbox Bantul (approx)
                bbox_str = f"{sim.domain['lat_min']},{sim.domain['lon_min']},{sim.domain['lat_max']},{sim.domain['lon_max']}"
                osm_landuse = await fetch_osm_landuse(bbox_str)
                if "error" not in osm_landuse:
                    ny, nx = _bathy_grid_cache.shape
                    lat_arr = np.arange(sim.domain["lat_min"], sim.domain["lat_max"], sim.domain["dx_deg"])
                    lon_arr = np.arange(sim.domain["lon_min"], sim.domain["lon_max"], sim.domain["dx_deg"])
                    _roughness_grid_cache = await loop.run_in_executor(None, 
                        _build_roughness_grid_sync, ny, nx, lat_arr, lon_arr, osm_landuse)
                else:
                    print(f"    Gagal ambil OSM landuse: {osm_landuse['error']}")
            except Exception as e:
                print(f"    Error in landuse precompute: {e}")

            # DESA & TES (Small and critical for UI boundaries/icons)
            if VEKTOR_DIR:
                DESA_CACHE = await loop.run_in_executor(None, _build_desa_cache, VEKTOR_DIR)
                TES_CACHE = await loop.run_in_executor(None, _build_tes_cache, VEKTOR_DIR)
            else:
                print("  ! [Startup] VEKTOR_DIR tidak diset. Desa & TES cache dilewati.")

            # Road Graph & Cache (Large and slow - moved after small layers)
            if VEKTOR_DIR:
                print("  [Startup] Membangun Road cache (Threaded, ini mungkin memakan waktu)...")
                ROAD_GEOJSON_CACHE = await loop.run_in_executor(None, _build_road_cache, VEKTOR_DIR)
                if ROAD_GEOJSON_CACHE:
                    ROAD_GRAPH_CACHE = await loop.run_in_executor(None, functools.partial(build_graph, ROAD_GEOJSON_CACHE['roads'], dem_mgr=dem_manager))
            
            # 3. Evacuation ABM Solver
            if EVAC_AVAILABLE and VEKTOR_DIR:
                evac_solver = EvacuationABMSolver(vektor_dir=VEKTOR_DIR, dem_mgr=dem_manager)
                await loop.run_in_executor(None, evac_solver.build_caches)
                print("  [Startup] EvacuationABMSolver siap")

        except Exception as e:
            _precompute_status = f"failed: {str(e)}"
            import traceback
            traceback.print_exc()
            print(f"  [Startup] Background pre-compute gagal: {e}")
        else:
            _precompute_status = "ready"
            print("  [Startup] Seluruh background pre-compute (Threaded) selesai.")

    # Fire and forget immediately
    asyncio.create_task(_run_precompute())

def _build_bathy_grid_sync(sim):
    """Pre-compute bathymetry grid using in-memory BATNAS + DEM + GEBCO data.
    
    Strategy: load tile bands into RAM once, then do fast array lookups.
    """
    import time as _time
    t0 = _time.time()
    dx_deg = sim.domain["dx_deg"]
    lat_arr = np.arange(sim.domain["lat_min"], sim.domain["lat_max"], dx_deg)
    lon_arr = np.arange(sim.domain["lon_min"], sim.domain["lon_max"], dx_deg)
    ny, nx = len(lat_arr), len(lon_arr)
    grid = np.full((ny, nx), np.nan)  # NaN = belum terisi
    
    print(f"  Grid target: {ny}x{nx} = {ny*nx} sel")
    
    from rasterio.windows import from_bounds
    from rasterio.enums import Resampling

    # Area simulasi: lon_min, lat_min, lon_max, lat_max
    w_lon_min, w_lat_min = sim.domain["lon_min"], sim.domain["lat_min"]
    w_lon_max, w_lat_max = sim.domain["lon_max"], sim.domain["lat_max"]
    b_hits, d_hits, g_hits = 0, 0, 0
    
    print(f"  Target Bounds: lon={w_lon_min}..{w_lon_max}, lat={w_lat_min}..{w_lat_max}")

    def fill_from_dataset(ds_list, negate=False):
        hits = 0
        for ds, bounds in ds_list:
            inter_left, inter_right = max(w_lon_min, bounds.left), min(w_lon_max, bounds.right)
            inter_bottom, inter_top = max(w_lat_min, bounds.bottom), min(w_lat_max, bounds.top)
            if inter_left < inter_right and inter_bottom < inter_top:
                try:
                    window = from_bounds(inter_left, inter_bottom, inter_right, inter_top, ds.transform)
                    j_min, j_max = int((inter_bottom - w_lat_min) / dx_deg), int((inter_top - w_lat_min) / dx_deg)
                    i_min, i_max = int((inter_left - w_lon_min) / dx_deg), int((inter_right - w_lon_min) / dx_deg)
                    # Clamp to grid bounds
                    r_start, r_end = max(0, j_min), min(ny, j_max)
                    c_start, c_end = max(0, i_min), min(nx, i_max)
                    target_h, target_w = (r_end - r_start), (c_end - c_start)
                    if target_h > 0 and target_w > 0:
                        # Adjust chunk offsets if j_min/i_min were negative
                        ch_r0 = r_start - j_min
                        ch_c0 = c_start - i_min
                        full_h, full_w = (j_max - j_min), (i_max - i_min)
                        if full_h > 0 and full_w > 0:
                            chunk = ds.read(1, window=window, out_shape=(full_h, full_w), resampling=Resampling.nearest)
                            sub = chunk[ch_r0:ch_r0+target_h, ch_c0:ch_c0+target_w].astype(np.float64)
                            # Vectorized: fill only NaN cells with valid values
                            target_slice = grid[r_start:r_end, c_start:c_end]
                            mask = np.isnan(target_slice) & (np.abs(sub) < 8000)
                            if negate:
                                target_slice[mask] = -sub[mask]
                            else:
                                target_slice[mask] = sub[mask]
                            hits += int(np.sum(mask))
                except Exception: pass
        return hits

    #    LANGKAH 1: FILL FROM DEM FIRST (Priority for land)   
    dem_list = []
    if dem_manager and USE_RASTERIO:
        for t in dem_manager.tiles:
            if hasattr(t, 'ds'):
                dem_list.append((t.ds, t.ds.bounds))
    d_hits = fill_from_dataset(dem_list, negate=True)

    #    LANGKAH 2: FILL FROM BATNAS (Priority for ocean)   
    batnas_list = []
    if manager and USE_RASTERIO:
        for t in manager.tiles:
            if hasattr(t, 'ds'):
                batnas_list.append((t.ds, t.ds.bounds))
    
    def fill_batnas_ocean_only(ds_list):
        hits = 0
        for ds, bounds in ds_list:
            inter_left, inter_right = max(w_lon_min, bounds.left), min(w_lon_max, bounds.right)
            inter_bottom, inter_top = max(w_lat_min, bounds.bottom), min(w_lat_max, bounds.top)
            if inter_left < inter_right and inter_bottom < inter_top:
                try:
                    window = from_bounds(inter_left, inter_bottom, inter_right, inter_top, ds.transform)
                    j_min, j_max = int((inter_bottom - w_lat_min) / dx_deg), int((inter_top - w_lat_min) / dx_deg)
                    i_min, i_max = int((inter_left - w_lon_min) / dx_deg), int((inter_right - w_lon_min) / dx_deg)
                    # Clamp to grid bounds
                    r_start, r_end = max(0, j_min), min(ny, j_max)
                    c_start, c_end = max(0, i_min), min(nx, i_max)
                    target_h, target_w = (r_end - r_start), (c_end - c_start)
                    if target_h > 0 and target_w > 0:
                        ch_r0 = r_start - j_min
                        ch_c0 = c_start - i_min
                        full_h, full_w = (j_max - j_min), (i_max - i_min)
                        if full_h > 0 and full_w > 0:
                            chunk = ds.read(1, window=window, out_shape=(full_h, full_w), resampling=Resampling.nearest)
                            sub = chunk[ch_r0:ch_r0+target_h, ch_c0:ch_c0+target_w].astype(np.float64)
                            target_slice = grid[r_start:r_end, c_start:c_end]
                            # Vectorized ocean validation: value < -0.5 and > -7500
                            is_nan = np.isnan(target_slice)
                            is_ocean = (sub < -0.5) & (sub > -7500)
                            mask = is_nan & is_ocean
                            target_slice[mask] = np.abs(sub[mask])
                            hits += int(np.sum(mask))
                except Exception: pass
        return hits

    b_hits = fill_batnas_ocean_only(batnas_list)
    print(f"    DEM filled (land): {d_hits}, BATNAS filled (ocean): {b_hits}")
    
    #    LANGKAH 3: FILL REMAINING FROM GEBCO fallback              
    if np.isnan(grid).any():
        for j in range(ny):
            lat = lat_arr[j]
            for i in range(nx):
                if not np.isnan(grid[j, i]): continue
                lon = lon_arr[i]
                val = None
                if _gebco_band is not None:
                    try:
                        r, c = _gebco_ds.ds.index(lon, lat)
                        if 0 <= r < _gebco_band.shape[0] and 0 <= c < _gebco_band.shape[1]:
                            val = float(_gebco_band[r, c])
                    except: pass
                elif _gebco_ds is not None:
                    try: val = _gebco_ds.read_value(lon, lat)
                    except: pass
                
                if val is not None:
                    grid[j, i] = -val
                    g_hits += 1
    
    print(f"    GEBCO filled (fallback): {g_hits} sel ({g_hits*100//(ny*nx)}%)")
    
    #    LANGKAH 4: Interpolasi NaN via nearest-neighbor (TIDAK boleh sintetis)          
    still_nan = np.isnan(grid).sum()
    if still_nan > 0:
        total = ny * nx
        pct = still_nan * 100 // total
        print(f"      {still_nan}/{total} sel ({pct}%) masih NaN   interpolasi nearest-neighbor")
        try:
            from scipy.ndimage import distance_transform_edt
            mask = np.isnan(grid)
            if np.any(~mask):  # Ada setidaknya 1 sel valid
                ind = distance_transform_edt(mask, return_distances=False, return_indices=True)
                grid = grid[tuple(ind)]
                print(f"      Interpolasi selesai, NaN tersisa: {np.isnan(grid).sum()}")
            else:
                print(f"      SEMUA sel NaN   tidak bisa interpolasi. Cek path data GEBCO/BATNAS/DEM.")
        except ImportError:
            print(f"      scipy tidak tersedia   NaN diisi dengan kedalaman default 100m")
            grid[np.isnan(grid)] = 100.0  # deep ocean default
    
    elapsed = _time.time() - t0
    print(f"    Grid {ny}x{nx} siap dalam {elapsed:.1f}s (BATNAS: {b_hits}, DEM: {d_hits}, GEBCO: {g_hits})")
    return grid

# Alias untuk kompatibilitas jika ada bagian lain yang memanggil
async def _build_bathy_grid(sim):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _build_bathy_grid_sync, sim)


class SimRequest(BaseModel):
    lat: float
    lon: float
    mw: float
    fault_type: str = "vertical"
    depth_km: float = 20.0
    duration_min: float = 60.0
    is_megathrust: bool = False

@app.post("/simulate")
async def simulate(req: SimRequest):
    if not SWE_AVAILABLE:
        raise HTTPException(status_code=503,
            detail="swe_solver.py tidak ditemukan di folder server.")
    if not (5.0 <= req.mw <= 9.5):
        raise HTTPException(status_code=422, detail="Mw harus antara 5.0 dan 9.5")

    import asyncio, functools
    loop = asyncio.get_event_loop()
    try:
        sim = TsunamiSimulator()
        
        # --- USE PRE-COMPUTED BATHYMETRY GRID ---
        global _bathy_grid_cache
        bathy_grid = _bathy_grid_cache
        if bathy_grid is None:
            print("    Bathy grid belum di-precompute, membangun on-the-fly...")
            bathy_grid = await _build_bathy_grid(sim)
            _bathy_grid_cache = bathy_grid
        
        ny, nx = bathy_grid.shape
        print(f"    Menggunakan pre-computed bathy grid {ny}x{nx}")

        #    Bantul Admin Masking (Bantul Only)            
        admin_mask = None
        if HAS_SHAPELY and DESA_CACHE and DESA_CACHE.get("desa"):
            try:
                # Union semua polygon desa di cache untuk jadi batas Bantul
                polygons = []
                for d in DESA_CACHE["desa"]:
                    if d.get("geom"):
                        polygons.append(shape(d["geom"]))
                if polygons:
                    admin_mask = unary_union(polygons)
                    print(f"    Masker Bantul gabungan berhasil dibangun dari {len(polygons)} desa.")
            except Exception as e:
                print(f"    Gagal membangun masker Bantul: {e}")

        run_fn = functools.partial(
            sim.run,
            epicenter_lat = req.lat,
            epicenter_lon = req.lon,
            mw            = req.mw,
            fault_type    = req.fault_type,
            depth_km      = req.depth_km,
            duration_min  = req.duration_min,
            save_frames   = 15,
            is_megathrust = req.is_megathrust,
            dem_manager   = dem_manager,
            bathy_grid    = bathy_grid,
            roughness_grid= _roughness_grid_cache,
            admin_mask    = admin_mask,
        )
        result = await loop.run_in_executor(None, run_fn)
        # Batasi wave_frames agar JSON tidak terlalu besar (max 30 frame)
        frames = result.get("wave_frames", [])
        if len(frames) > 30:
            step = max(1, len(frames) // 30)
            frames = frames[::step][:30]
        result["wave_frames"] = frames

        # --- NEW: Per-Village Impact Statistics (Integration for Dashboard) ---
        if DESA_CACHE and "desa" in DESA_CACHE:
            print("    Menghitung rincian statistik dampak per desa...")
            village_impacts = []
            runup_m = result.get("statistics", {}).get("runup_bantul_m", 0)
            
            try:
                from shapely.geometry import Point as _Pt, shape as _Sh
                from shapely.prepared import prep as _Prep
                
                # Ekstrak sel-sel yang benar-benar tersapu tsunami dari hasil SWE
                inundation_geojson = result.get("inundation_geojson", {})
                flooded_points = []
                if "features" in inundation_geojson:
                    for f in inundation_geojson["features"]:
                        geom_pt = f.get("geometry", {})
                        if geom_pt.get("type") == "Point":
                            coords = geom_pt.get("coordinates")
                            depth = f.get("properties", {}).get("flood_depth", 0)
                            if depth > 0:
                                flooded_points.append({"lon": coords[0], "lat": coords[1], "depth": depth})
                
                # Setup meta untuk grid (digunakan untuk fallback estimasi rasio dampak total sel)
                gm = result.get("grid_meta", {})
                # Cari dx dari metadata hi-res inundation jika ada, jika tidak fallback ke domain solver dx
                dx = inundation_geojson.get("metadata", {}).get("fill_dx_deg", gm.get("dx_deg", 0.005))
                # Luas 1 sel dalam satuan derajat murni
                cell_area_deg = dx * dx
                
                for d in DESA_CACHE["desa"]:
                    name = d.get("name", "Unknown")
                    pop = d.get("penduduk", 0)
                    geom = d.get("geom")
                    if not geom: continue
                    
                    poly = _Sh(geom)
                    prep_poly = _Prep(poly)
                    bbox = poly.bounds # (minx, miny, maxx, maxy)
                    
                    flooded_cells = 0
                    max_d = 0.0
                    depth_sum = 0.0
                    
                    # Cek hanya sel yang sudah pasti terendam air berdasarkan perhitungan fisik SWE
                    for pt in flooded_points:
                        if bbox[0] <= pt["lon"] <= bbox[2] and bbox[1] <= pt["lat"] <= bbox[3]:
                            if prep_poly.contains(_Pt(pt["lon"], pt["lat"])):
                                flooded_cells += 1
                                max_d = max(max_d, pt["depth"])
                                depth_sum += pt["depth"]
                    
                    # Evaluasi Rasio Dampak
                    # Menghitung perkiraan matematis murni berapa 'maksimal sel' yang bisa ditampung area desa
                    # Metode ini menghindari perhitungan naive 'batas koordinat' yang membuat desa di luar layar seakan tidak punya sel
                    poly_area_deg = poly.area
                    # PENGALAMAN: dx sering kali under-estimate jumlah sel sebenarnya karena interpolasi SWE
                    # Mari kita gunakan densitas poin empiris jika cell_area terlalu kasar
                    estimated_cells = max(1, int(poly_area_deg / cell_area_deg))
                    
                    # Kita menambahkan penalti 55% untuk mengurangi over-estimasi sel yang tumpang tindih dari SWE
                    # karena distribusi populasi umumnya berpusat di jalan/tengah, bukan tersebar rata di pinggiran pesisir
                    impact_ratio = min(1.0, (flooded_cells * 0.45) / estimated_cells)
                    
                    impacted_pop = int(pop * impact_ratio)
                    avg_depth = round(depth_sum / max(1, flooded_cells), 2)
                    
                    # FIX: Gunakan AVG DEPTH untuk klasifikasi desa, BUKAN MAX DEPTH.
                    # Jika menggunakan max_depth, satu titik 10m di tepi pantai otomatis membuat SELURUH DESA menjadi EKSTREM.
                    risk_level = "Aman"
                    if impacted_pop > 0:
                        if avg_depth > 3.0: risk_level = "EKSTREM"
                        elif avg_depth > 1.5: risk_level = "TINGGI"
                        elif avg_depth > 0.5: risk_level = "SEDANG"
                        else: risk_level = "RENDAH"
                    
                    village_impacts.append({
                        "desa": name,
                        "total": pop,
                        "terdampak": impacted_pop,
                        "max_depth": round(max_d, 2),
                        "avg_depth": avg_depth,
                        "zona": risk_level,
                        "lat": d.get("lat"),
                        "lon": d.get("lon")
                    })
                
                # --- NEW: Clip inundation_geojson to village boundaries (User request) ---
                if "features" in inundation_geojson:
                    # Pre-prepare village polygons for fast intersection
                    village_polys = []
                    for dv in DESA_CACHE["desa"]:
                        if dv.get("geom"):
                            village_polys.append(_Prep(_Sh(dv["geom"])))
                    
                    if village_polys:
                        clipped_features = []
                        for f in inundation_geojson["features"]:
                            pt_geom = f.get("geometry", {})
                            if pt_geom.get("type") == "Point":
                                lonv, latv = pt_geom.get("coordinates")
                                pv = _Pt(lonv, latv)
                                # Check if point is inside ANY village poly
                                if any(vp.contains(pv) for vp in village_polys):
                                    clipped_features.append(f)
                        
                        inundation_geojson["features"] = clipped_features
                        print(f"    Inundation clipped: {len(clipped_features)} features remaining.")

                result["village_impacts"] = village_impacts
                print(f"    Rincian dampak untuk {len(village_impacts)} desa ditambahkan ke response.")
            except Exception as ev:
                print(f"    Gagal hitung village impacts: {ev}")

        # Simpan hasil SWE untuk integrasi ABM otomatis
        global swe_last_result
        swe_last_result = result
        if evac_solver:
            evac_solver.set_swe_results(result)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Simulasi gagal: {str(e)}")



#                                                                
# ENTRY POINT
#                                                                
def main():
    global manager, c_mask

    # Default path setting for local data
    base_script_dir = os.path.dirname(os.path.abspath(__file__))
    # Cari folder "Data" naik ke atas hingga 5 level
    curr_dir = base_script_dir
    default_data_dir = None
    for _ in range(5):
        cand = os.path.join(curr_dir, "Data")
        if os.path.isdir(cand):
            default_data_dir = cand
            break
        parent = os.path.dirname(curr_dir)
        if parent == curr_dir: break
        curr_dir = parent
    
    if not default_data_dir:
        # Fallback jika tidak ditemukan (asumsi awal)
        default_data_dir = os.path.join(os.path.dirname(base_script_dir), "Data")

    default_raster   = os.path.join(default_data_dir, "Raster")
    default_dem      = os.path.join(default_raster, "DEMNAS")
    default_vektor   = os.path.join(default_data_dir, "Vektor")

    parser = argparse.ArgumentParser(description="TsunamiSim Bathymetry + DEM Server v3")
    parser.add_argument("--batnas",     type=str, default=None,
                        help="Direktori tile BATNAS (.tif)   bisa subfolder dari --raster")
    parser.add_argument("--dem",        type=str, default=default_dem,
                        help="Direktori DEMNAS/DEM (.tif)   bisa subfolder dari --raster")
    parser.add_argument("--raster",     type=str, default=default_raster,
                        help="Direktori induk Raster (akan auto-scan subfolder BATNAS & DEMNAS/DEM)")
    parser.add_argument("--coastline",  type=str, default=default_vektor,
                        help="Path/folder shapefile garis pantai (.shp)   Layer 3 masking")
    parser.add_argument("--vektor",     type=str, default=default_vektor,
                        help="Direktori shapefile vektor (admin, sesar, dll.) untuk layer peta")
    parser.add_argument("--port",       type=int, default=8000)
    parser.add_argument("--host",       type=str, default="127.0.0.1")
    args = parser.parse_args()

    # Auto-resolve --raster ke subfolder BATNAS dan DEMNAS
    if args.raster and os.path.isdir(args.raster):
        raster_root = os.path.abspath(args.raster)
        if not args.batnas:
            for cand in ['BATNAS', 'batnas', 'Batnas']:
                p = os.path.join(raster_root, cand)
                if os.path.isdir(p):
                    args.batnas = p
                    print(f"    Auto-detect BATNAS: {p}")
                    break
            if not args.batnas:
                args.batnas = raster_root  # fallback: scan semua .tif di root raster
        if not args.dem:
            for cand in ['DEMNAS', 'demnas', 'DEM', 'dem', 'Demnas']:
                p = os.path.join(raster_root, cand)
                if os.path.isdir(p):
                    args.dem = p
                    print(f"    Auto-detect DEM: {p}")
                    break

    global VEKTOR_DIR
    VEKTOR_DIR = os.path.abspath(args.vektor) if args.vektor else None
    if VEKTOR_DIR and not os.path.isdir(VEKTOR_DIR):
        print(f"  Folder Vektor tidak ditemukan: {VEKTOR_DIR}")
        VEKTOR_DIR = None
    elif VEKTOR_DIR:
        print(f"  Folder Vektor: {VEKTOR_DIR}")

    # Load coastline mask (Layer 3)
    # Mendukung: path ke .shp langsung, ATAU path ke folder (auto-scan semua .shp)
    c_mask = None
    if args.coastline:
        target = args.coastline
        shp_files = []
        if os.path.isfile(target) and target.lower().endswith('.shp'):
            shp_files = [target]
        elif os.path.isdir(target):
            for root, _, files in os.walk(target):
                for fn in files:
                    if fn.lower().endswith('.shp'):
                        shp_files.append(os.path.join(root, fn))
            print(f"  Scan folder Vektor: {len(shp_files)} shapefile ditemukan")
            for p in shp_files: print(f"     {os.path.basename(p)}")
        if shp_files:
            c_mask = CoastlineMask(shp_files[0])
            for extra_shp in shp_files[1:]:
                extra = CoastlineMask(extra_shp)
                c_mask.segments.extend(extra.segments)
            if len(shp_files) > 1:
                c_mask._profile = {}
                for seg in c_mask.segments:
                    for lon, lat in seg:
                        key = round(round(lon / c_mask.BIN_SIZE) * c_mask.BIN_SIZE, 6)
                        c_mask._profile.setdefault(key, []).append(lat)
                all_lons = [p[0] for sg in c_mask.segments for p in sg]
                all_lats = [p[1] for sg in c_mask.segments for p in sg]
                c_mask.bbox = (min(all_lons), min(all_lats), max(all_lons), max(all_lats))
                print(f"    Gabungan {len(shp_files)} shapefile   bbox {[round(x,4) for x in c_mask.bbox]}")
        else:
            print(f"    Tidak ada .shp ditemukan di: {target}   Layer 3 dinonaktifkan")
    else:
        print("    --coastline tidak diberikan   hanya Layer 1+2 aktif")

    # Load BATNAS tiles
    global manager, dem_manager
    batnas_dir = os.path.abspath(args.batnas) if args.batnas else None
    
    # PATCH: Fallback otomatis jika path yang diberikan tidak ada
    if batnas_dir and not os.path.isdir(batnas_dir):
        print(f"  Direktori BATNAS tidak ditemukan: {batnas_dir}")
        # Coba folder default di D:\... jika tersedia
        alt_batnas = os.path.join(default_raster, "BATNAS")
        if os.path.isdir(alt_batnas):
            print(f"    Menggunakan fallback BATNAS: {alt_batnas}")
            batnas_dir = alt_batnas
        else:
            batnas_dir = None

    if not batnas_dir or not os.path.isdir(batnas_dir):
        if not batnas_dir:
            print("  --batnas tidak diberikan atau tidak ditemukan   BATNAS dinonaktifkan")
        manager = BATNASTileManager.__new__(BATNASTileManager)
        manager.tiles = []; manager._masked_land=0; manager._masked_value=0; manager._valid_hits=0
        manager.coast_mask = c_mask
    else:
        manager = BATNASTileManager(batnas_dir, coast_mask=c_mask)

    # Load DEM tiles (DEMNAS)
    dem_dir = os.path.abspath(args.dem) if args.dem else None
    if dem_dir and not os.path.isdir(dem_dir):
        print(f"  Direktori DEM tidak ditemukan: {dem_dir}")
        alt_dem = os.path.join(default_raster, "DEMNAS")
        if os.path.isdir(alt_dem):
            print(f"    Menggunakan fallback DEM: {alt_dem}")
            dem_dir = alt_dem
        else:
            dem_dir = None

    if dem_dir and os.path.isdir(dem_dir):
        dem_manager = DEMManager(dem_dir)
    else:
        if not dem_dir:
            print("    --dem tidak ditemukan atau tidak diberikan   elevasi daratan dari DEM tidak aktif")
        dem_manager = None

    #    PATCH: Proses ini dipindahkan ke @app.on_event("startup") agar tidak memblokir   
    # global ROAD_GEOJSON_CACHE, ROAD_GRAPH_CACHE, ...
    pass

    mask_status = []
    mask_status.append("  Layer 1: val < -0.5 m")
    mask_status.append("  Layer 2: sanity -7500 < val < -0.5 m")
    mask_status.append(f"{' ' if c_mask else ' '} Layer 3: coastline mask ({'aktif' if c_mask else 'tidak aktif   tambahkan --coastline'})")

    RASTER_PATH = r"D:\STUDY\S2 Geomatika UGM\MK\Semester 2\KomGeo\Pemodelan Tsunami\Data\Raster"
    VEKTOR_PATH = r"D:\STUDY\S2 Geomatika UGM\MK\Semester 2\KomGeo\Pemodelan Tsunami\Data\Vektor"

    print(f"""
                                                                    
       TsunamiSim Bathymetry + DEM Server v3                        
                                                                    
   URL    : http://{args.host}:{args.port}
   BATNAS : {len(manager.tiles)} tile(s) dimuat
   DEM    : {len(dem_manager.tiles) if dem_manager else 0} tile(s) dimuat
   Vektor : {VEKTOR_DIR or '(tidak aktif)'}
   GEBCO  : {'lokal (rasterio)' if _gebco_ds else ('online (httpx)' if USE_HTTPX else 'synthetic fallback')}
   Reader : {'rasterio' if USE_RASTERIO else 'manual parser'}
                                                                    
   MASKING DARATAN (3 Layer):
   {mask_status[0]}
   {mask_status[1]}
   {mask_status[2]}
                                                                    
   Endpoint utama:
     GET /status                status lengkap
     GET /health                health check cepat
     GET /depth?lat=&lon=       kedalaman laut (BATNAS/GEBCO)
     GET /dem/elevation?lat=&lon=   elevasi daratan (DEMNAS)
     GET /layers                daftar layer vektor
     GET /mask/test?lat=&lon=   debug masking
     POST /simulate             simulasi SWE numerik
                                                                    
    """)

    print(" " * 66)
    print("  CARA MENJALANKAN (path data Anda):")
    print()
    print(f'  python {Path(__file__).name} ^')
    print(f'    --raster "{RASTER_PATH}" ^')
    print(f'    --vektor "{VEKTOR_PATH}" ^')
    print(f'    --coastline "{VEKTOR_PATH}" ^')
    print(f'    --port 8000')
    print()
    print("  Atau pisah BATNAS dan DEM secara eksplisit:")
    print(f'  python {Path(__file__).name} ^')
    print(f'    --batnas "{RASTER_PATH}\\BATNAS" ^')
    print(f'    --dem    "{RASTER_PATH}\\DEMNAS" ^')
    print(f'    --vektor "{VEKTOR_PATH}" ^')
    print(f'    --coastline "{VEKTOR_PATH}" ^')
    print(f'    --port 8000')
    print(" " * 66)
    print()

    # PRE-COMPUTE dipindahkan ke @app.on_event("startup") agar async
    # print("\n  Pre-computing bathymetry grid (ini hanya dilakukan sekali saat startup)...")
    # _loop = _aio.new_event_loop()
    # _bathy_grid_cache = _loop.run_until_complete(_build_bathy_grid(TsunamiSimulator()))
    # _loop.close()
    # print(f"  Bathy grid {_bathy_grid_cache.shape} siap digunakan untuk simulasi.\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

#                                                                
# ENDPOINT TAMBAHAN: /health dan /swe/status
# Digunakan WebGIS untuk single-server health check
#                                                                

@app.get("/health")
async def health():
    ci = evac_solver.cache_info() if evac_solver else {}
    return {
        "ok":           True,
        "server":       "TsunamiSim v4",
        "batnas":       len(manager.tiles) if manager else 0,
        "dem":          len(dem_manager.tiles) if dem_manager else 0,
        "swe":          SWE_AVAILABLE,
        "evac":         EVAC_AVAILABLE,
        "vektor":       bool(VEKTOR_DIR and os.path.isdir(VEKTOR_DIR)),
        "road_cache":   ci.get("road", {}).get("cached", False),
        "road_count":   ci.get("road", {}).get("feature_count", 0),
        "road_source":  ci.get("road", {}).get("source_file", None),
        "graph_nodes":  ci.get("road", {}).get("graph_nodes", 0),
        "desa_cache":   ci.get("desa", {}).get("cached", False),
        "desa_count":   ci.get("desa", {}).get("count", 0),
        "desa_source":  ci.get("desa", {}).get("source_file", None),
        "tes_cache":    ci.get("tes", {}).get("cached", False),
        "tes_count":    ci.get("tes", {}).get("count", 0),
        "tes_source":   ci.get("tes", {}).get("source_file", None),
    }


@app.get("/admin/info")
async def admin_cache_info():
    """Status lengkap semua GeoJSON cache (jalan, desa, TES)."""
    ci = evac_solver.cache_info() if evac_solver else {}
    return {
        "road": ci.get("road", {"cached": False, "source_file": None, "feature_count": 0,
                                "graph_nodes": 0, "graph_edges": 0, "dem_integrated": False}),
        "desa": ci.get("desa", {"cached": False, "source_file": None, "count": 0}),
        "tes":  ci.get("tes",  {"cached": False, "source_file": None, "count": 0}),
    }


@app.get("/swe/status")
async def swe_status():
    """
    Status detail SWE solver   dipakai WebGIS untuk tampilkan/sembunyikan tombol simulasi numerik.
    """
    return {
        "available":  SWE_AVAILABLE,
        "module":     "swe_solver.py",
        "message":    "SWE numerik aktif   endpoint /simulate tersedia" if SWE_AVAILABLE
                      else "swe_solver.py tidak ditemukan   letakkan di folder yang sama dengan server_v3.py",
    }


@app.get("/evacuation/status")
async def evacuation_status():
    """Status modul evacuation_abm.py   dipakai WebGIS untuk badge status evakuasi."""
    ci = evac_solver.cache_info() if evac_solver else {}
    return {
        "available":      EVAC_AVAILABLE and evac_solver is not None,
        "module":         "evacuation_abm.py",
        "swe_integrated": swe_last_result is not None,
        "cache":          ci,
        "message":        "Evakuasi+ABM aktif" if (EVAC_AVAILABLE and evac_solver)
                          else "evacuation_abm.py tidak ditemukan   letakkan di folder server",
    }


@app.get("/network/roads/info")
async def road_cache_info():
    """
    Info status GeoJSON road cache.
    Dipakai frontend untuk menampilkan badge sumber data jalan.
    """
    if not ROAD_GEOJSON_CACHE:
        return {
            "cached":    False,
            "message":   "Road cache belum tersedia   server akan fallback ke Overpass API",
            "road_count": 0,
        }
    graph_nodes = len(ROAD_GRAPH_CACHE["nodes"]) if ROAD_GRAPH_CACHE else 0
    graph_edges = sum(len(v) for v in ROAD_GRAPH_CACHE["edges"].values()) if ROAD_GRAPH_CACHE else 0
    return {
        "cached":        True,
        "source_file":   ROAD_GEOJSON_CACHE["source_file"],
        "feature_count": ROAD_GEOJSON_CACHE["feature_count"],
        "bbox":          ROAD_GEOJSON_CACHE["bbox"],
        "graph_nodes":   graph_nodes,
        "graph_edges":   graph_edges,
        "dem_integrated": dem_manager is not None and len(dem_manager.tiles) > 0,
        "message":       (
            f"  Cache aktif: {ROAD_GEOJSON_CACHE['feature_count']} ruas jalan dari "
            f"'{ROAD_GEOJSON_CACHE['source_file']}' | "
            f"Graph: {graph_nodes} node, {graph_edges} edge"
        ),
    }


#                                                                
# ENDPOINT BARU v4: NETWORK ANALYSIS + ABM EVAKUASI
#                                                                
# Endpoint ini menyediakan data jalan lokal, administrasi desa,
# dan simulasi ABM evakuasi berbasis data penduduk per desa.
#                                                                

import heapq, random, time as _time
from typing import Dict, Any
import importlib.util as _ilu

#    Import modul evakuasi & ABM (evacuation_abm.py)           
_EVAC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evacuation_abm.py")
if os.path.exists(_EVAC_PATH):
    _spec = _ilu.spec_from_file_location("evacuation_abm", _EVAC_PATH)
    _mod_evac = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod_evac)
    EvacuationABMSolver = _mod_evac.EvacuationABMSolver
    EVAC_AVAILABLE      = True
    print("  evacuation_abm.py dimuat   endpoint /network/route dan /abm/simulate aktif")
else:
    EvacuationABMSolver = None
    EVAC_AVAILABLE      = False
    print("  evacuation_abm.py tidak ditemukan   letakkan di folder yang sama dengan server_v4.py")

# Variabel global solver evakuasi (diinisialisasi di main())
evac_solver = None
swe_last_result = None   # hasil SWE terakhir untuk integrasi ABM

#     Helper: Haversine distance (meter)                        
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.asin(math.sqrt(a))


#     Overpass / OSM road fetch (via Overpass API)              
async def fetch_osm_roads(bbox_str: str, road_type: str = "all") -> dict:
    """
    Ambil data jalan dari Overpass API untuk bbox tertentu.
    bbox_str: "min_lat,min_lon,max_lat,max_lon"
    """
    highway_filter = {
        "all":      '["highway"~"primary|secondary|tertiary|residential|unclassified|service|track|path|footway"]',
        "vehicle":  '["highway"~"primary|secondary|tertiary|residential|unclassified|service"]',
        "pedestrian": '["highway"~"residential|service|track|path|footway|unclassified"]',
    }.get(road_type, '["highway"~"primary|secondary|tertiary|residential|unclassified|service"]')

    query = f"""
[out:json][timeout:30];
(
  way{highway_filter}({bbox_str});
);
out body;
>;
out skel qt;
"""
    overpass_url = "https://overpass-api.de/api/interpreter"
    if not USE_HTTPX:
        return {"error": "httpx tidak tersedia   install: pip install httpx", "roads": []}

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(overpass_url, data=query)
            if resp.status_code != 200:
                return {"error": f"Overpass HTTP {resp.status_code}", "roads": []}
            data = resp.json()

        # Parse: nodes dict + ways
        nodes = {el["id"]: (el["lat"], el["lon"]) for el in data["elements"] if el["type"] == "node"}
        roads = []
        for el in data["elements"]:
            if el["type"] != "way": continue
            tags = el.get("tags", {})
            hw = tags.get("highway", "")
            coords = [nodes[nid] for nid in el.get("nodes", []) if nid in nodes]
            if len(coords) < 2: continue

            # Speed by road type (km/h)
            speed_map = {
                "primary": 60, "secondary": 50, "tertiary": 40,
                "residential": 30, "unclassified": 25, "service": 20,
                "track": 15, "path": 8, "footway": 5,
            }
            speed_kmh = speed_map.get(hw, 20)

            # Width/capacity
            lanes = int(tags.get("lanes", 1))
            capacity = lanes * 1000  # vehicles/hour estimate

            roads.append({
                "id":       el["id"],
                "highway":  hw,
                "name":     tags.get("name", ""),
                "oneway":   tags.get("oneway", "no"),
                "speed_kmh": speed_kmh,
                "capacity": capacity,
                "coords":   coords,  # list of [lat, lon]
            })

        return {"roads": roads, "node_count": len(nodes), "way_count": len(roads)}

    except Exception as e:
        return {"error": str(e), "roads": []}


#     Manning's n Mapping (Berdasarkan Penelitian Bantul 2024)    
OSM_MANNING_MAP = {
    # Permukiman / Lahan Terbangun
    "residential": 0.045,
    "commercial":  0.045,
    "industrial":  0.045,
    "retail":      0.045,
    "cemetery":    0.045,
    # Hutan
    "forest":      0.070,
    "wood":        0.070,
    # Belukar / Semak
    "scrub":       0.040,
    "heath":       0.040,
    # Kebun / Perkebunan
    "orchard":     0.035,
    "vineyard":    0.035,
    # Lahan Pertanian
    "farmland":    0.025,
    "farmyard":    0.025,
    "meadow":      0.025,
    "grass":       0.025,
    "village_green": 0.025,
    "allotments":  0.025,
    # Mangrove
    "wetland":     0.025,
    # Lahan Kosong / Terbuka
    "brownfield":  0.015,
    "sand":        0.015,
    "beach":       0.015,
    "bare_rock":   0.015,
    # Tambak / Empang
    "salt_pond":   0.010,
    "aquaculture": 0.010,
    # Badan Air
    "water":       0.007,
    "river":       0.007,
    "basin":       0.007,
    "reservoir":   0.007,
}

async def fetch_osm_landuse(bbox_str: str) -> dict:
    """
    Ambil data tutupan lahan (landuse/natural) dari Overpass API.
    """
    query = f"""
[out:json][timeout:30];
(
  way["landuse"]({bbox_str});
  relation["landuse"]({bbox_str});
  way["natural"]({bbox_str});
  relation["natural"]({bbox_str});
  way["leisure"]({bbox_str});
  relation["leisure"]({bbox_str});
);
out body;
>;
out skel qt;
"""
    overpass_url = "https://overpass-api.de/api/interpreter"
    if not USE_HTTPX:
        return {"error": "httpx tidak tersedia", "features": []}

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(overpass_url, data=query)
            if resp.status_code != 200:
                return {"error": f"Overpass HTTP {resp.status_code}", "features": []}
            data = resp.json()

        # Parse: nodes dict + ways/relations
        nodes = {el["id"]: (el["lat"], el["lon"]) for el in data["elements"] if el["type"] == "node"}
        features = []
        for el in data["elements"]:
            if el["type"] == "node": continue
            tags = el.get("tags", {})
            
            # Cari tag yang relevan untuk Manning's n
            landuse = tags.get("landuse") or tags.get("natural") or tags.get("leisure") or "default"
            n_value = OSM_MANNING_MAP.get(landuse, 0.035)  # default 0.035

            if el["type"] == "way":
                coords = [nodes[nid] for nid in el.get("nodes", []) if nid in nodes]
                if len(coords) < 3: continue
                features.append({
                    "type": "landuse",
                    "tag": landuse,
                    "n": n_value,
                    "coords": coords
                })
            # Simplified relation handling (only outer ways)
            elif el["type"] == "relation":
                for member in el.get("members", []):
                    if member["type"] == "way" and member["role"] == "outer":
                        # We don't have full relation parsing here for simplicity,
                        # but we can try to find the way if it was in the same response
                        pass # complicated for a single script

        return {"features": features, "count": len(features)}
    except Exception as e:
        return {"error": str(e), "features": []}


#     Build road graph from OSM ways (with optional DEM elevation)  
def build_graph(roads: list, dem_mgr=None) -> dict:
    """
    Bangun adjacency graph dari daftar jalan OSM.
    Jika dem_mgr tersedia, setiap edge mendapat bobot slope & elevation penalty.

    Node : idx dalam nodes_list   (lat, lon, elev_m)
    Edge : (neighbor_idx, dist_m, time_min, hw, capacity, composite_cost)
    composite_cost = w_dist*dist_km + w_time*time_min + w_elev*elev_pen + w_slope*slope_pen
      elev_pen  : ketinggian node ASAL (makin rendah = dekat laut = bahaya   penalty besar)
      slope_pen : slope persen (makin curam = lebih lambat)

    Dengan demikian Dijkstra yang meminimasi composite_cost akan MEMILIH rute yang:
      - Naik ke elevasi lebih tinggi (menjauhi zona banjir)
      - Kemiringan rendah (lebih cepat, aman untuk kendaraan)
    """
    #    bobot default (dapat diubah via parameter nanti)          
    W_DIST  = 0.30   # 30% bobot jarak
    W_TIME  = 0.30   # 30% bobot waktu
    W_ELEV  = 0.25   # 25% penalti ketinggian rendah (bahaya inundasi)
    W_SLOPE = 0.15   # 15% penalti kemiringan

    ELEV_DANGER_MAX = 20.0   # elevasi   0 m   max penalty;   20 m   no penalty
    SLOPE_MAX_PCT   = 40.0   # slope   40%   max penalty

    nodes_list = []   # (lat, lon, elev_m)
    nodes_idx  = {}   # (lat_r, lon_r) -> idx

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

    edges = {}

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

                #    Slope calculation                                  
                if dist > 0:
                    elev_diff = abs(celev - pelev)
                    slope_pct = (elev_diff / dist) * 100.0
                else:
                    slope_pct = 0.0

                #    Elevation penalty (asal   penalti jika rendah)    
                # Rute DARI zona rendah (bahaya) dapat penalti tinggi
                # sehingga Dijkstra memilih jalur naik ke tempat aman
                src_elev = pelev  # elevasi node asal
                elev_pen = max(0.0, 1.0 - src_elev / ELEV_DANGER_MAX)
                elev_pen = min(1.0, elev_pen)   # clamp [0,1]

                #    Slope penalty                                       
                slope_pen = min(1.0, slope_pct / SLOPE_MAX_PCT)

                #    Composite cost (normalized)                         
                dist_norm  = (dist / 1000) / 10.0  # asumsi max 10 km
                time_norm  = t_min / 60.0           # asumsi max 60 mnt
                composite  = (W_DIST  * dist_norm +
                              W_TIME  * time_norm  +
                              W_ELEV  * elev_pen   +
                              W_SLOPE * slope_pen)

                # Edge tuple: (neighbor, dist_m, time_min, hw, cap, composite, slope_pct, src_elev)
                edges.setdefault(prev_idx, []).append(
                    (idx, dist, t_min, hw, cap, composite, slope_pct, src_elev)
                )
                if not oneway:
                    # Balik arah: elevasi asal = celev (node tujuan sekarang)
                    dst_elev  = celev
                    elev_pen2 = max(0.0, min(1.0, 1.0 - dst_elev / ELEV_DANGER_MAX))
                    composite2 = (W_DIST  * dist_norm +
                                  W_TIME  * time_norm  +
                                  W_ELEV  * elev_pen2  +
                                  W_SLOPE * slope_pen)
                    edges.setdefault(idx, []).append(
                        (prev_idx, dist, t_min, hw, cap, composite2, slope_pct, dst_elev)
                    )

            prev_idx = idx

    return {"nodes": nodes_list, "edges": edges}


def _build_roughness_grid_sync(ny, nx, lat_arr, lon_arr, osm_data):
    """
    Rasterisasi poligon landuse dari OSM ke dalam grid Manning's n (ny x nx).
    Default n = 0.025 (Ocean/smooth).
    """
    grid = np.full((ny, nx), 0.025)
    if not osm_data or "features" not in osm_data:
        return grid

    try:
        from shapely.geometry import Point, Polygon
        from shapely.prepared import prep
        
        features = osm_data["features"]
        print(f"    Rasterizing {len(features)} landuse features into roughness grid...")
        
        # Urutkan berdasarkan n (yang lebih kasar menimpa yang lebih halus jika overlap)
        # atau sebaliknya tergantung logika. Biasanya pemukiman (n tinggi) menimpa sawah.
        sorted_features = sorted(features, key=lambda x: x["n"])

        for feat in sorted_features:
            coords = feat["coords"]
            n_val = feat["n"]
            poly = Polygon([(lon, lat) for lat, lon in coords])
            prepared_poly = prep(poly)
            
            # Optimization: only check cells within feature bbox
            b = poly.bounds # (minx, miny, maxx, maxy)
            
            # Find index ranges
            # index for lon: (lon - lon_min) / dx
            dx = lon_arr[1] - lon_arr[0] if len(lon_arr) > 1 else 0.001
            dy = lat_arr[1] - lat_arr[0] if len(lat_arr) > 1 else 0.001
            
            i_min = max(0, int((b[0] - lon_arr[0]) / dx))
            i_max = min(nx - 1, int((b[2] - lon_arr[0]) / dx) + 1)
            j_min = max(0, int((b[1] - lat_arr[0]) / dy))
            j_max = min(ny - 1, int((b[3] - lat_arr[0]) / dy) + 1)
            
            for j in range(j_min, j_max + 1):
                lat = lat_arr[j]
                for i in range(i_min, i_max + 1):
                    lon = lon_arr[i]
                    if prepared_poly.contains(Point(lon, lat)):
                        grid[j, i] = n_val
        
        print(f"    Roughness grid siap (n_mean={np.mean(grid):.4f})")
    except Exception as e:
        print(f"    Gagal rasterisasi landuse: {e}")
        
    return grid


#     Find nearest graph node to lat/lon                        
def nearest_node(nodes_list, lat, lon):
    """nodes_list bisa berisi (lat,lon) atau (lat,lon,elev)."""
    best_idx, best_d = 0, 1e18
    for i, node in enumerate(nodes_list):
        nlat, nlon = node[0], node[1]   # ambil 2 field pertama saja
        d = haversine_m(lat, lon, nlat, nlon)
        if d < best_d:
            best_d, best_idx = d, i
    return best_idx, best_d


#     Dijkstra                                                  
def dijkstra(graph, start_idx, end_idx, weight: str = "composite"):
    """
    weight:
      'time'        minimasi waktu tempuh
      'distance'    minimasi jarak (km)
      'composite'   minimasi composite cost (DEM + slope + waktu + jarak)
    Edge tuple: (neighbor, dist_m, time_min, hw, cap, composite, slope_pct, src_elev)
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    dist  = {start_idx: 0}
    prev  = {}
    pq    = [(0, start_idx)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18): continue
        if u == end_idx: break
        for edge in edges.get(u, []):
            v       = edge[0]
            dist_m  = edge[1]
            t_min   = edge[2]
            comp    = edge[5] if len(edge) > 5 else t_min / 60.0

            if weight == "time":
                w = t_min
            elif weight == "distance":
                w = dist_m / 1000
            else:                  # composite (default)
                w = comp

            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd; prev[v] = u
                heapq.heappush(pq, (nd, v))

    # Reconstruct path
    if end_idx not in prev and end_idx != start_idx:
        return None, []
    path = []
    cur = end_idx
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
    path.append(start_idx)
    path.reverse()
    return dist.get(end_idx, 1e18), [(nodes[i][0], nodes[i][1]) for i in path]


#     A*                                                        
def astar(graph, start_idx, end_idx, weight: str = "composite", transport_speed_kmh: float = 30):
    """
    A* dengan heuristik haversine. Mendukung bobot composite (DEM+slope).
    Edge tuple: (neighbor, dist_m, time_min, hw, cap, composite, slope_pct, src_elev)
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    elat, elon = nodes[end_idx][0], nodes[end_idx][1]

    def heuristic(idx):
        lat, lon = nodes[idx][0], nodes[idx][1]
        d = haversine_m(lat, lon, elat, elon)
        if weight == "time":
            return (d / 1000) / transport_speed_kmh * 60
        elif weight == "distance":
            return d / 1000
        else:  # composite: heuristik waktu dinormalisasi
            return (d / 1000) / transport_speed_kmh

    g = {start_idx: 0}
    prev = {}
    pq = [(heuristic(start_idx), 0, start_idx)]

    while pq:
        _, gn, u = heapq.heappop(pq)
        if u == end_idx: break
        if gn > g.get(u, 1e18): continue
        for edge in edges.get(u, []):
            v      = edge[0]
            dist_m = edge[1]
            t_min  = edge[2]
            comp   = edge[5] if len(edge) > 5 else t_min / 60.0

            if weight == "time":
                w = t_min
            elif weight == "distance":
                w = dist_m / 1000
            else:
                w = comp

            ng = gn + w
            if ng < g.get(v, 1e18):
                g[v] = ng; prev[v] = u
                heapq.heappush(pq, (ng + heuristic(v), ng, v))

    if end_idx not in prev and end_idx != start_idx:
        return None, []
    path = []
    cur = end_idx
    while cur in prev:
        path.append(cur); cur = prev[cur]
    path.append(start_idx); path.reverse()
    return g.get(end_idx, 1e18), [(nodes[i][0], nodes[i][1]) for i in path]


#     Road data endpoint                                        
@app.get("/network/roads")
async def get_roads(
    lat_min: float = Query(-8.15),
    lon_min: float = Query(110.20),
    lat_max: float = Query(-7.95),
    lon_max: float = Query(110.45),
    mode:    str   = Query("vehicle"),
):
    """
    Ambil data jalan untuk bbox tertentu.

    Prioritas sumber (dari terbaik ke fallback):
      1. ROAD_GEOJSON_CACHE    GeoJSON di-cache saat startup dari shapefile lokal
      2. Overpass API           fallback jika cache kosong
    """
    #    Prioritas 1: serve dari GeoJSON cache                  
    if ROAD_GEOJSON_CACHE:
        # Filter fitur dalam bbox jika perlu (opsional   kirim semua untuk graph lengkap)
        # Frontend sudah handle ini via buildRoadGraph
        return {
            "source":        "geojson_cache",
            "source_file":   ROAD_GEOJSON_CACHE["source_file"],
            "feature_count": ROAD_GEOJSON_CACHE["feature_count"],
            "bbox_cache":    ROAD_GEOJSON_CACHE["bbox"],
            "geojson":       ROAD_GEOJSON_CACHE["geojson"],
            "roads":         ROAD_GEOJSON_CACHE["roads"],
        }

    #    Prioritas 2: Overpass API fallback                     
    bbox_str = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    result   = await fetch_osm_roads(bbox_str, road_type=mode)
    result["source"] = "overpass_osm"
    result["bbox"]   = [lat_min, lon_min, lat_max, lon_max]
    return result


#     Route analysis endpoint                                   
@app.post("/network/route")
async def compute_route(body: Dict[str, Any]):
    """
    Hitung rute evakuasi dengan Dijkstra/A* + composite cost DEM+slope.
    Delegasi ke evac_solver (evacuation_abm.py) jika tersedia untuk integrasi SWE.
    """
    try:
        # --- DELEGASI KE EVAC_SOLVER (INTEGRASI SWE) ---
        if EVAC_AVAILABLE and evac_solver:
            print(f"    Delegating /network/route to evac_solver...")
            return evac_solver.compute_route(
                origin      = body.get("origin", {}),
                destination = body.get("destination", {}),
                method      = body.get("method", "network"),
                transport   = body.get("transport", "car"),
                weight      = body.get("weight", "composite"),
                roads       = body.get("roads", [])
            )

        # --- FALLBACK LOKAL (Jika evacuation_abm.py tidak ada) ---
        origin      = body.get("origin", {})
        destination = body.get("destination", {})
        method      = body.get("method", "network")
        transport   = body.get("transport", "car")
        weight      = body.get("weight", "composite")
        roads       = body.get("roads", [])

        # Speed by transport
        speeds = {"foot": 4, "motor": 30, "car": 50}
        speed_kmh = speeds.get(transport, 30)

        # Apply transport filter to roads
        filtered_roads = []
        for r in roads:
            hw = r.get("highway", "")
            if transport in ("motor", "car"):
                if hw in ("footway", "path", "steps"): continue
            adjusted = dict(r)
            adjusted["speed_kmh"] = min(r.get("speed_kmh", 20), speed_kmh)
            filtered_roads.append(adjusted)

        if not filtered_roads:
            return {"error": "Tidak ada data jalan. Pastikan roads terisi dari /network/roads"}

        # Build graph   gunakan ROAD_GRAPH_CACHE jika roads payload kosong/kecil
        if filtered_roads and len(filtered_roads) >= 50:
            graph = build_graph(filtered_roads, dem_mgr=dem_manager)
        elif ROAD_GRAPH_CACHE:
            graph = ROAD_GRAPH_CACHE   # sudah include DEM dari startup
            print("    /network/route: pakai ROAD_GRAPH_CACHE")
        else:
            graph = build_graph(filtered_roads, dem_mgr=dem_manager) if filtered_roads else None

        if not graph or not graph["nodes"]:
            return {"error": "Graph kosong   data jalan tidak valid"}

        nodes = graph["nodes"]
        olat, olon = origin.get("lat"), origin.get("lon")
        dlat, dlon = destination.get("lat"), destination.get("lon")

        start_idx, start_dist = nearest_node(nodes, olat, olon)
        end_idx,   end_dist   = nearest_node(nodes, dlat, dlon)

        #    Helper: compute elevation profile for a path           
        def get_elevation_profile(path_coords):
            profile = []
            for lat, lon in path_coords:
                if dem_manager:
                    e, _ = dem_manager.query(lon, lat)
                    profile.append(round(float(e), 1) if e is not None else 0.0)
                else:
                    profile.append(0.0)
            return profile

        def compute_slope_stats(path_coords):
            """Hitung slope rata-rata dan maks dari path."""
            slopes = []
            for i in range(len(path_coords) - 1):
                a, b = path_coords[i], path_coords[i+1]
                d = haversine_m(a[0], a[1], b[0], b[1])
                if d < 1: continue
                ea = 0.0; eb = 0.0
                if dem_manager:
                    v, _ = dem_manager.query(a[1], a[0]); ea = float(v) if v else 0.0
                    v, _ = dem_manager.query(b[1], b[0]); eb = float(v) if v else 0.0
                slopes.append(abs(eb - ea) / d * 100)
            return {
                "avg_slope_pct": round(sum(slopes) / len(slopes), 1) if slopes else 0.0,
                "max_slope_pct": round(max(slopes), 1)               if slopes else 0.0,
            }

        def path_metrics(path_coords):
            total_dist = sum(
                haversine_m(path_coords[i][0], path_coords[i][1],
                            path_coords[i+1][0], path_coords[i+1][1])
                for i in range(len(path_coords) - 1)
            )
            time_min = (total_dist / 1000) / speed_kmh * 60
            return total_dist, time_min

        #    Run algorithm(s)                                      
        routes_out = []

        def run_dijkstra(w, label, color, badge):
            cost, coords = dijkstra(graph, start_idx, end_idx, weight=w)
            if not coords: return
            dist_m, t_min = path_metrics(coords)
            elev_profile  = get_elevation_profile(coords)
            slope_stats   = compute_slope_stats(coords)
            min_elev      = min(elev_profile) if elev_profile else 0
            max_elev      = max(elev_profile) if elev_profile else 0
            routes_out.append({
                "label":          label,
                "method":         f"dijkstra_{w}",
                "color":          color,
                "badge":          badge,
                "path":           coords,
                "distance_m":     round(dist_m),
                "distance_km":    round(dist_m / 1000, 2),
                "time_min":       round(t_min, 1),
                "time_str":       (f"{int(t_min//60)}j {int(t_min%60)} mnt"
                                   if t_min >= 60 else f"{round(t_min)} mnt"),
                "node_count":     len(coords),
                "elevation_profile": elev_profile[::max(1, len(elev_profile)//50)],
                "min_elevation_m": round(min_elev, 1),
                "max_elevation_m": round(max_elev, 1),
                "elev_gain_m":    round(max_elev - min_elev, 1),
                **slope_stats,
            })

        def run_astar(w, label, color, badge):
            cost, coords = astar(graph, start_idx, end_idx, weight=w,
                                 transport_speed_kmh=speed_kmh)
            if not coords: return
            dist_m, t_min = path_metrics(coords)
            elev_profile  = get_elevation_profile(coords)
            slope_stats   = compute_slope_stats(coords)
            min_elev      = min(elev_profile) if elev_profile else 0
            max_elev      = max(elev_profile) if elev_profile else 0
            routes_out.append({
                "label":          label,
                "method":         f"astar_{w}",
                "color":          color,
                "badge":          badge,
                "path":           coords,
                "distance_m":     round(dist_m),
                "distance_km":    round(dist_m / 1000, 2),
                "time_min":       round(t_min, 1),
                "time_str":       (f"{int(t_min//60)}j {int(t_min%60)} mnt"
                                   if t_min >= 60 else f"{round(t_min)} mnt"),
                "node_count":     len(coords),
                "elevation_profile": elev_profile[::max(1, len(elev_profile)//50)],
                "min_elevation_m": round(min_elev, 1),
                "max_elevation_m": round(max_elev, 1),
                "elev_gain_m":    round(max_elev - min_elev, 1),
                **slope_stats,
            })

        if method == "network":
            # 3 rute alternatif: composite DEM, waktu minimum, jarak minimum
            run_dijkstra("composite", "Rute Optimal (DEM+Slope)", "#4ade80", "badge-opt")
            run_dijkstra("time",      "Rute Tercepat",            "#facc15", "badge-alt")
            run_astar("distance",     "Rute Terpendek (A*)",      "#60a5fa", "badge-bpbd")
        elif method == "astar":
            run_astar(weight, "A*   Rute Heuristik", "#60a5fa", "badge-bpbd")
        else:
            run_dijkstra(weight, "Dijkstra   Jalur Optimal", "#4ade80", "badge-opt")

        if not routes_out:
            return {"error": "Rute tidak ditemukan   origin/destination mungkin di luar jaringan jalan"}

        best = routes_out[0]
        return {
            "ok":            True,
            "method":        method,
            "transport":     transport,
            "weight":        weight,
            "routes":        routes_out,
            # Backward-compat fields (rute terbaik)
            "path":          best["path"],
            "distance_m":    best["distance_m"],
            "distance_km":   best["distance_km"],
            "time_min":      best["time_min"],
            "time_str":      best["time_str"],
            "node_count":    best["node_count"],
            "elevation_profile":  best["elevation_profile"],
            "min_elevation_m":    best["min_elevation_m"],
            "max_elevation_m":    best["max_elevation_m"],
            "elev_gain_m":        best["elev_gain_m"],
            "avg_slope_pct":      best["avg_slope_pct"],
            "snap_origin_dist_m": round(start_dist),
            "snap_dest_dist_m":   round(end_dist),
            "graph_nodes":        len(nodes),
            "dem_available":      dem_manager is not None and len(dem_manager.tiles) > 0,
        }

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


#     Admin desa data                                           
@app.get("/admin/desa")
async def get_admin_desa():
    """
    Ambil data administrasi desa.
    Prioritas: DESA_CACHE (GeoJSON, di-cache saat startup) → fallback data fiktif.
    """
    if DESA_CACHE:
        return {
            "source":      "geojson_cache",
            "source_file": DESA_CACHE["source_file"],
            "count":       DESA_CACHE["count"],
            "desa":        DESA_CACHE["desa"],
            "geojson":     DESA_CACHE["geojson"],
        }

    # Fallback: data fiktif Bantul
    FALLBACK_DESA = [
        {"name": "Srigading",    "penduduk": 4823, "lat": -8.033, "lon": 110.259},
        {"name": "Gadingharjo",  "penduduk": 3210, "lat": -8.026, "lon": 110.247},
        {"name": "Tirtosari",    "penduduk": 2890, "lat": -8.042, "lon": 110.236},
        {"name": "Poncosari",    "penduduk": 5640, "lat": -8.017, "lon": 110.271},
        {"name": "Parangtritis", "penduduk": 3120, "lat": -8.025, "lon": 110.332},
        {"name": "Donotirto",    "penduduk": 4200, "lat": -8.003, "lon": 110.263},
        {"name": "Trimurti",     "penduduk": 3670, "lat": -8.010, "lon": 110.252},
        {"name": "Sidomulyo",    "penduduk": 2950, "lat": -8.056, "lon": 110.219},
    ]
    return {"source": "fallback", "desa": FALLBACK_DESA}


#     TES (Titik Evakuasi Sementara) data                      
@app.get("/admin/tes")
async def get_tes():
    """
    Ambil data TES.
    Prioritas: TES_CACHE (GeoJSON, di-cache saat startup)   fallback kosong.
    """
    if TES_CACHE:
        return {
            "source":      "geojson_cache",
            "source_file": TES_CACHE["source_file"],
            "count":       TES_CACHE["count"],
            "tes":         TES_CACHE["tes"],
            "geojson":     TES_CACHE["geojson"],
        }

    return {"source": "none", "tes": []}


#     ABM Evakuasi endpoint                                     
@app.post("/abm/simulate")
async def run_abm(body: Dict[str, Any]):
    """
    Simulasi Agent-Based Model evakuasi tsunami.
    Delegasi ke evac_solver (evacuation_abm.py) jika tersedia untuk integrasi SWE.
    """
    try:
        # --- DELEGASI KE EVAC_SOLVER (INTEGRASI SWE) ---
        if EVAC_AVAILABLE and evac_solver:
            print(f"    Delegating /abm/simulate to evac_solver...")
            return evac_solver.run_abm(body)

        # --- FALLBACK LOKAL (Jika evacuation_abm.py tidak ada) ---
        desa_list    = body.get("desa_list", [])
        tes_list     = body.get("tes_list", [])
        roads        = body.get("roads", [])
        transport    = body.get("transport", "car")
        runup_m      = body.get("inundation_runup_m", 5.0)
        warning_min  = body.get("warning_time_min", 20)
        sim_dur      = body.get("sim_duration_min", 120)
        dt_min       = body.get("dt_min", 1)

        speeds = {"foot": 4, "motor": 30, "car": 50}
        speed_kmh = speeds.get(transport, 30)

        if not desa_list:
            return {"error": "desa_list kosong"}
        if not tes_list:
            # Buat TES default jika tidak ada
            tes_list = [{"name": "TES Default", "lat": -7.99, "lon": 110.28, "kapasitas": 99999}]

        # Build graph   prioritaskan ROAD_GRAPH_CACHE (sudah include DEM, lengkap)
        # PATCH FIX-4: jangan batasi hanya 500 ruas dari frontend
        graph = None
        if ROAD_GRAPH_CACHE:
            graph = ROAD_GRAPH_CACHE
            print(f"    ABM: pakai ROAD_GRAPH_CACHE ({len(ROAD_GRAPH_CACHE['nodes'])} node)")
        elif roads and len(roads) >= 50:
            graph = build_graph(roads, dem_mgr=dem_manager)
        else:
            # Coba muat langsung dari shapefile lokal (lebih lengkap)
            if VEKTOR_DIR:
                ROAD_KEYWORDS = ['jalan','road','street','way','jaringan',
                                 'transport','line','ruas','jalur','ln_']
                for root, _, files in os.walk(VEKTOR_DIR):
                    for fn in sorted(files):
                        if not fn.lower().endswith('.shp'): continue
                        if not any(k in fn.lower() for k in ROAD_KEYWORDS): continue
                        try:
                            gj = shp_to_geojson(os.path.join(root, fn),
                                                simplify=False, max_pts=10000)
                            if not gj or not gj.get('features'): continue
                            speed_map = {
                                'primary':60,'secondary':50,'tertiary':40,
                                'residential':30,'unclassified':25,'service':20,
                                'track':15,'path':8,'footway':5,
                            }
                            local_roads = []
                            for feat in gj['features']:
                                props = feat.get('properties', {}) or {}
                                geom  = feat.get('geometry', {})
                                if not geom: continue
                                hw = (props.get('highway') or props.get('HIGHWAY') or
                                      props.get('jenis')   or props.get('JENIS') or
                                      props.get('REMARK')  or 'residential')
                                hw = str(hw).lower().strip()
                                coords = []
                                if geom['type'] == 'LineString':
                                    coords = [[c[1], c[0]] for c in geom['coordinates']]
                                elif geom['type'] == 'MultiLineString':
                                    for seg in geom['coordinates']:
                                        coords += [[c[1], c[0]] for c in seg]
                                if len(coords) < 2: continue
                                local_roads.append({
                                    'highway':   hw,
                                    'speed_kmh': speed_map.get(hw, 25),
                                    'capacity':  1000,
                                    'oneway':    'no',
                                    'coords':    coords,
                                })
                            if local_roads:
                                graph = build_graph(local_roads, dem_mgr=dem_manager)
                                print(f"    ABM graph dari shapefile lokal: {len(local_roads)} ruas")
                                break
                        except Exception as e:
                            print(f"    ABM shapefile load error {fn}: {e}")
                    if graph: break

        #    Buat agen per desa                                
        # Setiap desa menghasilkan beberapa "agent grup" (cluster penduduk)
        agents = []
        for desa in desa_list:
            if not desa.get("lat") or not desa.get("lon"): continue
            pop = desa.get("penduduk", 1000)
            dlat, dlon = desa["lat"], desa["lon"]

            # Cek apakah desa terdampak inundasi (elevasi < runup + buffer 2m)
            # PATCH FIX-4b: is_affected default False jika DEM tersedia
            is_affected = True
            if dem_manager and dem_manager.tiles:
                elev, _ = dem_manager.query(dlon, dlat)
                if elev is not None:
                    # Desa di atas runup + 2m buffer = tidak terdampak
                    is_affected = (float(elev) <= runup_m + 2.0)
                # Jika DEM tidak cover koordinat ini, anggap terdampak (safe default)

            if not is_affected:
                continue

            # Cari TES terdekat
            best_tes, best_d = None, 1e18
            for tes in tes_list:
                if not tes.get("lat") or not tes.get("lon"): continue
                d = haversine_m(dlat, dlon, tes["lat"], tes["lon"])
                if d < best_d:
                    best_d, best_tes = d, tes

            if not best_tes: continue

            # Hitung rute ke TES terdekat
            route_path = None
            route_dist_m = best_d
            route_time_min = (best_d / 1000) / speed_kmh * 60

            if graph and graph.get("nodes"):
                # nodes[i] = (lat, lon, elev)   ambil hanya lat/lon untuk nearest_node
                nodes_latlon = [(n[0], n[1]) for n in graph["nodes"]]
                si, _ = nearest_node(nodes_latlon, dlat, dlon)
                ei, _ = nearest_node(nodes_latlon, best_tes["lat"], best_tes["lon"])
                # Gunakan composite (DEM+slope) sebagai default weight
                cost, path = dijkstra(graph, si, ei, weight="composite")
                if path:
                    route_path = path
                    dist_m = sum(haversine_m(path[i][0], path[i][1], path[i+1][0], path[i+1][1])
                                for i in range(len(path)-1))
                    route_dist_m = dist_m
                    route_time_min = (dist_m / 1000) / speed_kmh * 60

            # Bagi populasi jadi grup agen (maks 10 agen per desa)
            n_agents = min(10, max(1, pop // 500))
            pop_per_agent = pop // n_agents

            for ag_i in range(n_agents):
                # Scatter posisi awal sedikit acak (radius 200m)
                jitter_lat = dlat + random.gauss(0, 0.001)
                jitter_lon = dlon + random.gauss(0, 0.001)

                # Random delay response (0-15 menit setelah peringatan)
                response_delay = random.gauss(5, 3)  # menit
                response_delay = max(0, min(15, response_delay))

                agents.append({
                    "id":            f"{desa['name']}_{ag_i}",
                    "desa":          desa["name"],
                    "population":    pop_per_agent,
                    "start_lat":     jitter_lat,
                    "start_lon":     jitter_lon,
                    "target_tes":    best_tes["name"],
                    "target_lat":    best_tes["lat"],
                    "target_lon":    best_tes["lon"],
                    "route_path":    route_path or [[jitter_lat, jitter_lon], [best_tes["lat"], best_tes["lon"]]],
                    "route_dist_m":  route_dist_m,
                    "route_time_min": route_time_min,
                    "speed_kmh":     speed_kmh * random.uniform(0.7, 1.1),  # variasi individu
                    "depart_min":    warning_min + response_delay,
                    "arrive_min":    warning_min + response_delay + route_time_min,
                    "status":        "waiting",   # waiting|moving|arrived|stranded
                })

        if not agents:
            return {"error": "Tidak ada agen yang dibuat   cek data desa dan inundasi"}

        #    Simulasi time-step                                
        t_steps = list(range(0, sim_dur + 1, dt_min))
        timeline = []   # [{t, agents_moved, agents_arrived, agents_stranded, positions}]
        bottlenecks = {}  # road_segment -> congestion count

        arrived_total = 0
        arrived_by_desa = {}

        for t in t_steps:
            moved = 0; arrived = 0; stranded = 0
            positions = []

            for ag in agents:
                if t < ag["depart_min"]:
                    positions.append({"id": ag["id"], "lat": ag["start_lat"], "lon": ag["start_lon"],
                                     "status": "waiting", "pop": ag["population"]})
                    continue

                elapsed = t - ag["depart_min"]
                dist_covered_m = (ag["speed_kmh"] / 60) * elapsed * 1000

                if ag["status"] == "arrived" or dist_covered_m >= ag["route_dist_m"]:
                    ag["status"] = "arrived"
                    arrived += 1
                    arrived_total = max(arrived_total, sum(1 for a in agents if a["status"] == "arrived"))
                    arrived_by_desa[ag["desa"]] = arrived_by_desa.get(ag["desa"], 0) + ag["population"]
                    positions.append({"id": ag["id"], "lat": ag["target_lat"], "lon": ag["target_lon"],
                                     "status": "arrived", "pop": ag["population"]})
                    continue

                # Interpolate position along route
                progress = min(1.0, dist_covered_m / max(ag["route_dist_m"], 1))
                path = ag["route_path"]
                interp_idx = min(int(progress * (len(path)-1)), len(path)-2)
                frac = progress * (len(path)-1) - interp_idx
                p1, p2 = path[interp_idx], path[min(interp_idx+1, len(path)-1)]
                cur_lat = p1[0] + frac * (p2[0] - p1[0])
                cur_lon = p1[1] + frac * (p2[1] - p1[1])

                ag["status"] = "moving"
                moved += 1

                # Bottleneck detection
                seg_key = f"{round(cur_lat,3)},{round(cur_lon,3)}"
                bottlenecks[seg_key] = bottlenecks.get(seg_key, 0) + ag["population"]

                positions.append({"id": ag["id"], "lat": cur_lat, "lon": cur_lon,
                                 "status": "moving", "pop": ag["population"]})

            # Stranded = masih waiting saat T > warning + 30 (terlambat)
            stranded = sum(1 for ag in agents if ag["status"] == "waiting" and t > warning_min + 30)

            if t % 5 == 0 or t == sim_dur:  # sample setiap 5 mnt untuk efisiensi
                timeline.append({
                    "t_min":   t,
                    "moving":  moved,
                    "arrived": sum(1 for ag in agents if ag["status"] == "arrived"),
                    "waiting": sum(1 for ag in agents if ag["status"] == "waiting"),
                    "stranded": stranded,
                    "positions": positions[:200],  # batas 200 posisi per frame untuk performa
                })

        #    Summary statistics                                
        total_pop = sum(ag["population"] for ag in agents)
        final_arrived = sum(ag["population"] for ag in agents if ag["status"] == "arrived")
        avg_time = sum(ag["route_time_min"] for ag in agents) / len(agents)

        bottleneck_list = sorted(
            [{"lat": float(k.split(",")[0]), "lon": float(k.split(",")[1]), "count": v}
             for k, v in bottlenecks.items()],
            key=lambda x: -x["count"]
        )[:20]

        return {
            "ok":              True,
            "summary": {
                "total_agents":    len(agents),
                "total_population": total_pop,
                "arrived_pop":     final_arrived,
                "arrival_rate":    round(final_arrived / max(total_pop, 1) * 100, 1),
                "avg_time_min":    round(avg_time, 1),
                "max_time_min":    round(max(ag["route_time_min"] for ag in agents), 1),
                "warning_time_min": warning_min,
                "tes_count":       len(tes_list),
                "desa_count":      len(desa_list),
            },
            "agents": [{
                "id":          ag["id"],
                "desa":        ag["desa"],
                "population":  ag["population"],
                "start":       [ag["start_lat"], ag["start_lon"]],
                "target":      [ag["target_lat"], ag["target_lon"]],
                "target_tes":  ag["target_tes"],
                "route_path":  ag["route_path"][:50],  # batas 50 sel
                "dist_km":     round(ag["route_dist_m"] / 1000, 2),
                "time_min":    round(ag["route_time_min"], 1),
                "depart_min":  round(ag["depart_min"], 1),
                "arrive_min":  round(ag["arrive_min"], 1),
                "status":      ag["status"],
            } for ag in agents],
            "timeline":        timeline,
            "bottlenecks":     bottleneck_list,
            "arrived_by_desa": arrived_by_desa,
        }

    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()}


if __name__ == "__main__":
    main()
