# SupportAI - Backend

FastAPI application, background ingestion worker, PostgreSQL database (with pgvector) and a
local Ollama model runtime, all defined in this repository's own `docker-compose.yml`.

The frontend lives in a **separate repository** with its own Docker setup. Nothing here builds,
starts or configures it.

## Quick start

From the repository root:

```
python run.py
```

That is the recommended path. It checks Docker, prepares `.env`, builds the image once, starts
Postgres, runs migrations and seeds roles, starts Ollama, pulls the model on first run, then
starts the API and worker and waits until the API reports ready.

Using Compose directly works too, and needs no `.env`:

```
docker compose up --build
```

On Windows PowerShell, tell the backend which frontend origin may call it (CORS):

```
$env:ALLOWED_ORIGINS="http://localhost:3000"; docker compose up --build
```

### `run.py` options

| Command | Effect |
| --- | --- |
| `python run.py` | Start the backend stack and wait until the API is ready |
| `python run.py --rebuild` | Force a fresh image build first (several minutes) |
| `python run.py --skip-model` | Start without pulling the ~4.9 GB Ollama model |
| `python run.py --no-wait` | Return once containers are up, without waiting for readiness |
| `python run.py --status` | Report what is currently running |
| `python run.py --stop` | Stop the stack (volumes are kept) |
| `python run.py --frontend-origin URL` | Override the CORS allowlist origin |

`run.py` lives at the workspace root, one level above this directory, and resolves
`backend/docker-compose.yml` relative to its own location. Move it into this repository and
change `BACKEND` to `ROOT` if the two repositories are split apart.

## Services and ports

| Service | Host port | Container | Purpose |
| --- | --- | --- | --- |
| `api` | 8000 | 8000 | FastAPI application, interactive docs at `/docs` |
| `worker` | - | - | Procrastinate worker, runs document ingestion off the request path |
| `db` | 5434 | 5432 | PostgreSQL 16 with pgvector |
| `ollama` | 11434 | 11434 | Local generation model runtime |
| `init` | - | - | One-shot: migrations, job-queue schema, system roles |
| `model-pull` | - | - | One-shot: pulls the model on first run |

PostgreSQL is published on host port **5434** so a native Postgres on the host can keep using
5432. The container-to-container URLs always use `db:5432`.

There is no Redis. PostgreSQL is both the system of record and the job queue, and rate limiting
is per-process in-memory. See `DECISIONS.md` D002/D008 and `docs/ARCHITECTURE.md`.

## Volumes

| Volume | Holds |
| --- | --- |
| `supportai_pgdata` | Database files |
| `supportai_ollama` | Downloaded Ollama models |
| `supportai_uploads` | Uploaded source documents |
| `supportai_hf_cache` | Embedding and reranker weights (hundreds of MB, cached to avoid re-downloading) |

`./:/app` is bind-mounted into `api` and `worker` for live-reload development.

## Configuration

`docker-compose.yml` defaults every setting with `${VAR:-default}`, so a fresh clone runs with no
`.env` at all. Compose reads a `.env` sitting next to the compose file for those substitutions, so
copying `.env.example` to `.env` and editing it is all the configuration that is normally needed.

Settings that usually need attention:

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | placeholder | Set a real value before anything beyond local development |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Must exactly match the frontend origin; CORS forbids a wildcard for cookie-authenticated requests |
| `OLLAMA_MODEL` | `llama3.1:8b` | Generation model to pull |
| `EMBEDDING_MODEL_NAME` | `BAAI/bge-small-en-v1.5` | Must match `EMBEDDING_DIMENSIONS` (384) |
| `RERANK_MODEL_NAME` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranking model |
| `API_HOST_PORT` | `8000` | Host port for the API |
| `POSTGRES_HOST_PORT` | `5434` | Host port for PostgreSQL |
| `OLLAMA_HOST_PORT` | `11434` | Host port for Ollama |
| `SKIP_MODEL_PULL` | `0` | `1` skips the `model-pull` service |

Docker hosts are used internally (`db`, `ollama`), not `localhost`, so the same values work
whether the API runs in a container or on the host.

## Connecting the frontend

Start the backend first, then, from the frontend repository:

```
API_URL=http://localhost:8000 docker compose up --build
```

The frontend bakes `NEXT_PUBLIC_API_BASE_URL` into the browser bundle at build time, so it must
match where the API is actually reachable.

## Production mode

Production is driven by `ENVIRONMENT=production` in `backend/.env` (plus a real
`JWT_SECRET`). The same compose file is used; the runtime behavior is selected by it.

```dotenv
ENVIRONMENT=production
# A real, long, random value - the app refuses to start in production otherwise:
JWT_SECRET=replace-with-32+-random-chars
# The origin the frontend is actually served from. Cookies are Secure in production, so this
# must be an https origin, and CORS forbids a wildcard because auth uses credentials.
ALLOWED_ORIGINS=https://supportai.example.com
# A non-default DB password. POSTGRES_HOST_PORT stays on the host (5434) so a native Postgres
# can coexist; container-to-container URLs always use db:5432.
POSTGRES_PASSWORD=...
```

