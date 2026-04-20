# Atomic Mode: Container Architecture Design

**Date:** 2026-04-19
**Status:** Draft — pending implementation plan

---

## Goal

Define a fully self-contained ("atomic mode") deployment of napyclaw where every component runs on your own infrastructure with zero external service dependencies. All containers run on a single Docker host. No cloud accounts required.

---

## Container Topology

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  EXTERNAL                                                                    ║
║  Slack · Mattermost        Exa · Tavily · LLM APIs        Google·Bing·DDG   ║
╚════════════╤══════════════════════════════╤═══════════════════════╤══════════╝
             │ ▲                            │ ▲                     │ ▲
             ▼ │                            ▼ │                     ▼ │
  ┌───────────────────┐             ┌────────────────────────┐  ┌─────────────┐
  │       comms       │◄─approvals──│      egressguard       │  │   searxng   │
  │  {proto adapter}  │             │  {domain allowlist}    │  │ {meta-search}│
  │    stateless      │             │  {LLMClient}           │  │             │
  │                   │             │  {exfil sanitize}      │  │             │
  └───────────────────┘             └────────────────────────┘  └─────────────┘
             │ ▲                            │ ▲                     │ ▲
             ▼ │                            ▼ │                     ▼ │
╔══════════════════════════════════════════════════════════════════════════════╗
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │ bot                                                                  │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {InjectionGuard}   inbound prompt + outbound query scan              │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {ToolSystem}   web_search · file · send_message · scheduler          │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {LLMClient}    Ollama · OpenAI · Foundry · Bedrock                   │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {ContentShield}    scans all content before DB write                 │   ║
║  ├──────────────────────────────────────────────────────────────────────┤   ║
║  │ {GroupContext}     per-channel identity · history · memory           │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════════════╝
              │                                               │
              ▼                                               ▼
  ┌─────────────────────────┐               ┌─────────────────────────┐
  │           db            │               │        infisical         │
  │   postgres + pgvector   │               │   secrets (self-hosted)  │
  │       no internet       │               │       no internet        │
  └─────────────────────────┘               └─────────────────────────┘
```

**Legend:** container boxes · `{application layer}`

---

## Containers

| Container | Role | Internet access | Persistent state |
|---|---|---|---|
| `bot` | napyclaw core — LLM calls, tools, history | No — internal only | No (DB owns state) |
| `egressguard` | Outbound HTTP proxy for bot — domain allowlist, exfil detection, domain judgment LLM | Yes — LLM APIs, Exa, Tavily | No |
| `comms` | Stateless messaging adapter — Slack, Mattermost, or other | Yes — messaging APIs only | No |
| `searxng` | Self-hosted meta-search (Google, Bing, DDG) | Yes — search engines only | No |
| `db` | PostgreSQL + pgvector — all persistent state | No | Yes |
| `infisical` | Secrets manager (self-hosted) | No | Yes |

Each of the three external-facing containers (`comms`, `egressguard`, `searxng`) has its own dedicated network lane and its own scoped external access. The bot never reaches the internet directly — it always routes through one of these three containers. The bot is agnostic to which search backends are local or remote; SearXNG is architecturally identical to Exa or Tavily from the bot's perspective.

---

## Network Zones

Four Docker networks enforce isolation:

**`comms-net`** — bot, comms
- comms has internet routing; bot does not
- Bot sends and receives messages through comms only
- comms also receives approval requests from egressguard and forwards responses back

**`egress-net`** — bot, egressguard
- egressguard has internet routing; bot does not
- All bot outbound HTTP (LLM APIs, Exa, Tavily, unknown domains) routes through egressguard

**`search-net`** — bot, searxng
- searxng has internet routing; bot does not
- Bot sends search queries to searxng directly on this network
- searxng forwards to Google, Bing, DDG and returns results

**`data-net`** — bot, db, infisical
- No internet routing on any member
- Bot fetches secrets from infisical on startup; reads/writes all state to db

```
  bot ──comms-net──→  comms  ──→ internet (messaging)
                        ▲
                        │ approvals
                        │
  bot ──egress-net──→ egressguard ──→ internet (LLM APIs, Exa, Tavily)
  bot ──search-net──→ searxng     ──→ internet (Google, Bing, DDG)
  bot ──data-net───→  db
  bot ──data-net───→  infisical
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

EgressGuard runs as its own container with its own application layers:

