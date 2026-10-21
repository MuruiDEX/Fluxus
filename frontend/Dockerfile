# frontend/Dockerfile — production Next.js image (Stage 17).
#
# Build FROM THE REPO ROOT (context must include docs/contracts/, which
# lib/wire-manifest.test.ts imports — see frontend/tsconfig include note):
#   docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_APP_MODE=demo `
#     -t fluxus-web:demo-stage17 .
# LIVE variant:
#   docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_APP_MODE=live `
#     --build-arg NEXT_PUBLIC_API_URL=https://app.example.com -t fluxus-web:live .
#
# NEXT_PUBLIC_* values are baked at build time by design (Next.js inlines
# them into the client bundle). Never pass secrets as build args — only the
# public mode + public API origin belong here.

ARG NODE_VERSION=20-alpine

FROM node:${NODE_VERSION} AS deps
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
# Deterministic install from the lockfile; dev deps included for the build.
RUN npm ci

FROM node:${NODE_VERSION} AS builder
WORKDIR /app/frontend
COPY --from=deps /app/frontend/node_modules ./node_modules
# App sources plus the repo-level contract mirror imported by tests.
COPY frontend/ ./
COPY docs ../docs
# Build-time public config only (validated by scripts/build.mjs, fail-fast).
ARG NEXT_PUBLIC_APP_MODE
ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_ALLOW_INSECURE_API=
ENV NEXT_PUBLIC_APP_MODE=${NEXT_PUBLIC_APP_MODE} \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_ALLOW_INSECURE_API=${NEXT_PUBLIC_ALLOW_INSECURE_API}
RUN node scripts/build.mjs

FROM node:${NODE_VERSION} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Non-root runtime user (next.js image pattern).
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs
# Standalone output: minimal server + static assets only.
COPY --from=builder --chown=nextjs:nodejs /app/frontend/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/frontend/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/frontend/public ./public
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