Then:

```
python run.py                       # or: docker compose up --build
```

### Production hardening already applied

- `--reload` is **off** in production; `--proxy-headers` is on so a TLS-terminating reverse
  proxy is trusted for `X-Forwarded-*` (scheme, port, client IP).
- A startup guard in `app/main.py` refuses to boot when `ENVIRONMENT=production` and `JWT_SECRET`
  is still the placeholder.
- OpenAPI docs (`/docs`, `/api/openapi.json`) are **disabled in production** (verified: 404).
- Response headers: `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, plus an API `Content-Security-Policy`.
- Refresh-token cookie is `Secure` + `HttpOnly` + `SameSite=Strict` in production.
- Upload size is capped by `MAX_UPLOAD_SIZE_MB` (default 25) in `services/knowledge.py` and
  `services/url_fetch.py`; enforce a hard request-body cap at the reverse proxy too
  (`client_max_body_size`).
- Uvicorn worker limits: `--limit-max-requests 1000` (restarts to shed memory growth from CPU
  inference / model caches), `--limit-concurrency 200`, `--timeout-graceful-shutdown 30`s. Tune
  via `API_WORKERS`, `API_LIMIT_CONCURRENCY`, `API_LIMIT_MAX_REQUESTS`, `API_GRACEFUL_SHUTDOWN`.
- Connection pool: asyncpg pool sized by `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` (defaults 10/10).

For a real deployment put the whole stack behind a reverse proxy (Caddy/Nginx) that terminates
TLS and forwards to `localhost:8000`, and keep `ollama` reachable only from the `api`/`worker`
services, not exposed to the host.

### Scaling limits (important)

This stack is a **single-host self-hosted** backend. Correct and safe for small/medium use, but
it is **not** able to serve millions of *simultaneous* users as-is, and several correct-at-small
scale choices become the bottleneck:

- **The model runs in a single local Ollama instance** (`ollama` service, `llama3.1:8b`). Ollama is
  not horizontally scalable (~1 rps/GPU). For real concurrency, switch to a hosted/turbine provider
  (`AI_GENERATION_PROVIDER=hosted` + `HOSTED_AI_API_KEY`/`HOSTED_AI_BASE_URL`) or a dedicated
  model-serving cluster (vLLM/TGI) behind a router.
- **The rate limiter is in-process** (`app/core/rate_limit.py`). It is correct only for a single
  `api` process; running `API_WORKERS > 1` or multiple `api` replicas resets limits per process —
  externalize to Postgres or Redis first.
- **One uvicorn worker and one procrastinate worker by default.** Scale horizontally with multiple
  `api`/`worker` container replicas behind a load balancer. `WORKER_CONCURRENCY` (default 4) sets
  in-process job parallelism per worker replica.
- **PostgreSQL is a single instance** on a Docker volume. For capacity/HA use a managed database
  (or replicated Postgres) with **PgBouncer** between it and the app.

In short: for a team, `python run.py` behind Caddy/Nginx with TLS is production. For millions of
users, swap the LLM provider, externalize rate limiting, add PgBouncer + a managed database, and
run multiple replicas — this Compose file is the single-host starting point, not that fleet.

### Tests

Tests run in a throwaway container so they never touch the dev database or require the API
to be up:

```
docker compose run --rm --no-deps api pytest -q
```

`--no-deps` skips starting `db`/`init`/`model-pull`/`ollama`/`api` for the test run; it still
needs the `db` service running (the main stack above provides it). Tests use the
`supportai_test` database, which `conftest.py` creates and drops the schema for itself every
session — but the **database itself** must exist first. Create it once with:

```
sh scripts/setup_test_db.sh        # host helper that runs the psql commands against the db service
# or:
docker compose exec -T db psql -U supportai -d supportai -c "CREATE DATABASE supportai_test;"
docker compose exec -T db psql -U supportai -d supportai_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

A database file (`supportai_test`) missing is the only reason the full suite errors with
`InvalidCatalogNameError`; everything else passes.

### Useful commands

Run from this directory:

```
docker compose ps                    service status
docker compose logs -f api worker    follow backend logs
docker compose exec api pytest       backend tests
docker compose exec db psql -U supportai -d supportai
docker compose down                  stop, keep data
docker compose down -v               stop and delete all data
```

## First run notes

The image build installs CPU-only PyTorch and the embedding/reranker models, which takes several
minutes the first time and downloads several hundred MB. The Ollama model pull is a separate
~4.9 GB download; use `--skip-model` to defer it, in which case AI answers fail until you run:

```
docker compose exec ollama ollama pull llama3.1:8b
```