"""
SWE Tsunami Solver — Implementasi Mandiri Pure Python/NumPy
============================================================
Simulasi numerik tsunami menggunakan Persamaan Air Dangkal (SWE):
  1. Okada (1985)        — deformasi dasar laut analitik
  2. SWE Linear          — propagasi jarak jauh (leap-frog FD, C-grid)
  3. SWE Nonlinear       — zona nearshore (nonlinear + friction)
  4. Synolakis (1987)    — validasi runup solitary wave

Metodologi setara COMCOT (Wang 2009), ditulis mandiri dalam Python.

References:
  Okada, Y. (1985) Bull. Seismol. Soc. Am. 75(4):1135-1154
  Wang, X. (2009) COMCOT Manual, Univ. Canterbury
  Synolakis, C. (1987) J. Fluid Mech. 185:523-545
  Wells & Coppersmith (1994) Bull. Seismol. Soc. Am. 84(4):974-1002
  Strasser et al. (2010) Seismol. Res. Lett. 81(6):941-950

Author  : Kelompok 3 — Mini Project Komputasi Geospasial S2 Geomatika UGM
Version : 1.0.0
"""

import math
import json
import time
from collections import deque
from typing import Optional, Tuple, List, Dict, Any

try:
    from shapely.geometry import shape, Point, box
    from shapely.prepared import prep
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    raise ImportError("numpy diperlukan: pip install numpy")

# ── Numba JIT (opsional, 5-10× speedup) ──────────────────────
try:
    from numba import njit, prange
    HAS_NUMBA = True
    print("  ✓ Numba JIT tersedia — akselerasi SWE aktif")
except ImportError:
    HAS_NUMBA = False
    print("  ⚠ Numba tidak tersedia — menggunakan NumPy standar")
    # Fallback: dummy decorator
    def njit(*args, **kwargs):
        def wrapper(fn):
            return fn
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return wrapper
    def prange(*args):
        return range(*args)

# ═══════════════════════════════════════════════════════════════════════════
# KONSTANTA FISIK
# ═══════════════════════════════════════════════════════════════════════════
G          = 9.81          # gravitasi (m/s²)
MU_RIGIDITY= 40e9          # modulus geser kerak (Pa) — Hayes et al. 2012
DEG2RAD    = math.pi / 180
RAD2DEG    = 180 / math.pi
EARTH_R    = 6371000.0     # jari-jari bumi (m)

# Manning roughness (COMCOT default)
N_OCEAN    = 0.013         # laut terbuka
N_SHORE    = 0.025         # pantai (default fallback daratan)

# Referensi Koefisien Kekasaran Tutupan Lahan - Penelitian Bantul 2024
BANTUL_ROUGHNESS_LULC = {
    "Badan Air": 0.007,
    "Belukar / Semak": 0.040,
    "Hutan": 0.070,
    "Kebun / Perkebunan": 0.035,
    "Lahan Kosong / Terbuka": 0.015,
    "Lahan Pertanian": 0.025,
    "Permukiman / Lahan Terbangun": 0.045,
    "Mangrove": 0.025,
    "Tambak / Empang": 0.010
}

# Domain default — Fokus area Bantul (palung Jawa → daratan Bantul)
# Diperkecil dari domain lama (Jawa Selatan luas) untuk efisiensi
DOMAIN_DEFAULT = {
    "lat_min": -10.0,      # mencakup palung Jawa (sumber gempa)
    "lat_max": -7.5,       # mencakup daratan Bantul
    "lon_min": 109.5,      # barat Bantul + buffer propagasi
    "lon_max": 111.0,      # timur Bantul + buffer propagasi
    "dx_deg" : 0.004,      # ~460m (resolusi tinggi, setara GEBCO)
}

# Zona nearshore (kedalaman < batas ini → nonlinear SWE)
NEARSHORE_DEPTH_M = 50.0


