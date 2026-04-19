# Atomic Mode: Container Architecture Design

**Date:** 2026-04-19
**Status:** Draft — pending implementation plan

---

## Goal

Define a fully self-contained ("atomic mode") deployment of napyclaw where every component runs on your own infrastructure with zero external service dependencies. All containers run on a single Docker host. No cloud accounts required.

---

## Container Topology

```
╔══════════════════════════════════════════════════════════════════════╗
║  EXTERNAL                                                             ║
║  Slack / Mattermost     SearXNG · Exa · Tavily      LLM APIs         ║
║        ▲                           ▲                    ▲            ║
╚════════╪═══════════════════════════╪════════════════════╪════════════╝
         │                           │                    │
    [comms]                          └────────────────────┘
    {protocol adapter}                        ▲
    stateless                                 │ all bot outbound HTTP
         │  ▲                                 │ (search + LLM + unknown)
    msgs │  │ approval requests         [egressguard]
         │  │ via comms                 {domain allowlist}
         ▼  │                           {exfil / query sanitization}
╔══════════════════════════════════════════════════════════════════════╗
║  [bot]                                                               ║
║  ┌──────────────────────────────────────────────────────────────┐   ║
║  │ {InjectionGuard}   inbound prompt + outbound query scan      │   ║
║  ├──────────────────────────────────────────────────────────────┤   ║
║  │ {ToolSystem}   web_search · file · send_message · scheduler  │   ║
║  ├──────────────────────────────────────────────────────────────┤   ║
║  │ {LLMClient}    Ollama · OpenAI · Foundry · Bedrock           │   ║
║  ├──────────────────────────────────────────────────────────────┤   ║
║  │ {ContentShield}    scans all content before DB write         │   ║
║  ├──────────────────────────────────────────────────────────────┤   ║
║  │ {GroupContext}     per-channel identity · history · memory   │   ║
║  └──────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════╝
              │                                    │
              ▼                                    ▼
           [db]                             [infisical]
     postgres + pgvector                 secrets (self-hosted)
     no internet                         no internet
```

**Legend:** `[container]` · `{application layer}`

---

## Containers

| Container | Role | Internet access | Persistent state |
|---|---|---|---|
| `bot` | napyclaw core — LLM calls, tools, history | No — internal only | No (DB owns state) |
| `egressguard` | Outbound HTTP proxy for bot — domain allowlist, exfil detection | Yes — data plane | No |
| `comms` | Stateless messaging adapter — Slack, Mattermost, or other | Yes — messaging APIs | No |
| `db` | PostgreSQL + pgvector — all persistent state | No | Yes |
| `infisical` | Secrets manager (self-hosted) | No | Yes |

SearXNG is **not** a container in this stack. It is treated as an external HTTP service — local (`localhost:8080`) or cloud (Exa, Tavily) — accessed via EgressGuard the same way as any other search provider. This keeps the bot agnostic to whether search is local or remote.

---

## Network Zones

Three Docker networks enforce isolation:

**`internal`** — bot, db, infisical, egressguard, comms
- No internet routing
- Bot can reach db, infisical, egressguard, comms
- db and infisical are reachable only from bot (secrets fetch on startup)

**`egress`** — egressguard only
- Has internet routing
- All bot outbound HTTP (LLM APIs, search providers, unknown domains) routes here
- Unknown domains trigger an approval request routed back through comms

**`external`** — comms only
- Has internet routing
- Scoped to messaging APIs (Slack, Mattermost, etc.)

```
[bot] ──internal──→ [egressguard] ──egress──→ internet (LLM, search)
[bot] ──internal──→ [comms] ──external──→ internet (messaging)
[bot] ──internal──→ [db]
[bot] ──internal──→ [infisical]
```

---

## Application Layers (inside `bot`)

| Layer | Responsibility |
|---|---|
| `{InjectionGuard}` | Sanitizes inbound prompts and outbound tool call arguments (search queries, file paths) before they leave the process |
| `{ToolSystem}` | Exposes tools to the LLM: `web_search`, `send_message`, `file_read/write`, `scheduler` |
| `{LLMClient}` | Provider abstraction — Ollama, OpenAI, Azure AI Foundry, AWS Bedrock, swappable per channel |
| `{ContentShield}` | Scans all content before it reaches the DB — redacts credentials, PII, injection artifacts |
| `{GroupContext}` | Per-channel identity, conversation history, memory, owner permissions |

EgressGuard runs as its own container but also has an application layer (`{domain allowlist}`, `{exfil detection}`) that enforces policy on all proxied traffic.

---

## EgressGuard Approval Flow

When the bot makes an outbound call to an unknown domain:

1. `[egressguard]` intercepts and blocks the request
2. `[egressguard]` sends an approval request to `[comms]`
3. `[comms]` delivers the request to the owner via the configured messaging platform
4. Owner approves or denies via the messaging platform
5. `[comms]` receives the response and forwards it to `[egressguard]`
6. `[egressguard]` allows or permanently blocks the domain and completes or rejects the original request

Neither `[bot]` nor `[egressguard]` is directly coupled to the messaging platform — `[comms]` is the only component that knows which platform is in use.

---

## Atomic Mode

"Atomic mode" means the full stack runs with no external services required:

| Layer | Atomic option |
|---|---|
| LLM | Ollama (local, via Tailscale or localhost) |
| Search | SearXNG (self-hosted, `localhost:8080`) |
| Secrets | Infisical (self-hosted, in this compose stack) |
| Comms | Self-hosted Mattermost or similar (issue [#7](https://github.com/napyclaw/napyclaw/issues/7)) |
| DB | PostgreSQL + pgvector (always local) |

Cloud providers (OpenAI, Exa, Tavily, Slack) remain available as opt-in upgrades. The setup wizard will distinguish between required secrets, provider-specific secrets, and optional cloud backup secrets (Exa, Tavily).

The only current gap preventing full atomic mode is the comms layer — tracked in issue #7.

---

## What This Design Does Not Cover

- **Container escape** — kernel-level isolation is assumed. A full container escape bypasses all of this. Out of scope for a personal single-user deployment.
- **Multi-tenant isolation** — this architecture is designed for one owner. Shared deployments require additional sandboxing (see NemoClaw).
- **Infisical bootstrap automation** — setup.py currently requires manual Infisical project and secret creation. Automating this is a follow-on task.
- **Comms container implementation** — depends on issue #7 resolution (Mattermost vs Matrix vs other).
