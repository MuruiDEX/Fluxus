# backend/Dockerfile — production FastAPI image (Stage 17).
#
# Build (from repo root):
#   docker build -f backend/Dockerfile -t fluxus-api:0.1.0 ./backend
# Standalone run (host publish limited to loopback; a reverse proxy terminates TLS):
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file backend.env fluxus-api:0.1.0
#
# No secrets are baked in. All APP_* settings arrive at runtime via
# environment (or secret files mounted by compose). requirements.txt records
# compatible ranges, not a hash-locked resolution: capture an exact lock
# before claiming reproducible production builds.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv/app

FROM base AS deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS runner
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# scripts/ ships the smoke check + owner bootstrap for operators; fixture
# writers (seed_demo/e2e_live_setup/repro) are guarded by script_safety and
# refuse prod, but they are intentionally EXCLUDED from the image so a
# production container cannot even import them by accident.
COPY scripts/smoke_check.py ./scripts/smoke_check.py
COPY scripts/bootstrap_owner.py ./scripts/bootstrap_owner.py
# Non-root runtime user.
RUN addgroup --system --gid 1001 appuser \
 && adduser --system --uid 1001 --gid 1001 appuser \
 && chown -R appuser:appuser /srv/app
USER appuser
EXPOSE 8000
# Liveness via the dependency-free endpoint (readiness gates traffic, not restarts).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"
# Container networking requires 0.0.0.0: 127.0.0.1 is only reachable inside
# this container and would make Caddy's `reverse_proxy backend:8000` fail.
# No port is published; only the internal Docker network reaches this socket.
# Single worker: the login throttle is process-local (see app/core/rate_limit.py).
# Do NOT add --workers > 1 without moving the throttle to a shared store.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
