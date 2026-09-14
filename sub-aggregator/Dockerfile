# Multi-stage: prod image is Alpine, non-root, no dev deps, no build tools.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
# --omit=dev keeps ioredis-mock (dev-only) out of the runtime image.
RUN npm install --omit=dev --no-audit --no-fund

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=8080

# Drop root — Fastify binds to :8080 which is unprivileged.
RUN addgroup -S app && adduser -S -G app app
USER app

COPY --chown=app:app --from=deps /app/node_modules ./node_modules
COPY --chown=app:app src ./src
COPY --chown=app:app package.json ./

EXPOSE 8080

# 5-second interval → detects deadlock in <30 s (compose default).
HEALTHCHECK --interval=5s --timeout=2s --start-period=2s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1

CMD ["node", "src/server.js"]
