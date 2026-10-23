#!/bin/sh
# Single process entrypoint for the `api` service. Picked at runtime from the container
# environment (not baked by compose at parse time) so the same compose file serves dev and
# prod, and `docker compose run -e ENVIRONMENT=production` switches modes without edits.
#
# - development: uvicorn with auto-reload (fast local iteration).
# - production:  a single stable uvicorn worker with --proxy-headers so a TLS-terminating
#                reverse proxy's X-Forwarded-* headers are trusted.
set -eu

if [ "${ENVIRONMENT:-development}" = "production" ]; then
  # No --reload in prod. --limit-max-requests restarts each worker after N requests to shed
  # accumulated memory (CPU inference + model caches can leak across requests); pair with a
  # load balancer doing health checks so restarts are transparent. --limit-concurrency caps
  # in-flight connections so a slow-client/LLM storm can't exhaust the pool, and
  # --timeout-graceful-shutdown lets in-flight requests finish on SIGV/TERM.
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers \
    --workers "${API_WORKERS:-1}" \
    --limit-concurrency "${API_LIMIT_CONCURRENCY:-200}" \
    --limit-max-requests "${API_LIMIT_MAX_REQUESTS:-1000}" \
    --timeout-graceful-shutdown "${API_GRACEFUL_SHUTDOWN:-30}"
else
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --reload
fi
