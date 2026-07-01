# ─── Stage 1 — build the React admin dashboard ────────────────────────
FROM node:26-alpine AS dashboard-build

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

# Node.js — required by app/services/incy_crypto.py which shells out to
# `node scripts/incy_encode.mjs` for the @incy/link-encoder package.
# Without Node the bot still runs, but the "Open in Incy" button is
# silently hidden (see incy_crypto._disabled). Add it now so the button
# actually works in prod.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y --auto-remove curl gnupg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# SECURITY: Create non-root user before copying files (principle of least privilege)
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home appuser

WORKDIR /app

COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# npm install BEFORE the full code copy so docker cache layers
# only invalidate when package.json / package-lock.json change.
COPY --chown=appuser:appuser package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund

COPY --chown=appuser:appuser . .

# Explicit copy so migrations are never excluded by build context or future .dockerignore
COPY --chown=appuser:appuser migrations/ ./migrations/

# Bring the built SPA into the image at the path FastAPI mounts from
# (app/api/__init__.py: dashboard/dist via StaticFiles).
COPY --from=dashboard-build --chown=appuser:appuser /build/dashboard/dist ./dashboard/dist

USER appuser

CMD ["python", "main.py"]
