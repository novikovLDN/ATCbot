# ─── Stage 1 — build the React admin dashboard ────────────────────────
FROM node:20-alpine AS dashboard-build

WORKDIR /build/dashboard

# Cache deps separately from source to speed up incremental builds.
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund --silent

COPY dashboard/ ./
RUN npm run build

# ─── Stage 2 — Python bot runtime + bundled dashboard/dist ────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# SECURITY: Create non-root user before copying files (principle of least privilege)
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home appuser

WORKDIR /app

COPY --chown=appuser:appuser requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

# Explicit copy so migrations are never excluded by build context or future .dockerignore
COPY --chown=appuser:appuser migrations/ ./migrations/

# Bring the built SPA into the image at the path FastAPI mounts from
# (app/api/__init__.py: dashboard/dist via StaticFiles).
COPY --from=dashboard-build --chown=appuser:appuser /build/dashboard/dist ./dashboard/dist

USER appuser

CMD ["python", "main.py"]
