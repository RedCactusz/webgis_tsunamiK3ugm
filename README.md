# WebGIS Tsunami - Next.js Migration

Sistem informasi geografis untuk simulasi bencana tsunami di Bantul, Yogyakarta.

## 🏗️ Architecture

Monorepo dengan:
- **Frontend**: Next.js 14 (TypeScript, Tailwind CSS, Leaflet)
- **Backend**: Python FastAPI dengan SWE solver & ABM evacuation
- **State Management**: Zustand + TanStack Query
- **Development**: Docker Compose

## 📁 Structure

```
webgis-tsunami/
├── apps/
│   ├── web/          # Next.js Frontend (Port 3000)
│   └── api/          # Python FastAPI Backend (Port 8000)
├── docker-compose.yml
└── package.json
```

## 🚀 Quick Start

### Prerequisites
- Node.js 18+
- Python 3.10+
- Docker & Docker Compose (optional)

### Development (Docker - Recommended)

```bash
# Install dependencies
npm install

# Start all services
docker-compose up

# Frontend: http://localhost:3000
# Backend: http://localhost:8000
```

### Development (Manual)

```bash
# Terminal 1 - Frontend
cd apps/web
npm install
npm run dev

# Terminal 2 - Backend
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Environment Variables

Create `apps/web/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 🛠️ Tech Stack

### Frontend
- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS v4
- Leaflet (React-Leaflet)
- Zustand (State)
- TanStack Query (Server State)

### Backend
- FastAPI
- Python 3.10+
- rasterio, geopandas, shapely
- numpy, scipy
- SWE Solver (Shallow Water Equation)
- ABM Evacuation (Agent-Based Model)

## 📦 Features

- [x] Interactive map with Leaflet
- [x] Server status monitoring
- [x] Layer management
- [ ] Tsunami simulation (in progress)
- [ ] Depth probe
- [ ] Evacuation ABM
- [ ] Chart visualization

## 🔧 Development

### Build for Production

```bash
# Build Next.js
npm run build

# Build Docker images
docker-compose build
```

### Testing

```bash
# Frontend tests
cd apps/web && npm test

# Backend tests
cd apps/api && pytest
```

## 📝 Migration Notes

Project ini merupakan migrasi dari single-file HTML (6.5MB) ke arsitektur modern yang modular dan maintainable.

### Original Structure
- `index.html` (6619 lines) → Next.js components
- `server.py` → FastAPI dengan modular structure
- Inline CSS/JS → Separate files dengan TypeScript

### New Benefits
- ✅ Modular & maintainable
- ✅ Type safety dengan TypeScript
- ✅ Modern React patterns
- ✅ Better developer experience
- ✅ Easy deployment

## 📄 License

Tugas Kelompok - S2 Geomatika UGM

## 👥 Team

- Kelompok KomGeo - Pemodelan Tsunami
- Migration: Claude Code + Team
