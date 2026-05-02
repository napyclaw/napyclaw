# Handoff: Specialist Memory, Onboarding, and Backstage UI

**Date:** 2026-04-26
**For:** Implementing agent picking up this plan cold
**Plan:** `docs/superpowers/plans/2026-04-26-specialist-memory.md`
**Spec:** `docs/superpowers/specs/2026-04-25-specialist-memory-design.md`
**Issues:** [#9](https://github.com/napyclaw/napyclaw/issues/9), [#10](https://github.com/napyclaw/napyclaw/issues/10)

---

## What this builds

napyclaw is a multi-specialist AI agent framework with a webchat UI. Today, each specialist has a thin system prompt (just a name), no structured working memory, and stores every conversation exchange into a vector DB with no filter.

This work adds:

1. **Specialist onboarding** — when a new specialist is created, the agent collaboratively defines its role with the user before doing any work
2. **Structured working memory** (`specialist_memory` table) — typed entries (responsibility, task, tool, resource, preference, fact) retrieved semantically per turn
3. **Layered system prompt** (`PromptBuilder` module) — five priority blocks replacing the current thin one-liner
4. **Background summarizer** — captures what's leaving the conversation window into `thoughts`, filtered and routed by trust tier
5. **User identity** — Tailscale login name extracted and shown in the UI, passed to the bot as `owner_name`
6. **Backstage column** — third panel in the webchat UI showing live context, memory activity, and pending approvals without interrupting the chat

---

## Current codebase state

The stack is fully running on Docker. Key facts:

- **Bot** (`napyclaw/`): Python asyncio agent framework. Entry point `napyclaw/__main__.py`. Core logic in `napyclaw/app.py` (`NapyClaw` class, `GroupContext` dataclass). Agent loop in `napyclaw/agent.py`.
- **Comms** (`services/comms/`): FastAPI service. Receives messages from the webchat frontend over WebSocket, forwards to the bot via HTTP webhook (`/inbound`). Sends bot responses back to the frontend over the same WS. Static files served from `services/comms/static/`.
- **DB**: PostgreSQL with pgvector. Migrations in `napyclaw/migrations/`. Migrations 001–003 are applied. Migration 004 (this work) is not yet applied.
- **Vector memory**: Ollama embeddings, `thoughts` table (768-dim), per-specialist via `group_id`.
- **WebSocket**: Single active connection model — one WS at a time in `comms/main.py`. `_ws_connection` is a module-level global.

The webchat UI is currently two-panel: Specialists sidebar + Chat. The Backstage column does not exist yet.

---

## Key design decisions and why

**No explicit onboarding mode flag.** Onboarding is triggered purely by `job_description is None` in the system prompt Block 1. The agent reads the instruction and behaves accordingly. No state machine, no separate DB column. Keeps the flow natural — the agent can revisit onboarding language at any time.

**Trust tiers, not a single policy.** `responsibility` and `job_description` changes require user confirmation before any DB write (ask-first). Everything else (`task`, `tool`, `resource`, `preference`, `fact`) writes immediately and notifies in the Backstage sticky area with a 3-turn correction window. This was a deliberate tradeoff: high-stakes identity changes require approval, lower-stakes context is autonomous with a short review window.

**Correction window = 3 turns, hardcoded.** Not configurable yet. The user is on a mobile keyboard and typos are common — 3 turns gives enough time to say "that's wrong" naturally.

**Adjust and Exclude are Backstage-only actions.** Clicking either never posts anything to the chat column. This is the "btw" interaction pattern — sidebar corrections that don't interrupt the conversation.

**`specialist_memory` is per-specialist only.** No global pool yet. Cross-specialist access control (global, roles, manager tiers) is deferred to a future design. `group_id` is the only access mechanism. Do not add a `visibility` column.

**`thoughts` table unchanged.** The background summarizer writes to `thoughts` (episodic, per-specialist). `specialist_memory` is for structured working context. These are separate concerns with different retrieval patterns.

**Summarizer fires on prune threshold, not every turn.** Only triggered when `len(history) // 2 > verbatim_turns + summary_turns` (default 7+5=12). This means roughly every 5-8 turns. The summarizer runs as a non-blocking `asyncio.create_task` after the response is sent — zero added latency to the user.

**Summarizer uses the same model as the specialist.** No separate config. Tunable later.

**`PromptBuilder` is a standalone module.** `app.py` calls `build()` and knows nothing about block internals. Block order (earlier = higher LLM attention): IDENTITY → RESPONSIBILITIES → WORKING CONTEXT → EPISODIC MEMORY → PRUNED HISTORY. Markdown default, JSON available for benchmarking.

**Bot notifies comms via POST to `/backstage/event`.** Comms then pushes to the active WebSocket. The bot does not have a direct WS connection — it communicates with the frontend only through comms.

---

## Critical gotchas

- **Do not leave two `save_to_memory` tools registered.** The existing `SaveToMemoryTool` in `napyclaw/tools/memory_tool.py` must be superseded by the new one in `specialist_tools.py`. If both are registered, the agent will have duplicate tool names.

- **`search_specialist_memory` uses raw `<=>` operator, not a stored function.** The `thoughts` table uses a `match_thoughts()` SQL function. Do not create a similar function for `specialist_memory` — use inline SQL with `<=>` in `db.py`.

- **Egress approval backend is unchanged.** The existing `/notify/approval` and `/approval/respond` endpoints in `comms/main.py` are not touched. Only the frontend rendering changes — egress approvals move into the Backstage sticky area visually (Task 9), but the backend flow is identical to today.

- **`verbatim_turns`/`summary_turns` NULL handling.** Existing `group_contexts` rows before migration 004 will have NULL for these columns. `_row_to_ctx` must fall back to defaults (7 and 5) rather than passing NULL to `GroupContext`.

- **`owner_name` empty string when Tailscale header absent.** In local dev, the `Tailscale-User-Name` header won't be present. The `/identity` endpoint returns `""` — do not hardcode a fallback name like "the user". The system prompt handles the empty case gracefully.

- **`comms-tailscale` must be restarted with comms.** `comms-tailscale` uses `network_mode: service:comms` and shares comms' network namespace. Whenever comms is rebuilt, restart both:
  ```bash
  docker compose up -d --no-deps comms && docker compose up -d --no-deps comms-tailscale
  ```

- **Always use `--no-deps` when restarting a single service.** Omitting it causes Docker to recreate `db` and `infisical`, wiping all machine identity credentials.

---

## How to run tests

Unit tests (no Postgres required):
```bash
cd /c/Users/NathanKaemingk/source/napyclaw
.venv/Scripts/python -m pytest tests/ --ignore=tests/test_db.py -v
```

DB integration tests (require running stack):
```bash
.venv/Scripts/python -m pytest tests/test_db.py -v
```

Run the stack:
```bash
docker compose up -d
```

Check bot logs:
```bash
docker compose logs -f bot
```

---

## Files you will touch

| File | What changes |
|------|-------------|
| `napyclaw/migrations/004_specialist_memory.sql` | New — migration |
| `napyclaw/db.py` | New CRUD methods + updated save/load signatures |
| `napyclaw/prompt_builder.py` | New module |
| `napyclaw/tools/specialist_tools.py` | New tools (supersedes `memory_tool.py`) |
| `napyclaw/summarizer.py` | New module |
| `napyclaw/app.py` | Wire everything — GroupContext, PromptBuilder, summarizer, tools |
| `services/comms/main.py` | Identity endpoint, backstage event endpoint, WS handlers |
| `services/comms/static/index.html` | Three-panel layout, Backstage column |
| `tests/test_prompt_builder.py` | New |
| `tests/test_specialist_tools.py` | New |
| `tests/test_summarizer.py` | New |
| `tests/test_db.py` | Add specialist_memory + new column tests |
| `tests/test_app.py` | Update `_make_context`, `_FakeDB` for new fields |

---

## Where to start

Read the plan (`2026-04-26-specialist-memory.md`), then start at **Task 1**. Each task has a **Spec Reference** — read those spec sections before writing any code for that task. If something in the task conflicts with the spec, follow the spec and surface the conflict rather than guessing.
