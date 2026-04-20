# Atomic Mode Architecture — Handoff

**Date:** 2026-04-19
**Branch:** main
**Implementation plan:** `docs/superpowers/plans/2026-04-19-atomic-mode-architecture.md`
**Spec:** `docs/superpowers/specs/2026-04-19-atomic-mode-architecture-design.md`

---

## What we decided and why

napyclaw's stack was almost entirely self-hostable — except Slack and the old single-compose structure that didn't enforce any network isolation. This session designed and specced a full restructure so every component can run on your own hardware with no external service dependencies ("atomic mode").

**Key decisions made:**

**Three external lanes, not one.** Instead of a single outbound path, we split external traffic into three containers each with their own Docker network: `comms` (messaging), `egressguard` (LLM APIs + cloud search), `searxng` (search engines). The bot container has zero direct internet access — enforced at the kernel level by Docker networks.

**EgressGuard as a container, not just in-process.** The existing `egress.py` runs inside the bot process. Moving it to its own container means a compromised bot can't bypass it — the kernel blocks direct outbound. The tradeoff (approval flow complexity) was resolved by routing approval messages through the comms container.

**Comms as a stateless adapter.** Slack today, Mattermost/Matrix later (issue #7). The bot and egressguard both talk to comms via HTTP — neither knows or cares what messaging platform is behind it. Swapping platforms requires no changes to bot or egressguard.

**202 async approval flow.** When egressguard hits an unknown domain, it returns 202 + token immediately rather than blocking the chain. The bot's ToolSystem surfaces "awaiting approval" to the LLM and continues. Primary resolution is an event callback from comms when the user responds. Stepback retry (30s → 60s → 2m → 5m → 10m → 20m) is the safety net for when the user is away or the callback fails.

**Approve once vs approve always.** Two approval variants — one-time exception or permanent allowlist entry. Same for deny. Allowlist management (view/edit/revoke) is out of scope here, tracked as a separate feature.

**SearXNG as external service.** SearXNG has its own container but is architecturally treated identically to Exa/Tavily from the bot's perspective — just another URL behind egressguard's search lane. The bot doesn't know or care if search is local or cloud.

**Infisical always in the stack.** Previously optional (cloud or self-hosted). Now always a container in docker-compose, which unlocks future bootstrap automation. Manual secret seeding still required for now — full automation is a follow-on.

---

## Current state at handoff

### Done this session
- `docker-compose.yml` — restructured to 6 services and 4 named networks (not yet committed as working code — plan only)
- `napyclaw/setup.py` — Exa/Tavily split into optional cloud backup section (committed, pushed)
- `README.md` — Slack noted as only non-self-hostable gap, links to issue #7 (committed, pushed)
- Spec written and committed: `docs/superpowers/specs/2026-04-19-atomic-mode-architecture-design.md`
- Implementation plan written and committed: `docs/superpowers/plans/2026-04-19-atomic-mode-architecture.md`

### Not yet started (implementation plan tasks)
All 10 tasks in the plan are pending. Nothing has been implemented — only designed and planned.

### Open issues created
- **[#7](https://github.com/napyclaw/napyclaw/issues/7)** — Replace Slack with a self-hosted messaging backend (comms gap for full atomic mode)

---

## What a new session needs to know

**The codebase today** has a working single-process bot with in-process EgressGuard, ContentShield, InjectionGuard, and a Slack channel integration. All of that remains intact — the implementation plan builds new containers around it rather than rewriting the bot internals.

**The existing `egress.py`** (`napyclaw/egress.py`) has an `escalate` verdict path that currently defaults to deny ("Slack approval flow added later" — see line 169). Task 2 of the plan implements that flow. Don't touch the existing `EgressGuard` class — add the new FastAPI service alongside it.

**The existing `channels/slack.py`** connects directly to Slack Socket Mode. In the new architecture, inbound messages come from the comms container via HTTP instead. The comms service wraps the Slack connection. Task 3 of the plan handles this — the `SlackChannel` class is reused inside the comms service.

**Two Python diagrams exist.** The spec has the container topology diagram. The README has a separate Python process flow diagram showing what happens inside the bot. Both are correct and should coexist — the plan's Task 9 adds the container diagram as a new section, updates the Python diagram heading only, does not replace it.

**Search routing note.** In the current code, `WebSearchTool` calls Exa/Tavily/SearXNG directly. After Task 4+7, all those calls route through egressguard. The SearXNG URL changes from `localhost:8080` to `http://searxng:8080` (container hostname on search-net) — but the bot still hits it via `egress-net` through egressguard, not directly on search-net. Read the spec Network Zones section (lines 87–90) carefully — the `search-net` note in the spec describes bot→searxng as direct, but the later architectural decision was that all search goes through egressguard's egress lane for exfil protection. The plan reflects this correctly; the spec's search-net description is slightly inconsistent with the final decision. Follow the plan.

---

## Out of scope — don't implement these in this plan

- **Allowlist management** — view, edit, revoke approved domains. Separate spec needed.
- **Full Infisical bootstrap automation** — auto-create project, machine identity, seed secrets. Follow-on task.
- **Comms container for Mattermost/Matrix** — issue #7. The comms container in this plan wraps Slack. The abstraction is built so swapping is easy later.
- **Container escape hardening** — out of scope for a personal single-user deployment.
- **Multi-tenant isolation** — this is a single-owner stack.
