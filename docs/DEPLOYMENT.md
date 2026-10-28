# Deployment guide

The Compose stack has been run and tested locally, including through nginx with three workers. This
guide covers putting it on a public host, which **has not been done yet**.

Target: one Linux VM (any cloud, or any host that runs Docker Compose) with TLS in front.

## 1. Host
- Provision a small VM (2 vCPU / 2–4 GB is plenty for a demo), install Docker Engine and the Compose
  plugin, and open only ports 22, 80 and 443.
- `git clone` the repo, then `cp .env.example .env` and set:
  - `JWT_SECRET`: `openssl rand -hex 32`
  - `POSTGRES_PASSWORD`: `openssl rand -hex 16`
  - `METRICS_TOKEN` if Prometheus will scrape from outside the Compose network
  - Never commit `.env`.

## 2. Start
```bash
docker compose up -d --build --scale worker=2 --wait
curl -fsS localhost:8080/healthz
```
The API container runs `alembic upgrade head` before it serves. Workers wait for the API to be healthy,
so migrations always run first. In prod mode the app refuses to start without a strong `JWT_SECRET` or
with SQLite.

## 3. TLS
Put Caddy (automatic Let's Encrypt) on the host in front of the `web` container:
```
robotops.example.com {
    reverse_proxy localhost:8080
}
```
nginx serves the SPA and proxies `/api`, so the browser sees one origin and `CORS_ORIGINS` stays empty.
Devices also use `https://robotops.example.com/api/v1/ingest/events`.

`/metrics` and `/readyz` are **not** proxied by nginx. They are only reachable inside the Compose network
or on the host.

## 4. Verify
```bash
curl https://robotops.example.com/healthz
E2E_BASE_URL=https://robotops.example.com npx playwright test     # from frontend/, uses fresh accounts
python scripts/worker_chaos.py --url https://robotops.example.com   # multi-worker check
```

## Operations
- **Scaling workers:** `docker compose up -d --scale worker=N`. Claims are atomic, and a crashed worker's
  job is re-queued after `WORKER_LEASE_SECONDS` (default 60).
- **Monitoring:** `docker compose --profile monitoring up -d` starts Prometheus on `127.0.0.1:9090`
  (use an SSH tunnel). The alert rules are in `monitoring/alerts.yml`: API down, 5xx above 5%, queue
  stalled, p95 latency over 1 s. Wire Alertmanager or a hosted Prometheus to receive them.
- **Readiness vs. liveness:** container healthchecks use `/healthz` (process alive). Load balancers should
  use `/readyz`, which returns 503 if PostgreSQL is unreachable.
- **Migrations:** change the models, run `alembic revision --autogenerate -m msg`, review the result and
  deploy (it is applied at container start). Roll back with
  `docker compose exec api alembic downgrade -1`.
- **Backups:** schedule `docker compose exec db pg_dump -U robotops robotops > backup-$(date +%F).sql` and
  test restores. A restore into a fresh database was verified earlier.
- **Device keys:** rotate from the device page. The old key fails immediately, and agents exit with a
  clear error instead of retrying.
- **Logs:** JSON lines on stdout with a request ID (`X-Request-ID` is echoed back). Passwords, tokens and
  device keys are never logged.

## Known security limits
The JWT lives in browser localStorage, which is exposed to XSS. There is no refresh or revocation, no email
verification and no password reset. Device keys are bearer secrets, so use TLS. No compliance claims.
