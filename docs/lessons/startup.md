# Startup Lessons Learned

Issues hit during first bring-up of the 6-container atomic mode stack (2026-04-22).

---

## 1. Infisical requires Redis

**Problem:** `infisical/infisical:latest` crashed on startup with:
```
Either REDIS_URL, REDIS_SENTINEL_HOSTS or REDIS_CLUSTER_HOSTS must be defined.
```
The Infisical self-host docs mention Redis but the compose example we started from omitted it.

**Fix:** Added `redis:7-alpine` service to `docker-compose.yml` on `data-net`, and `REDIS_URL: redis://redis:6379` to the infisical environment block.

**TODO:** Add Redis to the docker-compose planning checklist for self-hosted Infisical.

---

## 2. `data-net: internal: true` blocks port publishing

**Problem:** `localhost:8888` returned ERR_CONNECTION_REFUSED even though Infisical was running. The PORTS column showed `8080/tcp` with no host binding.

**Root cause:** Docker `internal: true` networks have no external routing — port publishing is silently ignored.

**Fix:** Added `admin-net` (a normal bridge network) and attached Infisical to both `data-net` (for db/redis access) and `admin-net` (for host port binding). The `8888:8080` mapping then works.

**TODO:** Document in the architecture notes that `internal: true` blocks all host port bindings. Any service that needs a management UI must be on a non-internal network.

---

## 3. `INFISICAL_ENCRYPTION_KEY` must be exactly 32 characters

**Problem:** Infisical crashed with `Invalid key length` (Node.js crypto error). We had generated a 64-character hex string (32 bytes encoded as hex).

**Root cause:** Infisical passes the key directly to Node's `createCipheriv` as a UTF-8 string, which requires exactly 32 bytes (32 ASCII characters) for AES-256.

**Fix:** Replaced with a 32-character alphanumeric string.

**TODO:** Update `.env` template comment to say "exactly 32 characters (not hex)" and add an example format.

---

## 4. `comms` and `bot` both need `INFISICAL_URL` pointing to the local container

**Problem:** Both `comms` and `bot` defaulted to `https://app.infisical.com` (Infisical Cloud) when no `site_url` was set, so all secret fetches failed silently.

**Root cause:** `InfisicalClient(ClientSettings(...))` defaults to cloud. No `site_url` was passed in either service.

**Fix:**
- Added `INFISICAL_URL: http://infisical:8080` to both `bot` and `comms` in `docker-compose.yml`
- Both services now read `INFISICAL_URL` from env and pass it as `site_url` to `ClientSettings`
- Added `SLACK_OWNER_CHANNEL` as a required Infisical secret — this is the Slack channel ID where egress approval messages are sent

**TODO:** Any new service using Infisical must pass `site_url` from `INFISICAL_URL` env var. Never rely on the default (it points to cloud).

---

## 5. Old stale containers block `docker compose up`

**Problem:** `docker compose up` failed with:
```
Conflict. The container name "/napyclaw-db-1" is already in use.
```

**Root cause:** Previous partial runs left stopped containers behind with conflicting names.

**Fix:** `docker compose down --remove-orphans` clears all stale containers before bringing the stack up fresh.

**TODO:** Add to the README startup instructions: run `docker compose down` first if you've previously started any services.

---

## 6. `comms` was reading Slack tokens from env vars, not Infisical

**Problem:** `comms` had `SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}` in docker-compose, requiring the tokens to be in `.env`. That defeats the purpose of self-hosted Infisical.

**Root cause:** `comms` was designed to read tokens from environment variables directly. Only the `bot` container had Infisical integration.

**Fix:** Added Infisical client to `comms/main.py` as a FastAPI lifespan handler — loads `SLACK_BOT_TOKEN` and `SLACK_OWNER_CHANNEL` from Infisical at startup. Removed `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` from docker-compose env substitution. Added `data-net` to `comms` networks so it can reach the `infisical` container.

**TODO:** Any future service that needs secrets should use the same Infisical lifespan pattern, not env var substitution in docker-compose.

---

## 7. Infisical Python SDK uses `secret_value` not `secretValue`

**Problem:** Config loaded zero secrets despite the SDK connecting successfully. `_load_infisical()` returned `{}` for all secrets.

**Root cause:** The `infisical-python>=2.1` SDK returns a `GetSecretResponseSecret` dataclass with `secret_value` (snake_case), not `secretValue` (camelCase). The bare `except: pass` in the fetch loop was silently swallowing `AttributeError`. Same bug existed in `comms/main.py`.

**Fix:** Changed all `val.secretValue` → `val.secret_value` in `config.py` and `comms/main.py`. Also added explicit error logging to the fetch loop to surface future SDK changes.

**TODO:** Never use bare `except: pass` when fetching secrets — at minimum log the exception so silent failures are visible.

---

---

## 8. `docker compose up <service>` without `--no-deps` recreates dependent containers

**Problem:** Running `docker compose up -d comms-tailscale` (without `--no-deps`) caused Docker to recreate `db`, `redis`, and `infisical` because they are listed as dependencies. Recreating the Infisical container wiped its database state, invalidating all machine identity credentials. Every container that talks to Infisical then failed with "Invalid credentials" and had to be re-bootstrapped.

**Root cause:** `docker compose up` without `--no-deps` recreates the full dependency graph if any dependent image has changed. When `db` is recreated, Infisical loses all stored state (machine identities, projects, secrets).

**Fix:** Always use `--no-deps` when restarting a single service in a running stack:
```bash
docker compose up -d --no-deps <service>
```

**TODO:** Add a warning to the README: never run `docker compose up` without `--no-deps` on a running stack unless you intend a full teardown/rebuild.

---

## 9. Restarting `comms` orphans the `comms-tailscale` sidecar

**Problem:** After `docker compose up -d --no-deps comms`, the Tailscale IP became unreachable (connection timed out).

**Root cause:** `comms-tailscale` uses `network_mode: service:comms`, meaning it shares comms' network namespace. When comms is recreated, its network namespace is replaced — the sidecar's `tailscaled` process is left attached to the dead old namespace.

**Fix:** Always restart both together:
```bash
docker compose up -d --no-deps comms && docker compose up -d --no-deps comms-tailscale
```
Added a healthcheck to `comms` and `depends_on: comms: condition: service_healthy` on the sidecar so the dependency is explicit at cold start.

**TODO:** Any service using `network_mode: service:<X>` must be restarted whenever `<X>` is restarted.

---

## Open TODOs

- [x] Update `.env` comment: `INFISICAL_ENCRYPTION_KEY` must be exactly 32 characters (not hex)
- [x] Add `SLACK_OWNER_CHANNEL` to Infisical secrets checklist in the README
- [x] Add note to setup instructions: add secrets to the `prod` environment in Infisical (not `dev`)
- [x] Add Redis to docker-compose documentation/comments
- [x] Document `admin-net` purpose in docker-compose comments
- [x] README startup steps: add `docker compose down` before first full `up`
- [x] Any new service needing secrets: use the Infisical lifespan pattern from `comms/main.py`, not docker-compose env substitution
- [ ] README: warn never to run `docker compose up` without `--no-deps` on a running stack