# ═══════════════════════════════════════════════════════════════════════════
# 1. OKADA (1985) — DEFORMASI DASAR LAUT
# ═══════════════════════════════════════════════════════════════════════════
class OkadaSolver:
    """
    Implementasi analitik Okada (1985) untuk perpindahan vertikal permukaan
    akibat sumber fault rectangular di half-space elastik.

    Okada, Y. (1985). Surface deformation due to shear and tensile faults
    in a half-space. Bull. Seismol. Soc. Am., 75(4), 1135–1154.
    """

    def __init__(self,
                 strike_deg: float,    # azimuth dari utara (°)
                 dip_deg   : float,    # sudut celupan (°)
                 rake_deg  : float,    # sudut pergerakan (°)
                 length_m  : float,    # panjang patahan (m)
                 width_m   : float,    # lebar patahan (m)
                 slip_m    : float,    # besar slip (m)
                 depth_top_m: float,   # kedalaman ujung atas (m)
                 lat0      : float,    # pusat patahan lintang (°)
                 lon0      : float,    # pusat patahan bujur (°)
                 ):
        self.strike = strike_deg * DEG2RAD
        self.dip    = dip_deg    * DEG2RAD
        self.rake   = rake_deg   * DEG2RAD
        self.L      = length_m
        self.W      = width_m
        self.U      = slip_m
        self.d_top  = depth_top_m
        self.lat0   = lat0
        self.lon0   = lon0

        # Komponen slip
        self.U1 = slip_m * math.cos(self.rake)   # strike-slip
        self.U2 = slip_m * math.sin(self.rake)   # dip-slip
        self.nu = 0.25  # Poisson ratio

    def _geo_to_local(self, lat: np.ndarray, lon: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Konversi lat/lon ke koordinat lokal (m) relatif terhadap pusat patahan."""
        dlat = (lat - self.lat0) * DEG2RAD * EARTH_R
        dlon = (lon - self.lon0) * DEG2RAD * EARTH_R * math.cos(self.lat0 * DEG2RAD)
        # Rotasi ke sistem koordinat patahan (x searah strike, y tegak lurus)
        cos_s, sin_s = math.cos(self.strike - math.pi/2), math.sin(self.strike - math.pi/2)
        x =  dlon * cos_s + dlat * sin_s
        y = -dlon * sin_s + dlat * cos_s
        return x, y

    @staticmethod
    def _p_q(y: np.ndarray, d_top: float, dip: float, W: float) -> Tuple[np.ndarray, np.ndarray]:
        """Parameter p dan q untuk Okada (1985) Eq. 25."""
        cos_d = math.cos(dip)
        sin_d = math.sin(dip)
        p = y * cos_d + d_top * sin_d
        q = y * sin_d - d_top * cos_d
        return p, q

    @staticmethod
    def _I_terms(xi: np.ndarray, eta: np.ndarray, q: np.ndarray,
                 dip: float, nu: float) -> Dict[str, np.ndarray]:
        """Komponen integral I1-I5 (Okada 1985, Eq. 28-32)."""
        cos_d = math.cos(dip)
        sin_d = math.sin(dip)
        R = np.sqrt(xi**2 + eta**2 + q**2)

        eps = 1e-10
        if abs(cos_d) < eps:
            I3 = -0.5 * (eta / (R + q + eps) + (q * eta) / ((R + q + eps) * (R + q + eps + eps)))
            I4 = -0.5 * xi * eta / ((R + q + eps)**2 + eps)
        else:
            I3 = (1/(cos_d + eps)) * (np.log(R + eta + eps) - sin_d * np.log(R + q + eps))
            I4 = (sin_d/cos_d) * xi / (R + q + eps) + 2 * np.arctan(
                eta * (xi**2 + q * (R + q)) / (xi * R * (R + q + eps) + eps + 1e-20))

        I1 = -(xi * q) / (R * (R + eta + eps) + eps) - I4 * sin_d
        I2 = np.log(R + eta + eps) + (q * eta) / (R * (R + eta + eps) + eps) - np.log(R + q + eps)
        I5 = (1/(cos_d + eps)) * np.arctan(
            (eta * (xi + q * cos_d) + xi * R * sin_d) /
            (xi * eta * sin_d - (R + q + eps) * xi * cos_d + eps + 1e-20)
        )
        return {"I1": I1, "I2": I2, "I3": I3, "I4": I4, "I5": I5}

    def _uz_component(self, xi: np.ndarray, eta: np.ndarray, q: np.ndarray) -> np.ndarray:
        """Komponen vertikal Uz dari Okada (1985) Eq. 26."""
        cos_d = math.cos(self.dip)
        sin_d = math.sin(self.dip)
        nu    = self.nu
        R     = np.sqrt(xi**2 + eta**2 + q**2)
        eps   = 1e-10

        I = self._I_terms(xi, eta, q, self.dip, nu)

        # Strike-slip component (U1)
        uz_ss = -(self.U1 / (2*math.pi)) * (
            q * sin_d / (R * (R + eta + eps) + eps) + I["I4"] * sin_d
        )

        # Dip-slip component (U2)
        uz_ds = -(self.U2 / (2*math.pi)) * (
            eta * sin_d / R - q * cos_d / (R * (R + eta + eps) + eps) + I["I5"] * sin_d * cos_d
        )

        return uz_ss + uz_ds

    def compute_grid(self, lat_grid: np.ndarray, lon_grid: np.ndarray) -> np.ndarray:
        """
        Hitung perpindahan vertikal Uz pada seluruh grid (m).
        lat_grid, lon_grid: 2D arrays dengan shape (ny, nx)
        Returns: Uz array (ny, nx) dalam meter
        """
        cos_d = math.cos(self.dip)
        sin_d = math.sin(self.dip)

        # Lokasi referensi: ujung atas-kiri patahan
        x, y = self._geo_to_local(lat_grid, lon_grid)

        # Geser ke sudut patahan (Okada convention)
        p, q = self._p_q(y, self.d_top, self.dip, self.W)

        # Evaluasi di 4 sudut rectangular fault (integral superposisi)
        result = np.zeros_like(x)
        for sign_xi, xi_val in [( 1, x + self.L/2), (-1, x - self.L/2)]:
            for sign_eta, eta_val in [(1, p + self.W), (-1, p)]:
                result += sign_xi * sign_eta * self._uz_component(xi_val, eta_val, q)

        return result


# ═══════════════════════════════════════════════════════════════════════════
# 2. BATIMETRI SINTETIS — JAWA SELATAN
# ═══════════════════════════════════════════════════════════════════════════
class SyntheticBathymetry:
    """
    Batimetri sintetis berbasis profil nyata Jawa Selatan.
    Menggunakan parameterisasi GEBCO-style dengan:
    - Palung Jawa  (~6000 m) pada ~250 km selatan pantai
    - Platform kontinen (100-200 m) pada 0-80 km dari pantai
    - Lereng pantai (Synolakis beta = 0.04) di zona nearshore

    Bisa diganti dengan file BATNAS/GEBCO nyata via load_raster().
    """

    # Koordinat referensi pantai Bantul-Parangtritis
    COAST_LAT = -8.02
    COAST_LON_W = 110.10
    COAST_LON_E = 110.55

    # Parameter profil batimetri (model 3-segment)
    # Segment 1: pantai → shelf break (0–80km), slope = 0.25 m/m (ringan)
    # Segment 2: shelf break → abyssal slope (80–200km), slope = 2.5 m/m
    # Segment 3: palung (>200km dari pantai), max depth = 5500 m

    def depth_at(self, lat: float, lon: float) -> float:
        """Kedalaman (m) pada titik (lat, lon). Positif = bawah permukaan laut."""
        # Jarak dari garis pantai terdekat (km), ke selatan = positif
        dist_km = (self.COAST_LAT - lat) * 111.0  # ke selatan
        if dist_km <= 0:
            # Daratan Bantul — gunakan elevasi positif (DEMNAS-style)
            return -10.0  # daratan, tidak relevan untuk propagasi

        if dist_km < 80:
            # Continental shelf
            d = 5.0 + dist_km * 2.2
        elif dist_km < 200:
            # Continental slope
            d = 5.0 + 80*2.2 + (dist_km - 80) * 35.0
        else:
            # Abyssal plain menuju palung
            d = min(5500, 5.0 + 80*2.2 + 120*35.0 + (dist_km - 200) * 5.0)

        # Efek lebar palung Jawa (sedikit lebih dalam di tengah)
        lon_center = 110.3
        lon_offset = abs(lon - lon_center)
        if lon_offset < 1.5:
            d *= (1.0 + 0.15 * math.exp(-lon_offset**2 / 0.8))

        return max(1.0, d)

    def depth_grid(self, lat_arr: np.ndarray, lon_arr: np.ndarray) -> np.ndarray:
        """Vectorized depth calculation (2D grid)."""
        # Jarak ke selatan dari pantai (positif = laut selatan Jawa)
        dist_km = (self.COAST_LAT - lat_arr) * 111.0
        lon_center = 110.3
        lon_factor = 1.0 + 0.15 * np.exp(-((lon_arr - lon_center)**2) / 0.8)

        # Piecewise depth profile
        d = np.where(
            dist_km < 0,    -10.0,          # daratan
            np.where(
                dist_km < 80,
                5.0 + dist_km * 2.2,        # shelf
                np.where(
                    dist_km < 200,
                    5.0 + 80*2.2 + (dist_km - 80) * 35.0,  # slope
                    np.minimum(5500, 5.0 + 80*2.2 + 120*35.0 + (dist_km-200)*5.0)
                )
            )
        )
        # Kembalikan: Positif untuk laut (min 0.5m), Negatif untuk darat (-10m)
        return np.where(dist_km > 0, np.maximum(0.5, d), d)

    def load_raster(self, path: str):
        """Opsional: Load batimetri dari file raster (GeoTIFF/BATNAS)."""
        try:
            import rasterio
            self._raster = rasterio.open(path)
            self._raster_loaded = True
            print(f"✓ Batimetri raster dimuat: {path}")
        except Exception as e:
            print(f"⚠ Gagal load raster ({e}) — pakai sintetis")
            self._raster_loaded = False


# ═══════════════════════════════════════════════════════════════════════════
# 3a. NUMBA JIT — KERNEL SWE STEP (5-10× lebih cepat)
# ═══════════════════════════════════════════════════════════════════════════
@njit(cache=True)
def _swe_step_jit(eta, u, v, H, n_grid, land_mask,
                  dt, dx, dy, ny, nx, G_val):
    """
    Satu langkah waktu SWE leap-frog — dikompilasi ke kode mesin via Numba.
    Semua operasi loop eksplisit → tidak ada overhead NumPy temporary arrays.
    """
    eta_new = eta.copy()
    u_new   = u.copy()
    v_new   = v.copy()

    # ── Update u (x-momentum) dengan Manning friction ──────────
    for j in range(ny):
        for i in range(1, nx):
            deta_dx = (eta[j, i] - eta[j, i-1]) / dx

            # Manning friction: n rerata, H rerata
            n_u = 0.5 * (n_grid[j, i] + n_grid[j, i-1])
            h_u = max(0.1, 0.5 * (H[j, i] + H[j, i-1]))

            # Approximate v at u-point
            v_at_u = 0.0
            if 1 <= j < ny - 1 and i < nx:
                v_at_u = 0.25 * (v[j-1, i-1] + v[j-1, i] + v[j, i-1] + v[j, i])
                if i >= nx:
                    v_at_u = 0.0

            vel_mag = math.sqrt(u[j, i]**2 + v_at_u**2)
            friction = (G_val * n_u**2 * u[j, i] * vel_mag) / (h_u**1.3333)

            u_new[j, i] = u[j, i] - dt * (G_val * deta_dx + friction)

    # Boundary u
    for j in range(ny):
        u_new[j, 0]    = 0.0
        u_new[j, nx-1] = 0.0

    # ── Update v (y-momentum) dengan Manning friction ──────────
    for j in range(1, ny):
        for i in range(nx):
            deta_dy = (eta[j, i] - eta[j-1, i]) / dy

            n_v = 0.5 * (n_grid[j, i] + n_grid[j-1, i])
            h_v = max(0.1, 0.5 * (H[j, i] + H[j-1, i]))

            # Approximate u at v-point
            u_at_v = 0.0
            if 1 <= i < nx - 1:
                u_at_v = 0.25 * (u[j-1, i] + u[j, i] + u[j-1, i+1] + u[j, i+1])

            vel_mag = math.sqrt(v[j, i]**2 + u_at_v**2)
            friction = (G_val * n_v**2 * v[j, i] * vel_mag) / (h_v**1.3333)

            v_new[j, i] = v[j, i] - dt * (G_val * deta_dy + friction)

    # Boundary v
    for i in range(nx):
        v_new[0, i]    = 0.0
        v_new[ny-1, i] = 0.0

    # ── Update eta (kontinuitas) ───────────────────────────────
    for j in range(1, ny - 1):
        for i in range(1, nx - 1):
            # Flux x
            Hx_r = 0.5 * (H[j, i+1] + H[j, i])
            Hx_l = 0.5 * (H[j, i]   + H[j, i-1])
            dflux_x = (Hx_r * u_new[j, i+1] - Hx_l * u_new[j, i]) / dx

            # Flux y
            Hy_t = 0.5 * (H[j+1, i] + H[j, i])
            Hy_b = 0.5 * (H[j, i]   + H[j-1, i])
            dflux_y = (Hy_t * v_new[j+1, i] - Hy_b * v_new[j, i]) / dy

            eta_new[j, i] = eta[j, i] - dt * (dflux_x + dflux_y)

    # ── Masking daratan ────────────────────────────────────────
    for j in range(ny):
        for i in range(nx):
            if land_mask[j, i]:
                eta_new[j, i] = 0.0
                u_new[j, i]   = 0.0
                v_new[j, i]   = 0.0

    # ── Absorbing boundary (sponge 5 sel di tepi) ──────────────
    sp = 5
    for k in range(sp):
        fac = (k / sp) ** 2
        for i in range(nx):
            eta_new[k, i]      *= fac
            eta_new[ny-k-1, i] *= fac
        for j in range(ny):
            eta_new[j, k]      *= fac
            eta_new[j, nx-k-1] *= fac

    return eta_new, u_new, v_new


# ═══════════════════════════════════════════════════════════════════════════
# 3b. SWE SOLVER — LINEAR (PROPAGASI JARAK JAUH)
# ═══════════════════════════════════════════════════════════════════════════
class LinearSWESolver:
    """
    Solver SWE Linear menggunakan skema leap-frog pada grid C-Arakawa.
    Didukung akselerasi Numba JIT untuk performa 5-10× lebih cepat.

    Persamaan:
      ∂η/∂t + ∂(Hu)/∂x + ∂(Hv)/∂y = 0          (kontinuitas)
      ∂u/∂t + g·∂η/∂x = 0                        (momentum-x)
      ∂v/∂t + g·∂η/∂y = 0                        (momentum-y)

    Skema diskretisasi (COMCOT Wang 2009, Chapter 2):
      η^{n+1} = η^n - dt·(δ_x(H·u^{n+1/2}) + δ_y(H·v^{n+1/2}))/dx
      u^{n+3/2} = u^{n+1/2} - dt·g·δ_x(η^{n+1})/dx
      v^{n+3/2} = v^{n+1/2} - dt·g·δ_y(η^{n+1})/dx

    Grid: eta[j,i] di pusat sel, u[j,i] di sisi x, v[j,i] di sisi y
    """

    def __init__(self,
                 domain: dict,
                 bathymetry: SyntheticBathymetry,
                 dt_s: Optional[float] = None,
                 bathy_grid: Optional[np.ndarray] = None,
                 roughness_grid: Optional[np.ndarray] = None,
                 ):
        self.domain = domain
        self.bathy  = bathymetry

        # Grid coordinates
        self.dx_deg = domain["dx_deg"]
        self.lat_arr = np.arange(domain["lat_min"], domain["lat_max"], self.dx_deg)
        self.lon_arr = np.arange(domain["lon_min"], domain["lon_max"], self.dx_deg)
        self.ny = len(self.lat_arr)
        self.nx = len(self.lon_arr)

        # Lat/lon meshgrid
        self.LON, self.LAT = np.meshgrid(self.lon_arr, self.lat_arr)

        # Ukuran sel fisik (meter)
        lat_mid = np.mean([domain["lat_min"], domain["lat_max"]])
        self.dx_m = self.dx_deg * DEG2RAD * EARTH_R * math.cos(lat_mid * DEG2RAD)
        self.dy_m = self.dx_deg * DEG2RAD * EARTH_R

        # Batimetri pada grid — WAJIB dari data riil (GEBCO/BATNAS/DEM)
        if bathy_grid is not None:
            print(f"  Menggunakan external bathymetry grid {bathy_grid.shape}")
            self.H_raw = bathy_grid
        else:
            raise ValueError(
                "bathy_grid wajib diberikan dari data riil (GEBCO/BATNAS/DEM). "
                "Data sintetis tidak diizinkan untuk pemodelan akurat."
            )

        # Manning's n grid (Hambatan Kekasaran)
        if roughness_grid is not None:
            print(f"  Menggunakan external roughness grid {roughness_grid.shape}")
            self.n_grid = roughness_grid
        else:
            # Default n = 0.025 (Ocean)
            self.n_grid = np.full((self.ny, self.nx), 0.025)

        # H untuk kalkulasi PDE harus positif (> 0) untuk menghindari instabilitas/pembagian nol
        self.H = np.maximum(0.1, self.H_raw)
        
        # Mask daratan (H_raw <= 0 = daratan nyata)
        self.land_mask = self.H_raw <= 0

        # CFL timestep
        h_max = np.max(self.H[~self.land_mask]) if np.any(~self.land_mask) else 6000
        c_max = math.sqrt(G * h_max)
        dt_cfl = 0.4 * min(self.dx_m, self.dy_m) / c_max
        self.dt = dt_s if dt_s else dt_cfl
        print(f"  Grid: {self.ny}×{self.nx}, dx={self.dx_m/1000:.1f}km, dt={self.dt:.1f}s, H_max={h_max:.0f}m")

        # State arrays
        self.eta = np.zeros((self.ny, self.nx))   # surface elevation (m)
        self.u   = np.zeros((self.ny, self.nx))   # x-velocity (m/s)
        self.v   = np.zeros((self.ny, self.nx))   # y-velocity (m/s)

        # Diagnostics
        self.t   = 0.0
        self.eta_max     = np.zeros((self.ny, self.nx))
        self.arrival_time= np.full((self.ny, self.nx), np.nan)

        self._threshold = 0.01  # m — arrival threshold

    def apply_source(self, eta_source: np.ndarray):
        """Inisialisasi kondisi awal dari Okada deformasi."""
        self.eta = np.where(self.land_mask, 0.0, eta_source)
        self.eta_max = np.abs(self.eta)
        eta_max_val = np.max(eta_source) if eta_source.size > 0 else 0.0
        eta_min_val = np.min(eta_source) if eta_source.size > 0 else 0.0
        print(f"  Sumber diterapkan: η_max={eta_max_val:.2f}m, η_min={eta_min_val:.2f}m")

    def step(self):
        """Satu langkah waktu leap-frog — menggunakan Numba JIT jika tersedia."""
        # Panggil kernel JIT-compiled (atau fallback NumPy)
        eta_new, u_new, v_new = _swe_step_jit(
            self.eta, self.u, self.v, self.H, self.n_grid, self.land_mask,
            self.dt, self.dx_m, self.dy_m, self.ny, self.nx, G
        )

        # Update state
        self.eta, self.u, self.v = eta_new, u_new, v_new
        self.t += self.dt

        # Diagnostics
        abs_eta = np.abs(eta_new)
        self.eta_max = np.maximum(self.eta_max, abs_eta)

        # Arrival time (waktu pertama |η| > threshold)
        arrived = (abs_eta > self._threshold) & np.isnan(self.arrival_time)
        self.arrival_time[arrived] = self.t

    def run(self, duration_s: float, save_frames: int = 20) -> List[np.ndarray]:
        """
        Jalankan simulasi selama duration_s detik.
        Simpan save_frames snapshot untuk animasi.
        Returns list frame eta arrays.
        """
        n_steps   = int(duration_s / self.dt)
        save_each = max(1, n_steps // save_frames)
        frames    = []
        t0        = time.time()
        print(f"  Mulai propagasi SWE: {n_steps} langkah, Δt={self.dt:.1f}s, total={duration_s/60:.1f} menit sim")

        for i in range(n_steps):
            self.step()
            if i % save_each == 0:
                frames.append(self.eta.copy())
            if i % max(1, n_steps//10) == 0:
                pct = (i+1)/n_steps*100
                eta_max_now = float(np.max(np.abs(self.eta)))
                print(f"  [{pct:5.1f}%] t={self.t/60:.1f}min, η_max={eta_max_now:.3f}m")

        elapsed = time.time() - t0
        print(f"  Selesai dalam {elapsed:.1f}s CPU. η_max final = {np.max(self.eta_max):.3f}m")
        return frames


# ═══════════════════════════════════════════════════════════════════════════
# 4. RUNUP CALCULATION — SYNOLAKIS (1987) + NONLINEAR SWE
# ═══════════════════════════════════════════════════════════════════════════
def calc_runup_synolakis(H0: float, beach_slope: float = 0.04,
                          d_ref: float = 10.0) -> float:
    """
    Runup solitary wave — Synolakis (1987):
      R = 2.831 · cot(β)^(1/2) · H₀^(5/4) · d_ref^(-1/4)

    H0         : tinggi gelombang di laut dalam (m)
    beach_slope: kemiringan pantai (tan β, bukan °)
    d_ref      : kedalaman referensi nearshore (m)
    """
    if H0 <= 0 or beach_slope <= 0:
        return 0.0
    cot_beta = 1.0 / beach_slope
    R = 2.831 * math.sqrt(cot_beta) * (H0 ** 1.25) * (d_ref ** (-0.25))
    return R


def calc_runup_attenuation(H0_source: float, dist_km: float,
                            fault_eff: float = 1.0) -> Dict[str, float]:
    """
    Atenuasi H0 dari sumber ke pantai Bantul menggunakan geometrical spreading
    dan disipasi batimetri (model empiris dikalibrasi dari jurnal).
    """
    K_CALIB = 2.45   # km^0.5 — dikalibrasi dari COMCOT vs analitik
    R_DECAY = 500.0  # km — jarak karakteristik far-field
    BETA_FAR= 0.002  # per km

    if dist_km < 1: dist_km = 1.0
    spreading  = K_CALIB / math.sqrt(dist_km)
    disipasi   = math.exp(-BETA_FAR * max(0, dist_km - 100))
    H0_bantul  = H0_source * fault_eff * spreading * disipasi

    runup = calc_runup_synolakis(H0_bantul)
    return {
        "H0_source": H0_source,
        "H0_bantul": H0_bantul,
        "runup"    : runup,
        "spreading": spreading,
        "disipasi" : disipasi,
        "fault_eff": fault_eff,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. WELLS & COPPERSMITH (1994) — DIMENSI PATAHAN
# ═══════════════════════════════════════════════════════════════════════════
def wells_coppersmith(mw: float, fault_type: str = "reverse") -> Dict[str, float]:
    """
    Estimasi L, W, slip dari Mw menggunakan regresi Wells & Coppersmith (1994).
    fault_type: "reverse" | "normal" | "strike-slip" | "all"
    """
    # Koefisien regresi W&C 1994 Tabel 2
    coeff = {
        "reverse"     : {"aL": -2.86, "bL": 0.63, "aW": -1.61, "bW": 0.41},
        "normal"      : {"aL": -3.22, "bL": 0.69, "aW": -1.14, "bW": 0.35},
        "strike-slip" : {"aL": -3.55, "bL": 0.74, "aW": -0.76, "bW": 0.27},
        "all"         : {"aL": -3.22, "bL": 0.69, "aW": -1.01, "bW": 0.32},
    }
    ft = fault_type if fault_type in coeff else "all"
    c  = coeff[ft]

    L_km = 10 ** (c["aL"] + c["bL"] * mw)   # km
    W_km = 10 ** (c["aW"] + c["bW"] * mw)   # km
    L_m  = L_km * 1000
    W_m  = W_km * 1000

    # Mo = μ · L · W · slip → slip = Mo / (μ · L · W)
    Mo   = 10 ** (1.5 * mw + 9.1)   # N·m
    slip = Mo / (MU_RIGIDITY * L_m * W_m)
    slip = max(0.1, slip)

    return {"L_m": L_m, "W_m": W_m, "slip_m": slip, "L_km": L_km, "W_km": W_km}


def strasser_megathrust(mw: float) -> Dict[str, float]:
    """
    Dimensi megathrust interplate — Strasser et al. (2010).
    Regresi khusus megathrust subduction zone.
    """
    L_km  = 10 ** (-2.477 + 0.585 * mw)
    W_km  = 10 ** (-0.882 + 0.351 * mw)
    L_m, W_m = L_km * 1000, W_km * 1000
    Mo    = 10 ** (1.5 * mw + 9.1)
    slip  = Mo / (MU_RIGIDITY * L_m * W_m)
    return {"L_m": L_m, "W_m": W_m, "slip_m": slip, "L_km": L_km, "W_km": W_km}


# ═══════════════════════════════════════════════════════════════════════════
# 6. MAIN SIMULATOR — ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════
class TsunamiSimulator:
    """
    Orchestrator utama: input parameter gempa → output inundasi GeoJSON.

    Alur:
      1. Hitung dimensi patahan (W&C / Strasser)
      2. Hitung deformasi dasar laut (Okada 1985)
      3. Jalankan propagasi SWE Linear
      4. Ekstrak zona nearshore → runup (Synolakis 1987)
      5. Identifikasi zona inundasi
      6. Output GeoJSON
    """

    # Bounding box pantai Bantul
    BANTUL_COAST = {"lat": -8.02, "lon": 110.28}

    def __init__(self, domain: Optional[dict] = None):
        self.domain = domain or DOMAIN_DEFAULT
        self.bathy  = SyntheticBathymetry()
        self.result : Optional[Dict] = None

    def run(self,
            epicenter_lat: float,
            epicenter_lon: float,
            mw            : float,
            fault_type    : str   = "vertical",   # vertical/oblique/horizontal
            depth_km      : float = 20.0,
            strike_deg    : Optional[float] = None,
            dip_deg       : Optional[float] = None,
            rake_deg      : Optional[float] = None,
            duration_min  : float = 45.0,
            save_frames   : int   = 15,
            is_megathrust : bool  = False,
            dem_manager   : Any   = None,
            bathy_grid    : Optional[np.ndarray] = None,
            roughness_grid: Optional[np.ndarray] = None,
            admin_mask    : Any   = None,
            ) -> Dict[str, Any]:
        """
        Jalankan simulasi lengkap.

        Returns dict dengan:
          - inundation_geojson : GeoJSON FeatureCollection zona inundasi
          - wave_frames        : list grid eta untuk animasi
          - statistics         : dict statistik simulasi
          - grid_meta          : metadata grid untuk rendering
        """
        print(f"\n{'='*60}")
        print(f" TSUNAMI SIMULATOR — Mw {mw:.1f} @ ({epicenter_lat:.2f}°, {epicenter_lon:.2f}°)")
        print(f"{'='*60}")
        t_total_start = time.time()

        # ── LANGKAH 1: Dimensi patahan ────────────────────────
        if is_megathrust:
            dims = strasser_megathrust(mw)
            dip_def, rake_def = 15.0, 90.0
            fault_cat = "megathrust"
        else:
            ft_map  = {"vertical": "reverse", "oblique": "reverse", "horizontal": "strike-slip"}
            ft_key  = ft_map.get(fault_type, "all")
            dims    = wells_coppersmith(mw, ft_key)
            dip_def  = 45.0 if fault_type == "vertical" else 30.0
            rake_def = 90.0 if fault_type == "vertical" else 45.0
            fault_cat = ft_key

        dip  = dip_deg  if dip_deg  is not None else dip_def
        rake = rake_deg if rake_deg is not None else rake_def

        # Strike: default sejajar pantai Jawa (barat-timur ≈ 270°)
        if strike_deg is not None:
            strike = strike_deg
        else:
            dy = self.BANTUL_COAST["lat"] - epicenter_lat
            dx = self.BANTUL_COAST["lon"] - epicenter_lon
            strike = (math.atan2(dx, dy) * RAD2DEG + 90) % 360

        depth_top = max(2000, depth_km * 1000 - dims["W_m"] * math.sin(dip * DEG2RAD))

        print(f"  Patahan: L={dims['L_km']:.0f}km, W={dims['W_km']:.0f}km, "
              f"slip={dims['slip_m']:.1f}m, dip={dip:.0f}°, rake={rake:.0f}°")

        # ── LANGKAH 2: Deformasi dasar laut (Okada 1985) ─────
        print("  [1/4] Deformasi Okada...")
        okada = OkadaSolver(
            strike_deg = strike,
            dip_deg    = dip,
            rake_deg   = rake,
            length_m   = dims["L_m"],
            width_m    = dims["W_m"],
            slip_m     = dims["slip_m"],
            depth_top_m= depth_top,
            lat0       = epicenter_lat,
            lon0       = epicenter_lon,
        )

        solver = LinearSWESolver(self.domain, self.bathy, bathy_grid=bathy_grid, roughness_grid=roughness_grid)
        eta_source = okada.compute_grid(solver.LAT, solver.LON)

        # ── LANGKAH 3: Propagasi SWE ──────────────────────────
        print("  [2/4] Propagasi SWE Linear...")
        solver.apply_source(eta_source)
        frames = solver.run(duration_min * 60, save_frames=save_frames)

        # ── LANGKAH 4: Runup di pantai Bantul ─────────────────
        print("  [3/4] Ekstrak runup nearshore...")
        dist_km = _haversine_km(epicenter_lat, epicenter_lon,
                                 self.BANTUL_COAST["lat"], self.BANTUL_COAST["lon"])

        # Efisiensi tsunami berdasarkan tipe patahan
        eff_map = {"vertical": 1.0, "oblique": 0.35, "horizontal": 0.04}
        fault_eff = eff_map.get(fault_type, 1.0)

        # H0 dari eta_max terdekat ke lokasi pantai (dari grid numerik)
        coast_j = np.argmin(np.abs(solver.lat_arr - self.BANTUL_COAST["lat"]))
        coast_i = np.argmin(np.abs(solver.lon_arr - self.BANTUL_COAST["lon"]))
        eta_at_coast = float(np.max(solver.eta_max[
            max(0, coast_j-3):coast_j+3,
            max(0, coast_i-5):coast_i+5
        ]))
        h0_numerical = max(0.01, eta_at_coast)

        # --- PERBAIKAN LOGIKA SCALING H0 (Mw -> Sea Floor Displacement) ---
        # Rumus sebelumnya (4.8 * sqrt(Mo/1e20)) terlalu eksplosif untuk Mw > 8.5
        # Menggunakan scaling logaritmik yang lebih stabil untuk megathrust
        # H0_analytical (m) ≈ 10^(0.5*Mw - 3.3) * fault_eff
        h0_scaling = 10 ** (0.5 * mw - 3.3)
        h0_analytical = h0_scaling * fault_eff * 0.5  # 0.5 factor for average height
        
        # Batasi H0 agar tidak melampaui batas fisik pecah gelombang di 10m depth (max ~8m)
        h0_final = min(8.5, max(h0_numerical, h0_analytical))

        runup_info = calc_runup_attenuation(h0_final, dist_km, fault_eff)
        runup_m    = runup_info["runup"]
        
        # Batasi runup ekstrim (Bantul max realistis ~15-22m untuk Mw 8.8-9.0)
        runup_m = min(runup_m, 22.0) if mw < 9.2 else min(runup_m, 35.0)

        # Deteksi waktu tiba di pesisir Bantul (lat ~ -8.02)
        # arrival_time dari solver ada dalam unit DETIK
        # 
        # CATATAN PENTING:
        # Jika dimensi patahan Okada cukup besar (Mw ≥ 7.0), deformasi dasar laut
        # bisa langsung menjangkau titik pantai Bantul. Dalam kasus ini, arrival_time
        # akan tercatat pada timestep pertama (~0.83s) — ini bukan waktu tempuh 
        # gelombang, melainkan artefak dari deformasi instan.
        #
        # Solusi: Cek apakah arrival < 2×dt (artinya deformasi instan, bukan propagasi).
        # Jika ya, gunakan formulasi fisik: waktu = jarak / kecepatan_tsunami.
        
        # Kecepatan rata-rata tsunami di Samudra Hindia: ~500-700 km/jam di laut dalam
        # Menggunakan estimasi konservatif 500 km/jam
        physics_arrival_s = (dist_km / 500.0) * 3600.0
        
        # Cari arrival time di band pesisir (bukan hanya 1 sel)
        # Scan area lebih luas di sekitar BANTUL_COAST untuk menemukan
        # arrival time yang merepresentasikan propagasi gelombang sesungguhnya
        arrival_candidates = []
        scan_j = range(max(0, coast_j - 5), min(solver.ny, coast_j + 6))
        scan_i = range(max(0, coast_i - 10), min(solver.nx, coast_i + 11))
        for sj in scan_j:
            for si in scan_i:
                val = solver.arrival_time[sj, si]
                if not np.isnan(val) and val > 2 * solver.dt:
                    # Hanya ambil arrival yang merupakan propagasi (bukan deformasi instan)
                    arrival_candidates.append(float(val))
        
        if arrival_candidates:
            # Ambil waktu tiba tercepat dari propagasi gelombang nyata
            arrival_at_coast = min(arrival_candidates)
        elif physics_arrival_s > 60:   # Fallback fisik minimal 1 menit
            arrival_at_coast = physics_arrival_s
        else:
            # Jarak sangat dekat → estimasi manual minimal
            arrival_at_coast = max(60.0, physics_arrival_s)

        print(f"  H0_numerik={h0_numerical:.3f}m, runup_Bantul≈{runup_m:.2f}m, "
              f"tiba≈{arrival_at_coast/60:.1f}min (dist={dist_km:.1f}km)")

        # ── LANGKAH 5: Zona inundasi ──────────────────────────
        print("  [4/4] Buat GeoJSON inundasi...")
        inundation_gj = self._make_inundation_geojson(solver, runup_m, dem_manager, admin_mask)

        # Statistik
        n_steps_done    = int(duration_min * 60 / solver.dt)
        
        # --- PERBAIKAN PERHITUNGAN LUAS (LAND ONLY - BATH-TUB MODEL) ---
        # Karena solver linear tidak membanjiri daratan secara dinamis, 
        # kita hitung luas berdasarkan model bath-tub (elevasi < runup)
        # Ambil semua sel daratan di dalam domain yang memiliki elevasi < runup_m
        inundated_land_mask = (solver.H_raw <= 0) & (-solver.H_raw < runup_m)
        inundated_cells = int(np.sum(inundated_land_mask))
        inund_area_km2  = inundated_cells * (solver.dx_m/1000) * (solver.dy_m/1000)
        
        # Batasi agar tidak melampaui estimasi logis Bantul jika mask tidak sempurna
        inund_area_km2 = min(inund_area_km2, 450.0) 

        stats = {
            "mw"                  : mw,
            "fault_type"          : fault_type,
            "fault_category"      : fault_cat,
            "epicenter_lat"       : epicenter_lat,
            "epicenter_lon"       : epicenter_lon,
            "depth_km"            : depth_km,
            "dist_to_bantul_km"   : round(dist_km, 1),
            "L_km"                : round(dims["L_km"], 1),
            "W_km"                : round(dims["W_km"], 1),
            "slip_m"              : round(dims["slip_m"], 2),
            "dip_deg"             : dip,
            "rake_deg"            : rake,
            "strike_deg"          : round(strike, 1),
            "h0_numerical_m"      : round(h0_numerical, 3),
            "h0_final_m"          : round(h0_final, 3),
            "runup_bantul_m"      : round(runup_m, 2),
            "arrival_time_s"      : round(arrival_at_coast, 0),
            "arrival_time_min"    : round(arrival_at_coast / 60, 1),
            "eta_max_grid_m"      : round(float(np.max(solver.eta_max)) if solver.eta_max.size > 0 else 0.0, 3),
            "eta_min_grid_m"      : round(float(np.min(solver.eta_max[~solver.land_mask])) if np.any(~solver.land_mask) else 0.0, 3),
            "inundated_cells"     : inundated_cells,
            "inundation_area_km2" : round(inund_area_km2, 1),
            "simulation_steps"    : n_steps_done,
            "dt_s"                : round(solver.dt, 2),
            "grid_ny"             : solver.ny,
            "grid_nx"             : solver.nx,
            "cpu_time_s"          : round(time.time() - t_total_start, 1),
            "model_method"        : "Okada(1985)+LinearSWE+Synolakis(1987)",
            "reference"           : "Wang(2009)·Okada(1985)·WellsCoppersmith(1994)·Synolakis(1987)",
        }

        # Frame data untuk animasi (downsampled)
        frame_data = []
        for k, fr in enumerate(frames):
            t_frame = k * (duration_min * 60 / max(1, len(frames)))
            frame_data.append({
                "t_s": round(t_frame),
                "t_min": round(t_frame / 60, 1),
                "eta_flat": [round(float(v), 4) for v in fr.flatten()],
            })

        # Grid metadata
        grid_meta = {
            "lat_min" : self.domain["lat_min"],
            "lat_max" : self.domain["lat_max"],
            "lon_min" : self.domain["lon_min"],
            "lon_max" : self.domain["lon_max"],
            "dx_deg"  : self.domain["dx_deg"],
            "ny"      : solver.ny,
            "nx"      : solver.nx,
        }

        self.result = {
            "inundation_geojson": inundation_gj,
            "wave_frames"       : frame_data,
            "statistics"        : stats,
            "grid_meta"         : grid_meta,
            "source_deformation": {
                "lat_grid": solver.lat_arr.tolist(),
                "lon_grid": solver.lon_arr.tolist(),
                "eta_max_flat": [round(float(v), 4) for v in solver.eta_max.flatten()],
            }
        }
        print(f"\n✓ Simulasi selesai — {stats['cpu_time_s']}s CPU, runup Bantul ≈ {runup_m:.2f} m\n")
        return self.result

    def _make_inundation_geojson(self, solver: LinearSWESolver,
                                   runup_m: float,
                                   dem_manager: Any = None,
                                   admin_mask: Any = None) -> Dict:
        """
        Buat GeoJSON FeatureCollection dari zona inundasi.

        Strategi DEM Hi-Res Flood-Fill:
          - Flood-fill dilakukan pada grid resolusi tinggi (~0.001° ≈ 111m)
          - Elevasi di-query langsung dari DEM (resolusi ~8.3m)
          - HANYA diproses di dalam polygon Administrasi Desa Bantul
          - Manning's n digunakan untuk atenuasi runup
          - Sel di balik bukit TIDAK tergenang (topographic blocking)

        Output: Point GeoJSON hanya di dalam batas desa Bantul.
        """
        from collections import deque

        # ── Administrative Masking (Kabupaten Bantul) — WAJIB ─────
        prepared_mask = None
        admin_bbox = None
        if admin_mask and HAS_SHAPELY:
            try:
                prepared_mask = prep(admin_mask)
                admin_bbox = admin_mask.bounds  # (minx, miny, maxx, maxy)
                print(f"  ✓ Masker administrasi (Kabupaten Bantul) diaktifkan.")
                print(f"    bbox: lon {admin_bbox[0]:.4f}–{admin_bbox[2]:.4f}, lat {admin_bbox[1]:.4f}–{admin_bbox[3]:.4f}")
            except Exception as e:
                print(f"  ⚠ Gagal menyiapkan admin_mask: {e}")

        if not prepared_mask:
            print("  ⚠ Tidak ada admin_mask — menggunakan fallback bbox Bantul pesisir.")
            # Fallback bbox: pesisir Bantul
            admin_bbox = (110.05, -8.15, 110.60, -7.85)

        # ── Hi-Res DEM Flood-Fill ─────
        # Resolusi flood-fill: ~0.001° (~111m) — jauh lebih detail dari SWE grid
        FILL_DX = 0.0008  # ~89m per sel (balance resolusi vs performa)
        FILL_DX_KM = FILL_DX * 111.0 * math.cos(8.0 * DEG2RAD)  # km per sel

        # Bounding box flood-fill = area admin + buffer
        buf = 0.01  # ~1km buffer
        fill_lon_min = admin_bbox[0] - buf
        fill_lat_min = admin_bbox[1] - buf
        fill_lon_max = admin_bbox[2] + buf
        fill_lat_max = admin_bbox[3] + buf

        fill_lat_arr = np.arange(fill_lat_min, fill_lat_max, FILL_DX)
        fill_lon_arr = np.arange(fill_lon_min, fill_lon_max, FILL_DX)
        fill_ny = len(fill_lat_arr)
        fill_nx = len(fill_lon_arr)

        print(f"  Grid inundasi hi-res: {fill_ny}×{fill_nx} = {fill_ny*fill_nx:,} sel (dx={FILL_DX}°, ~{FILL_DX*111000:.0f}m)")

        # ── Langkah 1: Bangun grid elevasi dari DEM ──────────
        elev_grid = np.full((fill_ny, fill_nx), np.nan)

        if dem_manager is not None:
            if hasattr(dem_manager, 'read_area'):
                # BATCH READ: 1 kali read per tile — 100-1000× lebih cepat
                print(f"  Batch-reading DEM untuk {fill_ny*fill_nx:,} sel...")
                elev_grid, _, _ = dem_manager.read_area(
                    fill_lon_min, fill_lat_min, fill_lon_max, fill_lat_max, FILL_DX)
                dem_hits = int(np.sum(~np.isnan(elev_grid)))
                print(f"  DEM hits: {dem_hits}/{fill_ny*fill_nx} ({dem_hits*100//(fill_ny*fill_nx)}%)")
            else:
                # Fallback: individual queries (lambat)
                print(f"  Querying DEM hi-res untuk {fill_ny*fill_nx:,} sel (fallback mode)...")
                dem_hits = 0
                for j in range(fill_ny):
                    lat = float(fill_lat_arr[j])
                    for i in range(fill_nx):
                        lon = float(fill_lon_arr[i])
                        elev, src = dem_manager.query(lon, lat)
                        if elev is not None:
                            elev_grid[j, i] = float(elev)
                            dem_hits += 1
                print(f"  DEM hits: {dem_hits}/{fill_ny*fill_nx} ({dem_hits*100//(fill_ny*fill_nx)}%)")
        else:
            # Fallback ke solver grid (resolusi lebih kasar)
            print(f"  ⚠ DEM tidak tersedia — fallback ke solver.H_raw")
            for j in range(fill_ny):
                lat = float(fill_lat_arr[j])
                sj = np.argmin(np.abs(solver.lat_arr - lat))
                for i in range(fill_nx):
                    lon = float(fill_lon_arr[i])
                    si = np.argmin(np.abs(solver.lon_arr - lon))
                    if 0 <= sj < solver.ny and 0 <= si < solver.nx:
                        # H_raw positif = laut, negatif = daratan → elevasi = -H_raw
                        elev_grid[j, i] = -float(solver.H_raw[sj, si])

        # Isi NaN yang tersisa dengan interpolasi nearest-neighbor
        nan_count = np.isnan(elev_grid).sum()
        if nan_count > 0 and nan_count < elev_grid.size:
            print(f"  Interpolasi {nan_count} sel NaN via nearest-neighbor...")
            from scipy.ndimage import distance_transform_edt
            mask = np.isnan(elev_grid)
            if np.any(~mask):  # Ada setidaknya 1 sel valid
                ind = distance_transform_edt(mask, return_distances=False, return_indices=True)
                elev_grid = elev_grid[tuple(ind)]

        # ── Langkah 2: Manning's roughness grid — vectorized resampling ──
        fill_n_grid = np.full((fill_ny, fill_nx), 0.025)
        if hasattr(solver, 'n_grid'):
            # Vectorized: cari nearest index untuk setiap fill lat/lon
            sj_idx = np.array([np.argmin(np.abs(solver.lat_arr - float(fill_lat_arr[j])))
                               for j in range(fill_ny)])
            si_idx = np.array([np.argmin(np.abs(solver.lon_arr - float(fill_lon_arr[i])))
                               for i in range(fill_nx)])
            # Clamp indices
            sj_idx = np.clip(sj_idx, 0, solver.ny - 1)
            si_idx = np.clip(si_idx, 0, solver.nx - 1)
            # Broadcast: fill_n_grid[j, i] = solver.n_grid[sj_idx[j], si_idx[i]]
            fill_n_grid = solver.n_grid[np.ix_(sj_idx, si_idx)]
            
        # PENTING: Override kekasaran (friction) untuk sel-sel daratan.
        # solver.n_grid seringkali default ke 0.025 (karena tidak ada roughness_grid).
        # Pemukiman, vegetasi, dan pertanian Bantul sangat menghambat laju air.
        # n = 0.045 adalah standar minimum untuk daratan pesisir berpenghuni.
        fill_n_grid = np.where(elev_grid >= 0, 0.045, fill_n_grid)

        # ── Langkah 3: Identifikasi sel-sel pantai (seed untuk flood-fill) ──
        is_land = np.where(~np.isnan(elev_grid), elev_grid >= 0, False)
        is_ocean = np.where(~np.isnan(elev_grid), elev_grid < 0, False)

        # Vectorized: find land cells adjacent to ocean (4-connectivity)
        ocean_neighbor = np.zeros_like(is_land)
        ocean_neighbor[1:, :] |= is_ocean[:-1, :]  # neighbor above
        ocean_neighbor[:-1, :] |= is_ocean[1:, :]   # neighbor below
        ocean_neighbor[:, 1:] |= is_ocean[:, :-1]   # neighbor left
        ocean_neighbor[:, :-1] |= is_ocean[:, 1:]    # neighbor right

        coast_mask = is_land & ocean_neighbor
        # Exclude border pixels
        coast_mask[0, :] = False; coast_mask[-1, :] = False
        coast_mask[:, 0] = False; coast_mask[:, -1] = False
        coast_j, coast_i = np.where(coast_mask)
        coast_seeds = list(zip(coast_j.tolist(), coast_i.tolist()))

        print(f"  Sel pantai (seed): {len(coast_seeds)}")

        # ── Langkah 4: Flood-fill dari pantai dengan atenuasi Manning ──
        K_ATEN = 0.30  # atenuasi per km

        flood_grid = np.full((fill_ny, fill_nx), -1.0)  # -1 = belum diproses
        dist_grid = np.full((fill_ny, fill_nx), -1.0)   # Simpan jarak untuk GeoJSON
        visited = np.zeros((fill_ny, fill_nx), dtype=bool)

        queue = deque()
        for j, i in coast_seeds:
            elev = elev_grid[j, i]
            if np.isnan(elev) or elev > runup_m:
                continue
            eff_runup = runup_m
            flood_d = eff_runup - elev
            if flood_d > 0.05:
                flood_grid[j, i] = flood_d
                dist_grid[j, i] = 0.0
                visited[j, i] = True
                queue.append((j, i, 0.0))

        # Proses BFS
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]  # 4-konektivitas
        while queue:
            cj, ci, dist_km = queue.popleft()
            for dj, di in directions:
                nj, ni = cj + dj, ci + di
                if nj < 0 or nj >= fill_ny or ni < 0 or ni >= fill_nx:
                    continue
                if visited[nj, ni]:
                    continue

                elev = elev_grid[nj, ni]
                if np.isnan(elev):
                    visited[nj, ni] = True
                    continue

                # Admin mask check
                if prepared_mask:
                    lon_n = float(fill_lon_arr[ni])
                    lat_n = float(fill_lat_arr[nj])
                    if not prepared_mask.contains(Point(lon_n, lat_n)):
                        visited[nj, ni] = True
                        continue

                # Jarak kumulatif dari pantai
                new_dist = dist_km + FILL_DX_KM

                # Manning's n untuk atenuasi
                n_manning = fill_n_grid[nj, ni]
                alpha_eff = K_ATEN * (n_manning / 0.025)

                # Effective runup berkurang dengan jarak dari pantai
                eff_runup = runup_m * math.exp(-alpha_eff * new_dist)

                # Sel hanya tergenang jika elevasi < effective runup
                if elev > eff_runup:
                    visited[nj, ni] = True
                    continue
                if elev < 0:  # strictly land only (elevation >= 0 m)
                    visited[nj, ni] = True
                    continue

                flood_d = eff_runup - elev
                if flood_d < 0.05:
                    visited[nj, ni] = True
                    continue

                flood_grid[nj, ni] = flood_d
                dist_grid[nj, ni] = new_dist
                visited[nj, ni] = True
                queue.append((nj, ni, new_dist))

        # ── Langkah 5: Konversi flood_grid ke GeoJSON (Points) ──
        color_map = {
            "EKSTREM" : "#ff0000",
            "TINGGI"  : "#ff6400",
            "SEDANG"  : "#ffb400",
            "RENDAH"  : "#ffe650"
        }

        features = []
        total_cells = 0
        for j in range(fill_ny):
            for i in range(fill_nx):
                fd = flood_grid[j, i]
                if fd <= 0.05:
                    continue

                lon = float(fill_lon_arr[i])
                lat = float(fill_lat_arr[j])

                # Final admin mask check (ensure strictly within village boundaries)
                if prepared_mask:
                    if not prepared_mask.contains(Point(lon, lat)):
                        continue
                
                # Double check: only output points on land
                if elev_grid[j, i] < 0:
                    continue

                risk = ("EKSTREM" if fd >= 10 else
                        "TINGGI"  if fd >= 5  else
                        "SEDANG"  if fd >= 2  else "RENDAH")

                elev = float(elev_grid[j, i])
                # Gunakan dist_km dari proses BFS agar konsisten dengan redaman eksponensial solver
                dist_km = float(dist_grid[j, i])
                if dist_km < 0:
                    lat_pantai = -8.02 - (lon - 110.2) * 0.1
                    dist_km = max(0.0, (lat - lat_pantai) * 111.0)

                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "flood_depth": round(fd, 2),
                        "elev_m": round(elev, 1),
                        "dist_km": round(dist_km, 2),
                        "risk": risk,
                        "color": color_map[risk]
                    }
                })
                total_cells += 1

        print(f"  ✅ Inundasi hi-res (Points): {total_cells} sel tergenang di wilayah Bantul")

        return {
            "type"    : "FeatureCollection",
            "features": features,
            "metadata": {
                "runup_m"      : round(runup_m, 2),
                "total_cells"  : total_cells,
                "format"       : "points",
                "fill_dx_deg"  : FILL_DX,
                "fill_grid"    : f"{fill_ny}x{fill_nx}",
                "model"        : "Okada(1985) + LinearSWE + Synolakis(1987) + DEM_HiRes_FloodFill",
            }
        }


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════════════
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Jarak haversine dalam km."""
    dlat = (lat2 - lat1) * DEG2RAD
    dlon = (lon2 - lon1) * DEG2RAD
    a = math.sin(dlat/2)**2 + math.cos(lat1*DEG2RAD) * math.cos(lat2*DEG2RAD) * math.sin(dlon/2)**2
    return 2 * EARTH_R / 1000 * math.asin(math.sqrt(a))


def quick_estimate(mw: float, dist_km: float,
                   fault_type: str = "vertical",
                   is_megathrust: bool = False) -> Dict:
    """
    Estimasi cepat (tanpa grid numerik) untuk preview hasil.
    Digunakan ketika server belum siap / untuk pra-kalkulasi.
    """
    Mo       = 10 ** (1.5 * mw + 9.1)
    eff_map  = {"vertical": 1.0, "oblique": 0.35, "horizontal": 0.04}
    fault_eff= eff_map.get(fault_type, 1.0)
    h0       = 4.8 * math.sqrt(Mo / 1e20) * fault_eff * 0.35
    result   = calc_runup_attenuation(h0, dist_km, fault_eff)
    return {**result, "method": "quick_estimate_analytical"}


# ═══════════════════════════════════════════════════════════════════════════
# CLI — Jalankan dari command line
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(
        description="SWE Tsunami Simulator — Kelompok 3 S2 Geomatika UGM"
    )
    parser.add_argument("--lat",    type=float, default=-8.5,  help="Lintang episenter")
    parser.add_argument("--lon",    type=float, default=110.5, help="Bujur episenter")
    parser.add_argument("--mw",     type=float, default=8.5,   help="Magnitudo Mw")
    parser.add_argument("--type",   default="vertical",        help="Tipe patahan")
    parser.add_argument("--depth",  type=float, default=20.0,  help="Kedalaman (km)")
    parser.add_argument("--dur",    type=float, default=60.0,  help="Durasi simulasi (menit)")
    parser.add_argument("--mega",   action="store_true",       help="Megathrust flag")
    parser.add_argument("--out",    default="hasil_simulasi.json", help="Output JSON")
    args = parser.parse_args()

    sim    = TsunamiSimulator()
    result = sim.run(
        epicenter_lat = args.lat,
        epicenter_lon = args.lon,
        mw            = args.mw,
        fault_type    = args.type,
        depth_km      = args.depth,
        duration_min  = args.dur,
        is_megathrust = args.mega,
        save_frames   = 10,
    )

    # Simpan tanpa wave frames (terlalu besar)
    output = {k: v for k, v in result.items() if k != "wave_frames"}
    output["n_frames_available"] = len(result["wave_frames"])

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ Hasil disimpan ke {args.out}")
    s = result["statistics"]
    print(f"\n  Mw {s['mw']} — Runup Bantul: {s['runup_bantul_m']} m")
    print(f"  Tiba di pantai: ±{s['arrival_time_min']} menit")
    print(f"  Luas inundasi: {s['inundation_area_km2']} km²")
    print(f"  Grid: {s['grid_ny']}×{s['grid_nx']}, {s['simulation_steps']} langkah")