| Layer | Responsibility |
|---|---|
| `{domain allowlist}` | Pre-approved and permanently blocked domain lists; fast path for known domains |
| `{LLMClient}` | Judges unknown domains — assesses whether the domain is safe to allow based on context |
| `{exfil / query sanitization}` | Scans outbound request content for data exfiltration patterns before forwarding |

---

## EgressGuard Approval Flow

### Request lifecycle

When the bot makes an outbound call to an unknown domain:

1. `egressguard` intercepts the request
2. `{LLMClient}` inside egressguard judges the domain — if clearly safe (e.g. a well-known API) it auto-approves and adds to the permanent allowlist; if clearly malicious it permanently blocks
3. If inconclusive, `egressguard` returns `202 Accepted` to the bot with a pending token and sends an approval request to `comms`
4. `bot`'s `{ToolSystem}` receives the 202, surfaces "awaiting approval for domain X" to the LLM, and continues other work — the chain is not blocked
5. `comms` delivers the approval request to the owner with two response options: **Approve once** or **Approve always**
6. Owner responds; `comms` POSTs the decision back to `egressguard` as an event callback
7. `egressguard` updates its state and the pending request resolves immediately

Neither `bot` nor `egressguard` is directly coupled to the messaging platform — `comms` is the only component that knows which platform is in use. Swapping Slack for a self-hosted comms platform (issue [#7](https://github.com/napyclaw/napyclaw/issues/7)) requires no changes to egressguard.

### Approve once vs approve always

The comms approval message presents two options:

| Response | Effect |
|---|---|
| **Approve once** | Domain is allowed for this request only. Next request to the same domain triggers a new approval flow. |
| **Approve always** | Domain is added to the permanent allowlist in `egressguard`. Future requests pass through without prompting. |

Deny has the same two variants (deny once, deny always / block permanently). Allowlist management — viewing, editing, and revoking approved domains — is out of scope for this spec.

### Async retry cadence

The event callback is the primary resolution path. The stepback retry is a safety net for callback failure or bot restarts:

```
202 returned → retry schedule starts in bot scheduler:
  30s → 60s → 2m → 5m → 10m → 20m → fail with "pending approval"
```

At each retry, bot polls `egressguard` for the token status:
- `pending` → wait for next interval
- `approved` → complete the original request
- `denied` → surface failure to LLM

When the user approves (use case 1: active chat in seconds; use case 2: away for 30+ minutes), `comms` fires the callback to `egressguard` immediately. The bot's next scheduler poll — or the callback itself if the bot exposes a notify endpoint — resolves the request without waiting for the next retry interval.

After the final 20m retry with no resolution, the task fails with a message the LLM can surface to the user: *"Waiting on domain approval for X — retry the task once approved."* The pending token remains valid; the user can still approve later and re-run.

---

## Atomic Mode

"Atomic mode" means the full stack runs with no external services required:

| Layer | Always in stack | Atomic option | Cloud upgrade |
|---|---|---|---|
| LLM | — | Ollama (local inference) | OpenAI, Azure Foundry, Bedrock |
| Search | SearXNG (container) | SearXNG only | + Exa, Tavily as fallbacks |
| Secrets | Infisical (container) | ✅ already self-hosted | Infisical Cloud |
| Comms | comms adapter (container) | Self-hosted Mattermost/Matrix ([#7](https://github.com/napyclaw/napyclaw/issues/7)) | Slack |
| DB | PostgreSQL + pgvector | ✅ always local | — |
| Egress control | egressguard (container) | ✅ always local | — |

In atomic mode, no traffic leaves your infrastructure except through the three scoped external lanes (`comms-net`, `egress-net`, `search-net`) — and in full atomic mode, all three point at self-hosted or local endpoints.

The only current gap is the comms layer — tracked in issue #7.

---

## What This Design Does Not Cover

- **Allowlist management** — viewing, editing, revoking, and bulk-managing approved/blocked domains is a separate feature and spec.
- **Container escape** — kernel-level isolation is assumed. A full container escape bypasses all of this. Out of scope for a personal single-user deployment.
- **Multi-tenant isolation** — this architecture is designed for one owner. Shared deployments require additional sandboxing (see NemoClaw).
- **Infisical bootstrap automation** — setup.py currently requires manual Infisical project and secret creation. Automating this is a follow-on task.
- **Comms container implementation** — depends on issue #7 resolution (Mattermost vs Matrix vs other).
