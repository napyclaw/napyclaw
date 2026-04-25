# napyclaw

A personal AI agent framework in Python, built to be small enough to understand and easy to customize. Runs on Ollama over Tailscale for local inference, or any OpenAI-compatible API (OpenAI, OpenRouter) with your own keys. Ships with Slack out of the gate. Designed to run in **atomic mode** — fully self-hosted with no external service dependencies — or with cloud providers as opt-in.

Inspired by [nanoclaw](https://github.com/NateBJones-Projects/nanoclaw) and [OB1 (Open Brain)](https://github.com/NateBJones-Projects/OB1) for knowledge/embedding architecture.

## Key Features

- **Multi-provider LLM support** — Ollama (local, over Tailscale), OpenAI-compatible APIs, Azure AI Foundry, or AWS Bedrock. Switch models per channel at runtime.
- **Slack Socket Mode** — runs as a Slack bot with per-channel identity (custom names, nicknames, owner permissions).
- **Semantic memory** — PostgreSQL + pgvector for vector search, with per-group Markdown files as fallback. Embeddings via Ollama.
- **Tool system** — web search (SearXNG, self-hosted), file read/write, scheduled tasks, messaging, bot identity management. All exposed to the LLM as function calls.
- **EgressGuard** — outbound domain policy engine. Every HTTP request passes through trust tiers (threat intel, Majestic top 10k, LLM judge) before leaving the process. Domain-only — never sees payload.
- **ContentShield** — scans all content before storage for credentials (detect-secrets) and PII (Presidio). Redacts secrets and SSNs; allows phone/email through.
- **Private sessions** — ephemeral DM conversations with no persistence. Nothing stored, nothing remembered.
- **Scheduled tasks** — cron, interval, or one-shot prompts with retry and exponential backoff.
- **InjectionGuard** — token-shuffle content inspector with rotating verification keys. Shuffles and bags input before sending to a reviewer LLM, defeating injection sequences while preserving detectable vocabulary. Blocks malicious content, warns on suspicious content. Applied to user input (HIGH) and tool results (source-tagged: web search = MEDIUM, internal tools = skipped).
- **Secrets via Infisical** — all config loaded from Infisical at startup. No `.env` files. Infisical is included in `docker-compose.yml` and runs self-hosted by default.

## Architecture

napyclaw is plain OOP Python — no frameworks, no magic. Each class has one job.

### Container architecture

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

### Bot container — internal flow

```
Message arrives (from comms container via HTTP)
    |
    v
SlackChannel --- normalizes to Message dataclass
    |
    v
NapyClaw.handle_message()
    |-- ContentShield.scan() --- redacts secrets/PII before storage
    |-- InjectionGuard.review() --- token-shuffle inspection (user_input = HIGH)
    |-- Store message in PostgreSQL
    |-- Check trigger (@name, @nickname, or Slack mention)
    |-- Get or create GroupContext for this channel
    |-- GroupQueue.run() --- one agent per channel at a time
         |
         v
    Agent.run()
         |-- Build system prompt (memory + context)
         |-- LLMClient.chat() --- OpenAI or Ollama
         |-- If tool calls: execute tools, InjectionGuard.review() on results, loop (max 10)
         |-- Return final text
         |
         v
    channel.send() --- reply in Slack
```

### File layout

```
napyclaw/
  __main__.py          Entry point
  app.py               NapyClaw orchestrator, GroupContext, GroupQueue
  agent.py             Agent — conversation loop, tool execution, history management
  config.py            Config from Infisical
  db.py                PostgreSQL persistence (asyncpg)
  memory.py            MemoryBackend: VectorMemory (pgvector), MarkdownMemory, NullMemory
  injection_guard.py   InjectionGuard — token-shuffle content inspector with rotating verification keys
  scheduler.py         Polls DB for due tasks, fires through agents
  shield.py            ContentShield — credential/PII scanning
  egress.py            EgressGuard — outbound domain policy with LLM judge
  private_session.py   Ephemeral DM sessions, no persistence
  oauth.py             OAuth callback server (Phase 2 scaffold)
  models/
    base.py            LLMClient ABC, ChatResponse, ToolCall
    openai_client.py   OpenAI API wrapper
    ollama_client.py   Ollama via OpenAI-compatible endpoint
  channels/
    base.py            Channel ABC, Message dataclass
    slack.py           Slack Socket Mode via slack-bolt
  tools/
    base.py            Tool ABC with schema property
    web_search.py      Pluggable search backends (SearXNG, Exa, Tavily) with fallback
    file_ops.py        Sandboxed file read/write
    messaging.py       Send messages to channels
    scheduling.py      Create/list/cancel scheduled tasks
    identity.py        Rename bot, add nicknames, switch models
    recipes/
      base.py          RecipeTool base (Phase 2 scaffold)
```

### How the pieces connect

**Config** loads all secrets from the Infisical instance (self-hosted by default) at startup into a typed dataclass. Nothing else touches Infisical.

**LLMClient** is an ABC with two implementations. `OpenAIClient` wraps the OpenAI SDK with a hardcoded context window table. `OllamaClient` wraps the same SDK pointed at an Ollama Tailscale URL, and fetches the actual context window from Ollama's `/api/show` endpoint.

**Agent** holds conversation history, calls the LLM, and executes tools in a loop. History is pruned by exchange blocks (user + assistant + tool messages together) to fit a dynamic budget based on model context window.

**GroupContext** ties a Slack channel to its agent, LLM client, names, and owner. Each channel gets its own identity — names, nicknames, model, and conversation history.

**EgressGuard** wraps httpx clients with a request hook. Before any HTTP request leaves the process, the hostname is checked against: (1) abuse.ch threat intel blocklist, (2) Majestic top 10k + internal allowlist, (3) verdict cache, (4) LLM judge. Only the hostname is inspected — never payload, headers, or body.

**MemoryBackend** has three implementations. `VectorMemory` (pgvector + Ollama embeddings) for semantic search. `MarkdownMemory` (per-group MEMORY.md files) as fallback. `NullMemory` for private sessions.

## Security

napyclaw is a personal agent — single owner, single instance, running on your own infrastructure. Its security model reflects that: it prioritizes keeping your data local and controlling what leaves your network, rather than multi-tenant isolation.

### Threat model

This table compares napyclaw against its predecessors and related projects across the same attack vectors.

| Vector | OpenClaw | NanoClaw | NemoClaw | napyclaw |
|---|---|---|---|---|
| Vendor lock-in | ❌ Tied to OpenAI | ⚠️ Anthropic API format required — Ollama (via proxy), Together AI, and Fireworks work but must be Anthropic-compatible | ❌ Tied to NVIDIA NIM | ✅ No lock-in. LLMClient ABC abstracts providers — swap Ollama, OpenAI, OpenRouter, Azure AI Foundry, or AWS Bedrock per channel at runtime. Scheduler and memory run locally, not on a provider's platform. OpenBrain-style architecture means your knowledge persists independent of any model. |
| Outbound exfiltration | ❌ Unrestricted | ❌ Unrestricted | ✅ Egress control + operator approval flow | ✅ EgressGuard: threat intel blocklist, Majestic top 10k allowlist, LLM judge, verdict cache. Domain-only — never inspects payload. |
| Audit trail | ❌ None | ❌ None | ✅ Built-in policy enforcement + audit logging | ✅ shield_log (credential/PII detections), egress_verdicts (domain decisions), egress_log (every outbound check), all messages stored with redaction metadata. |
| Credential theft | ❌ No protection | ⚠️ API key still mounted | ⚠️ Privacy router intercepts inference — credentials still exist inside sandbox | ✅ Infisical loads secrets at startup; held in Config only. ContentShield redacts credentials before any storage. Tools never expose secrets to the LLM. |
| Prompt injection | ❌ Full host impact | ⚠️ Container-scoped | ⚠️ Still architecturally unsolved — damage scoped to sandbox | ✅ InjectionGuard: token-shuffle inspection on user input (HIGH) and tool results (web search = MEDIUM, internal = skipped). Blocks malicious content before it reaches the agent context. Blast radius further limited by owner-only tool gates and EgressGuard. |
| Skill/plugin injection | ❌ Copy/paste, no review | ⚠️ Small surface but no formal ingestion | ⚠️ Sandbox limits damage but skills still copied verbatim | ✅ Learning pipeline: 4-stage LLM abstraction with security review, step count threshold, and staged activation ([#5](https://github.com/napyclaw/napyclaw/issues/5)). Raw skill text never reaches implementation. |
| Data locality | ❌ Cloud-dependent | ⚠️ Local inference, but no structured local storage | ⚠️ Local inference + sandbox, but NVIDIA stack phones home for licensing | ✅ Full local stack available: Ollama for inference, single PostgreSQL + pgvector instance for all state and memory. Nothing leaves your network unless you opt in. |
| Data → cloud leakage | ❌ No controls | ⚠️ Depends on config | ✅ Privacy router keeps internal data local | ✅ EgressGuard on all outbound HTTP. Can run fully local (Ollama + local Postgres). Cloud LLM is opt-in, not default. |
| Supply chain | ❌ 500k LoC unreviewed | ✅ 500 LoC auditable | ⚠️ Adds NVIDIA stack — attack surface grows | ✅ ~2k LoC core, plain Python, no frameworks. Dependencies are well-known libraries (openai, httpx, slack-bolt, asyncpg). |
| Exposed network port | ❌ 0.0.0.0 default | ✅ No listener | ✅ OpenShell netns isolation | ✅ No listener. comms, egressguard, and searxng containers handle all outbound — bot has no internet access. |
| Host RCE | ❌ Critical | ✅ Ephemeral containers | ✅ OpenShell (Landlock + seccomp + netns) | ⚠️ Bot runs in Docker with no internet access. File tools sandboxed to `workspace_dir` with path traversal checks. No Landlock/seccomp — process isolation only. |
| Network reach after compromise | ❌ Full internet from process | ⚠️ Container-scoped, but outbound internet available | ✅ netns isolation blocks direct outbound | ✅ Bot is on `data-net` only — no default gateway to the internet. A compromised process cannot exfiltrate directly; traffic must route through egressguard, comms, or searxng, each with scoped external access. Enforced at the kernel by Docker network namespaces. |
| Cross-agent leakage | ❌ Shared memory | ✅ Isolated sessions | ✅ Per-agent sandboxed environments | ⚠️ Per-group agents with separate history. Private sessions use NullMemory. No OS-level isolation between groups. |

### What napyclaw does well

**Credential hygiene.** Secrets live in Infisical, not in files. ContentShield scans everything before storage — if someone pastes an API key into Slack, it gets redacted before it reaches the database or the LLM. The original is never stored anywhere.

**Egress control.** napyclaw is the only project in this lineage (besides NemoClaw) that gates outbound network access. Every HTTP request passes through EgressGuard before leaving the process. Unknown domains hit an LLM judge and can be escalated to the owner for approval.

**Auditability.** The codebase is small enough to read in an afternoon. There are no plugin systems, no dynamic code loading, no eval. Every tool is a Python class with an `execute()` method that returns a string.

**Data locality.** Default configuration keeps everything on your machines — Ollama for inference, a single PostgreSQL + pgvector instance for all state, history, and memory. Cloud LLM providers are available but opt-in, and all outbound calls pass through EgressGuard. Your data never has to leave your network.

**Safe skill ingestion.** Most agent frameworks import skills by copy/paste — the user or agent blindly copies instruction text into the tool registry, and any prompt injection hidden in the skill definition comes along for the ride. napyclaw's learning pipeline ([#5](https://github.com/napyclaw/napyclaw/issues/5)) solves this at the ingestion layer: a four-stage LLM pipeline abstracts the skill into a structured schema (process, tools, data), reviews it for completeness and security risks, then writes a clean Python implementation from the abstraction only. The raw skill text never reaches the implementation call. A step count threshold rejects overly complex skills and forces decomposition into composable, independently reviewed pieces.

**No vendor lock-in.** Most agent frameworks are tightly coupled to a single model provider — your conversation history, memory, and scheduled tasks live on that provider's platform. napyclaw keeps all of that locally. The LLMClient ABC means you can swap between Ollama, OpenAI, OpenRouter, Azure AI Foundry, and AWS Bedrock per channel at runtime. The scheduler runs against your local database, not a provider's API. The OpenBrain-inspired memory architecture (pgvector + embeddings) means your knowledge base persists and remains searchable regardless of which model you're using today. If a provider raises prices, changes terms, or disappears, you switch models — not platforms.

**Deliberate provider choices.** Every external dependency in napyclaw was chosen with ToS, data rights, and AI-use permissions in mind. The table below shows the reasoning.

| Category | Provider | AI use permitted | Results storable | Self-hostable | Why chosen |
|----------|----------|-----------------|-----------------|---------------|------------|
| Search | SearXNG | ✅ | ✅ | ✅ | Default. No ToS — you own the instance. Aggregates Google, Bing, DDG. |
| Search | Exa | ✅ Explicitly | ✅ Explicitly | ❌ | Fallback. Neural search, built for LLMs, no storage restrictions. |
| Search | Tavily | ✅ Explicitly | ✅ Explicitly | ❌ | Fallback. AI-native, clean summaries, explicit agent use permission. |
| Search | Brave | ⚠️ Inference only | ❌ Prohibited | ❌ | Not included. Prohibits caching results and AI training/eval use — incompatible with vector memory. |
| LLM | Ollama | ✅ | ✅ | ✅ | Default. Fully local, no data leaves your machine. |
| LLM | OpenAI | ✅ | ✅ (opt-out) | ❌ | Opt-in. Industry standard, best tool-calling. API data not used for training by default. |
| LLM | Azure AI Foundry | ✅ | ✅ | ❌ | Opt-in. Enterprise Azure terms, strong data residency controls. |
| LLM | AWS Bedrock | ✅ | ✅ | ❌ | Opt-in. AWS terms explicitly prohibit using prompts/responses for model training. |
| Secrets | Infisical | N/A | N/A | ✅ | Secrets never touch the filesystem. Self-hostable for zero cloud dependency. |
| Database | PostgreSQL + pgvector | ✅ | ✅ | ✅ | Fully local. Your knowledge base never leaves your infrastructure. |
| Comms | Slack (Socket Mode) | ✅ | ✅ | ❌ | Outbound-only connection — no inbound port exposed. Only non-self-hostable component — tracked in [#7](https://github.com/napyclaw/napyclaw/issues/7). |

### Atomic mode

Every component in napyclaw can run on your own infrastructure with no external service dependencies:

| Layer | Self-hosted option |
|---|---|
| LLM | Ollama — runs locally or over Tailscale |
| Search | SearXNG — included in `docker-compose.yml` |
| Secrets | Infisical — included in `docker-compose.yml` |
| Comms | Self-hosted Mattermost or Matrix (issue [#7](https://github.com/napyclaw/napyclaw/issues/7)) |
| Database | PostgreSQL + pgvector — always local |
| Egress control | egressguard — always local |

In atomic mode, traffic only leaves your infrastructure through three scoped lanes:
- **`comms-net`** — messaging platform only
- **`egress-net`** — LLM APIs and cloud search (Exa, Tavily) — optional, only if you choose cloud providers
- **`search-net`** — SearXNG to search engines

The only current gap is the comms layer (Slack is not self-hostable) — tracked in issue [#7](https://github.com/napyclaw/napyclaw/issues/7).

### What we're working on

These are known vulnerabilities with active mitigation plans. See the linked issues for details and progress.

**No syscall isolation** ([#3](https://github.com/napyclaw/napyclaw/issues/3)). Unlike NemoClaw (Landlock + seccomp), the bot runs in a standard Docker container — no seccomp profile, no read-only filesystem, no non-root user. If the process is compromised, the attacker has container-level access including the Config object holding all secrets in memory. This is an acceptable tradeoff for a personal tool running on your own tailnet; it would not be acceptable in a shared or multi-tenant environment. Next step: non-root user, read-only root filesystem, seccomp profile.

**Secrets in memory** ([#4](https://github.com/napyclaw/napyclaw/issues/4)). Infisical keeps secrets out of files, but `Config.from_infisical()` loads them all into a Python dataclass at startup. A memory dump of the napyclaw process would expose every API key. This is inherent to any application that needs to use secrets at runtime. Planned mitigations: on-demand secret fetch with mlock to prevent swap-file leakage, and eventually a delegated auth proxy so napyclaw never holds API keys directly.

**Self-hostable comms** ([#7](https://github.com/napyclaw/napyclaw/issues/7)). Slack is the only component in the stack that can't be self-hosted. Evaluating self-hostable alternatives (Mattermost, Rocket.Chat, Zulip, Matrix) so the full stack can run on your own infrastructure with no external service dependencies.

### Legal & compliance

Agent frameworks interact with external APIs on your behalf, often in ways their ToS authors didn't anticipate. napyclaw treats this as a first-class concern.

**Search result licensing.** Most search APIs prohibit caching or persisting results — including using them to build embeddings or train models. napyclaw's default search stack (SearXNG, Exa, Tavily) was chosen specifically because all three permit AI agent use and result storage. Raw search result blocks are wrapped in markers and stripped from vector memory capture; only the agent's synthesis is embedded. Users can explicitly save specific content via `save_to_memory` when they want it persisted.

**Query privacy.** Search queries sent to cloud providers (Exa, Tavily) may be logged and used under those providers' terms. Sensitive queries should route through SearXNG, which is self-hosted and sends queries only to the underlying search engines under their standard terms. The `providers` parameter on `web_search` lets you direct specific queries to specific backends.

**LLM data retention.** Cloud LLM providers vary in how long they retain prompt and response data. OpenAI's API does not use data for training by default (opt-out required for zero retention). AWS Bedrock explicitly prohibits using prompts/responses for model training. Azure AI Foundry applies enterprise data handling terms. Ollama is local — no data leaves your machine.

**Conversation data.** All messages are stored in your local PostgreSQL instance. ContentShield redacts credentials and PII (SSNs) before storage. Phone numbers and email addresses pass through by default — adjust `shield.py` if your use case requires stricter handling.

**Termination and data deletion.** If you stop using a cloud search or LLM provider, their terms typically require destroying any retained data. Because napyclaw strips raw search results before vector memory capture, there is nothing to delete from your database on provider termination — only your own synthesized notes remain.

### Who this is for

napyclaw assumes you trust the machine it runs on and the Slack workspace it connects to. It's designed for a single person running their own agent on their own infrastructure. If you need multi-tenant isolation, sandboxed execution, or zero-trust agent architecture, look at NemoClaw or similar projects.

## Installation

There are two ways to get napyclaw running: have an AI agent walk you through it, or do it yourself. Either way, you'll make the same set of choices below.

### AI-guided install

If you have access to Claude, ChatGPT, or any capable coding agent, give it this README and ask it to help you set up napyclaw. The agent can run the setup wizard, ask you questions, write `napyclaw.toml` for you, and tell you exactly which secrets to put in Infisical. This is the recommended path if you haven't done this kind of setup before.

Prompt to get started:

> I want to set up napyclaw. Here's the README.
>
> Please help me:
> 1. Ask me which LLM provider I want to use and what model
> 2. Run `python -m napyclaw setup` and answer the prompts based on my answers
> 3. Tell me exactly which secrets to add to Infisical and where to find each one
> 4. Walk me through creating the Slack app step by step
> 5. Start the database with `docker compose up -d`
> 6. Pull the embedding model with `ollama pull nomic-embed-text`
> 7. Run `python -m napyclaw` and confirm it connects
>
> Ask me one section at a time and wait for my answers before proceeding.

### Manual install

If you're comfortable with Python, Docker, and API keys, here's what you need to do.

#### Setup wizard

napyclaw includes an interactive setup wizard that asks you questions, writes `napyclaw.toml`, and prints exactly which secrets to add to Infisical:

```bash
python -m napyclaw setup
```

Run this first, then follow the printed instructions. The steps below explain each decision in more detail.

#### Step 1: Choose your LLM

You need at least one. You can use both and switch between them per channel at runtime.

| Option | What you need | Best for |
|--------|--------------|----------|
| **Ollama (local)** | A machine running Ollama, reachable over Tailscale. Pull your model (`ollama pull llama3.3`). Set `num_ctx` explicitly — Ollama defaults to 2048 regardless of model capability. | Privacy, no API costs, full control |
| **OpenAI** | An OpenAI API key from platform.openai.com | Best tool-calling models, no hardware required |
| **OpenRouter** | An OpenRouter API key from openrouter.ai | Access to many models (Claude, Gemini, Llama, etc.) through one API |
| **Azure AI Foundry** | An Azure AI Foundry endpoint and API key. Set `FOUNDRY_BASE_URL` to your deployment endpoint and `FOUNDRY_API_KEY` to your key. | Azure-hosted models (GPT-4o, Phi, Mistral, etc.), enterprise Azure billing |
| **AWS Bedrock** | AWS credentials (access key + secret, or IAM role) and a region. Install the optional dependency: `pip install -e ".[bedrock]"`. | Claude, Llama, Nova, Titan — all through AWS billing and IAM |

For Ollama over Tailscale: install Tailscale on both the machine running Ollama and the machine running napyclaw. The Ollama base URL will be something like `http://100.x.x.x:11434/v1`.

**Important:** If using Ollama, set `num_ctx` in your model's Modelfile or via the API. The default 2048 context window severely limits conversation history. For llama3.3, `num_ctx=65536` is recommended if your hardware supports it.

#### Step 2: Configure napyclaw

Configuration is split into two parts: **app config** (non-sensitive, lives in `napyclaw.toml` in the repo) and **secrets** (credentials only, loaded from Infisical at startup).

##### napyclaw.toml

Edit `napyclaw.toml` in the repo root before running. This file is safe to commit — it contains no credentials.

```toml
[llm]
default_provider = "foundry"       # openai | ollama | foundry | bedrock
default_model = "grok-4-20-non-reasoning"
openai_base_url = "https://api.openai.com/v1"
ollama_base_url = "http://localhost:11434/v1"
# foundry_base_url = "https://your-name.openai.azure.com/"
# aws_region = "us-east-1"
vector_embed_model = "nomic-embed-text"

[db]
url = "postgresql://napyclaw:napyclaw-local@localhost:5432/napyclaw"

[app]
oauth_callback_port = 8765
workspace_dir = "/app/workspace"
groups_dir = "/app/groups"
# max_history_tokens = 6000
```

| Key | Example | What it controls |
|-----|---------|-----------------|
| `llm.default_provider` | `foundry` | Which provider to use on startup (`openai`, `ollama`, `foundry`, `bedrock`) |
| `llm.default_model` | `grok-4-20-non-reasoning` | Deployment/model name for the default provider |
| `llm.openai_base_url` | `https://api.openai.com/v1` | OpenAI API endpoint (change for OpenRouter) |
| `llm.ollama_base_url` | `http://100.x.x.x:11434/v1` | Ollama endpoint, usually over Tailscale |
| `llm.foundry_base_url` | `https://your-name.openai.azure.com/` | Azure AI Foundry endpoint |
| `llm.aws_region` | `us-east-1` | AWS region for Bedrock |
| `llm.vector_embed_model` | `nomic-embed-text` | Ollama embedding model for vector memory |
| `db.url` | `postgresql://napyclaw:napyclaw-local@localhost:5432/napyclaw` | PostgreSQL connection string |
| `app.oauth_callback_port` | `8765` | Local port for OAuth callback server (Phase 2) |
| `app.workspace_dir` | `/app/workspace` | Sandboxed directory for file tools |
| `app.groups_dir` | `/app/groups` | Directory for per-group Markdown memory fallback |
| `app.max_history_tokens` | `6000` | Optional override for conversation history budget |

##### Secrets (Infisical)

Only credentials go in Infisical. The default `docker-compose.yml` starts a self-hosted Infisical instance — no external account required. If you prefer to use Infisical Cloud, swap the `infisical` service URL in your environment variables.

Either way, you need three environment variables on the machine running napyclaw:

```bash
export INFISICAL_CLIENT_ID="your-machine-identity-client-id"
export INFISICAL_CLIENT_SECRET="your-machine-identity-client-secret"
export INFISICAL_PROJECT_ID="your-project-id"
```

Then add these secrets to your Infisical project (environment: `prod`):

| Secret | Example | Required |
|--------|---------|----------|
| `OPENAI_API_KEY` | `sk-...` | Yes (use `placeholder` if not using OpenAI) |
| `OLLAMA_API_KEY` | `ollama` | Yes (use `placeholder` if not using Ollama) |
| `SLACK_BOT_TOKEN` | `xoxb-...` | Yes |
| `SLACK_APP_TOKEN` | `xapp-...` | Yes |
| `SLACK_OWNER_CHANNEL` | `C01234ABCDE` | Yes — channel ID where egress approval messages are sent |
| `EXA_API_KEY` | `...` | Only if using Exa search fallback |
| `TAVILY_API_KEY` | `tvly-...` | Only if using Tavily search fallback |
| `DB_URL` | `postgresql://napyclaw:pass@localhost:5432/napyclaw` | Only if overriding `db.url` in napyclaw.toml |
| `FOUNDRY_API_KEY` | `abc123...` | Only if using Azure AI Foundry |
| `AWS_ACCESS_KEY_ID` | `AKIA...` | Only if using Bedrock with static credentials |
| `AWS_SECRET_ACCESS_KEY` | `wJalrXUtnFEM...` | Only if using Bedrock with static credentials |

#### Step 3: Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Socket Mode** (Settings > Socket Mode > toggle on). Copy the `xapp-` token.
3. Add bot token scopes under **OAuth & Permissions**: `chat:write`, `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `im:write`, `users:read`
4. Subscribe to **Events**: `message.channels`, `message.groups`, `message.im`
5. Install the app to your workspace. Copy the `xoxb-` bot token.
6. Add both tokens to Infisical as `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`.
7. Invite the bot to any channels where you want it available.

#### Step 4: Start the stack

napyclaw requires PostgreSQL + pgvector for all persistence (state, history, memory). A `docker-compose.yml` is included that starts the full stack: database, Redis, Infisical (secrets), SearXNG (search), and the bot services.

If you've previously started any services (partial runs, failed starts), clear stale containers first:

```bash
docker compose down --remove-orphans
```

Then start everything:

```bash
docker compose up -d
```

**First-time Infisical setup:** On the very first run, start only the backend services to bootstrap Infisical before adding secrets:

```bash
docker compose up -d db redis infisical
```

Open `http://localhost:8888`, create an account, create a project (environment: `prod`), and create a Machine Identity under Access Control. Copy the Client ID, Client Secret, and Project ID into your `.env` file, then bring up the full stack.

This starts PostgreSQL on port 5432 with the credentials from `docker-compose.yml`. Set `DB_URL` in Infisical to match:

```
postgresql://napyclaw:napyclaw-local@localhost:5432/napyclaw
```

Then pull the embedding model in Ollama: `ollama pull nomic-embed-text`

#### Step 5: Install and run

napyclaw runs on Linux (native, WSL, or Docker). The Infisical Python SDK does not work on native Windows.

```bash
git clone <this-repo>
cd napyclaw
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m spacy download en_core_web_lg   # for ContentShield PII detection

python -m napyclaw
```

#### Step 6: Talk to your bot

In any Slack channel where the bot is invited, mention it:

> @General_napy hello!

The bot will introduce itself and ask if you'd like to give it a different name. The first person to trigger the bot in a channel becomes the channel owner (controls renaming, model switching, and nickname clearing).

### Search providers

napyclaw supports multiple search backends, tried in order with automatic fallback. Configure the priority in `napyclaw.toml`:

```toml
[search]
providers = ["searxng", "exa", "tavily"]
searxng_url = "http://searxng:8080"
```

#### SearXNG (recommended — included in docker-compose)

SearXNG is a self-hosted meta-search engine that aggregates Google, Bing, and DuckDuckGo. It's included in `docker-compose.yml` and starts automatically alongside the database — no API key required, no ToS restrictions, no query data sent to third parties.

The default `searxng/settings.yml` in this repo enables JSON output and runs without rate limiting. Change `secret_key` before exposing it on a non-local network.

#### Exa (optional fallback)

Exa is a neural search API built explicitly for LLM use cases, with no storage restrictions. Get a key at [exa.ai](https://exa.ai) and add it to Infisical as `EXA_API_KEY`. Free tier: 1,000 requests/month.

#### Tavily (optional fallback)

Tavily is an AI-native search API designed for agents, with explicit permission for AI use. Get a key at [tavily.com](https://tavily.com) and add it to Infisical as `TAVILY_API_KEY`. Free tier available.

**Why not Brave Search?** Brave's ToS prohibits storing or caching search results, which conflicts directly with napyclaw's vector memory model. It also prohibits AI training/eval use and reserves broad rights over query data. SearXNG, Exa, and Tavily all explicitly permit AI agent use cases — Brave does not.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run on any platform (Windows, Linux, macOS) — all external dependencies are mocked. The full application runs in WSL or Docker where Infisical and all system dependencies are available.
