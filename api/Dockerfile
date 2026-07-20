# Single image serving both the API and the built frontend.
#
# The frontend calls relative /api/... paths and the session cookie is
# same_site="lax", so both must be served from one origin or login breaks
# silently. Building the frontend into this image guarantees that without
# needing a proxy layer.
#
# Build context is the REPO ROOT (not api/), so both trees are reachable —
# see dockerContext in render.yaml.

# --- frontend build ---
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
# npm ci installs exactly what package-lock.json pins.
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- api ---
FROM python:3.12-slim
WORKDIR /app

# Pinned lockfile. Copied and installed before the source so the layer caches
# across code-only changes.
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .

# app/asgi.py serves this at "/" and mounts the API under "/api".
COPY --from=frontend /build/dist ./static

EXPOSE 8000

# asgi:root, not main:app — main:app is the bare API, used directly by tests
# and by the old nginx-fronted compose setup.
CMD ["uvicorn", "app.asgi:root", "--host", "0.0.0.0", "--port", "8000"]
