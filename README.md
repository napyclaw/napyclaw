# napyclaw

A personal AI agent framework in Python, built to be small enough to understand and easy to customize. Runs on Ollama over Tailscale for local inference, or any OpenAI-compatible API (OpenAI, OpenRouter) with your own keys. Ships with Slack out of the gate.

Inspired by [nanoclaw](https://github.com/NateBJones-Projects/nanoclaw) and [OB1 (Open Brain)](https://github.com/NateBJones-Projects/OB1) for knowledge/embedding architecture.

## Key Features

- **Dual LLM support** — Ollama (local, over Tailscale) or OpenAI-compatible cloud APIs. Switch models per channel at runtime.
- **Slack Socket Mode** — runs as a Slack bot with per-channel identity (custom names, nicknames, owner permissions).
- **Semantic memory** — PostgreSQL + pgvector for vector search, with per-group Markdown files as fallback. Embeddings via Ollama.
- **Tool system** — web search (Brave), file read/write, scheduled tasks, messaging, bot identity management. All exposed to the LLM as function calls.
- **EgressGuard** — outbound domain policy engine. Every HTTP request passes through trust tiers (threat intel, Majestic top 10k, LLM judge) before leaving the process. Domain-only — never sees payload.
- **ContentShield** — scans all content before storage for credentials (detect-secrets) and PII (Presidio). Redacts secrets and SSNs; allows phone/email through.
- **Private sessions** — ephemeral DM conversations with no persistence. Nothing stored, nothing remembered.
- **Scheduled tasks** — cron, interval, or one-shot prompts with retry and exponential backoff.
- **Secrets via Infisical** — all config loaded from Infisical Cloud at startup. No `.env` files.

## Architecture

napyclaw is plain OOP Python — no frameworks, no magic. Each class has one job.

```
Message arrives (Slack Socket Mode)
    |
    v
SlackChannel --- normalizes to Message dataclass
    |
    v
NapyClaw.handle_message()
    |-- ContentShield.scan() --- redacts secrets/PII before storage
    |-- Store message in SQLite
    |-- Check trigger (@name, @nickname, or Slack mention)
    |-- Get or create GroupContext for this channel
    |-- GroupQueue.run() --- one agent per channel at a time
         |
         v
    Agent.run()
         |-- Build system prompt (memory + context)
         |-- LLMClient.chat() --- OpenAI or Ollama
         |-- If tool calls: execute tools, loop (max 10)
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
  config.py            Config from Infisical Cloud
  db.py                SQLite persistence (aiosqlite, WAL mode)
  memory.py            MemoryBackend: VectorMemory (pgvector), MarkdownMemory, NullMemory
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
    web_search.py      Brave Search API
    file_ops.py        Sandboxed file read/write
    messaging.py       Send messages to channels
    scheduling.py      Create/list/cancel scheduled tasks
    identity.py        Rename bot, add nicknames, switch models
    recipes/
      base.py          RecipeTool base (Phase 2 scaffold)
```

### How the pieces connect

**Config** loads all secrets from Infisical at startup into a typed dataclass. Nothing else touches Infisical.

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
| Host RCE | ❌ Critical | ✅ Ephemeral containers | ✅ OpenShell (Landlock + seccomp + netns) | ⚠️ No container — process-level only. File tools sandboxed to `workspace_dir` with path traversal checks. |
| Cross-agent leakage | ❌ Shared memory | ✅ Isolated sessions | ✅ Per-agent sandboxed environments | ⚠️ Per-group agents with separate history. Private sessions use NullMemory. No OS-level isolation between groups. |
| Credential theft | ❌ No protection | ⚠️ API key still mounted | ⚠️ Privacy router intercepts inference — credentials still exist inside sandbox | ✅ Infisical loads secrets at startup; held in Config only. ContentShield redacts credentials before any storage. Tools never expose secrets to the LLM. |
| Prompt injection | ❌ Full host impact | ⚠️ Container-scoped | ⚠️ Still architecturally unsolved — damage scoped to sandbox | ⚠️ Unsolved. Damage limited by tool permissions (owner-only gates on rename/model switch) and EgressGuard blocking unknown domains. |
| Outbound exfiltration | ❌ Unrestricted | ❌ Unrestricted | ✅ Egress control + operator approval flow | ✅ EgressGuard: threat intel blocklist, Majestic top 10k allowlist, LLM judge, verdict cache. Domain-only — never inspects payload. |
| Supply chain | ❌ 500k LoC unreviewed | ✅ 500 LoC auditable | ⚠️ Adds NVIDIA stack — attack surface grows | ✅ ~2k LoC core, plain Python, no frameworks. Dependencies are well-known libraries (openai, httpx, slack-bolt, aiosqlite). |
| Exposed network port | ❌ 0.0.0.0 default | ✅ No listener | ✅ OpenShell netns isolation | ✅ No listener. Slack Socket Mode is outbound-only. OAuth callback server is opt-in and local. |
| Audit trail | ❌ None | ❌ None | ✅ Built-in policy enforcement + audit logging | ✅ shield_log (credential/PII detections), egress_verdicts (domain decisions), egress_log (every outbound check), all messages stored with redaction metadata. |
| Data → cloud leakage | ❌ No controls | ⚠️ Depends on config | ✅ Privacy router keeps internal data local | ✅ EgressGuard on all outbound HTTP. Can run fully local (Ollama + local Postgres). Cloud LLM is opt-in, not default. |

### What napyclaw does well

**Credential hygiene.** Secrets live in Infisical, not in files. ContentShield scans everything before storage — if someone pastes an API key into Slack, it gets redacted before it reaches the database or the LLM. The original is never stored anywhere.

**Egress control.** napyclaw is the only project in this lineage (besides NemoClaw) that gates outbound network access. Every HTTP request passes through EgressGuard before leaving the process. Unknown domains hit an LLM judge and can be escalated to the owner for approval.

**Auditability.** The codebase is small enough to read in an afternoon. There are no plugin systems, no dynamic code loading, no eval. Every tool is a Python class with an `execute()` method that returns a string.

**Data locality.** Default configuration keeps everything on your machines — Ollama for inference, local PostgreSQL for memory, SQLite for state. Cloud LLM providers are available but opt-in, and all outbound calls pass through EgressGuard.

### What remains vulnerable

**Prompt injection** is unsolved at every layer of this architecture. A well-crafted message can convince the agent to misuse its tools within the permissions it already has. EgressGuard and owner-only tool gates limit the blast radius, but a prompt-injected agent can still search the web, write files to the workspace, and send messages to channels it has access to.

**No process isolation.** Unlike NanoClaw (Docker) or NemoClaw (Landlock + seccomp), napyclaw runs as a regular Python process. If the process is compromised, the attacker has access to everything the process can see — including the Config object holding all secrets in memory. This is an acceptable tradeoff for a personal tool running on your own tailnet; it would not be acceptable in a shared or multi-tenant environment.

**Secrets in memory.** Infisical keeps secrets out of files, but `Config.from_infisical()` loads them all into a Python dataclass at startup. A memory dump of the napyclaw process would expose every API key. This is inherent to any application that needs to use secrets at runtime.

### Who this is for

napyclaw assumes you trust the machine it runs on and the Slack workspace it connects to. It's designed for a single person running their own agent on their own infrastructure. If you need multi-tenant isolation, sandboxed execution, or zero-trust agent architecture, look at NemoClaw or similar projects.

## Installation

There are two ways to get napyclaw running: have an AI agent walk you through it, or do it yourself. Either way, you'll make the same set of choices below.

### AI-guided install

If you have access to Claude, ChatGPT, or any capable coding agent, give it this README and ask it to help you set up napyclaw. The agent can walk you through each decision, generate your Infisical secrets, help you create your Slack app, and troubleshoot along the way. This is the recommended path if you haven't done this kind of setup before.

Prompt to get started:
> "I want to set up napyclaw. Here's the README. Walk me through each step — I need help choosing an LLM, setting up Infisical, and creating the Slack app."

### Manual install

If you're comfortable with Python, Docker, and API keys, here's what you need to do.

#### Step 1: Choose your LLM

You need at least one. You can use both and switch between them per channel at runtime.

| Option | What you need | Best for |
|--------|--------------|----------|
| **Ollama (local)** | A machine running Ollama, reachable over Tailscale. Pull your model (`ollama pull llama3.3`). Set `num_ctx` explicitly — Ollama defaults to 2048 regardless of model capability. | Privacy, no API costs, full control |
| **OpenAI** | An OpenAI API key from platform.openai.com | Best tool-calling models, no hardware required |
| **OpenRouter** | An OpenRouter API key from openrouter.ai | Access to many models (Claude, Gemini, Llama, etc.) through one API |

For Ollama over Tailscale: install Tailscale on both the machine running Ollama and the machine running napyclaw. The Ollama base URL will be something like `http://100.x.x.x:11434/v1`.

**Important:** If using Ollama, set `num_ctx` in your model's Modelfile or via the API. The default 2048 context window severely limits conversation history. For llama3.3, `num_ctx=65536` is recommended if your hardware supports it.

#### Step 2: Set up Infisical

napyclaw loads all configuration from Infisical — there are no `.env` files. You have two options:

| Option | What it is | Best for |
|--------|-----------|----------|
| **Infisical Cloud** | Free tier at infisical.com. Create an account, create a project, add a machine identity. | Simplest setup, works everywhere |
| **Self-hosted Infisical** | Run Infisical locally via Docker Compose. See [Infisical self-host docs](https://infisical.com/docs/self-hosting/overview). | Full control, no cloud dependency |

Either way, you need three environment variables on the machine running napyclaw:

```bash
export INFISICAL_CLIENT_ID="your-machine-identity-client-id"
export INFISICAL_CLIENT_SECRET="your-machine-identity-client-secret"
export INFISICAL_PROJECT_ID="your-project-id"
```

Then add these secrets to your Infisical project (environment: `prod`):

| Secret | Example | Required |
|--------|---------|----------|
| `OPENAI_API_KEY` | `sk-...` | Yes |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Yes |
| `OLLAMA_BASE_URL` | `http://100.x.x.x:11434/v1` | Yes |
| `OLLAMA_API_KEY` | `ollama` | Yes (any non-empty string) |
| `DEFAULT_MODEL` | `llama3.3:latest` | Yes |
| `DEFAULT_PROVIDER` | `ollama` or `openai` | Yes |
| `SLACK_BOT_TOKEN` | `xoxb-...` | Yes |
| `SLACK_APP_TOKEN` | `xapp-...` | Yes |
| `BRAVE_API_KEY` | `BSA...` | Yes |
| `VECTOR_DB_URL` | `postgresql://localhost:5432/napyclaw` | No (omit for Markdown memory) |
| `VECTOR_EMBED_MODEL` | `nomic-embed-text` | Yes |
| `OAUTH_CALLBACK_PORT` | `8765` | Yes |
| `WORKSPACE_DIR` | `/home/user/napyclaw/workspace` | Yes |
| `DB_PATH` | `/home/user/napyclaw/napyclaw.db` | Yes |
| `GROUPS_DIR` | `/home/user/napyclaw/groups` | Yes |

**Note:** If you only plan to use OpenAI, you still need the Ollama fields (use placeholder values). Same in reverse — if you only use Ollama, provide a placeholder OpenAI key.

#### Step 3: Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Socket Mode** (Settings > Socket Mode > toggle on). Copy the `xapp-` token.
3. Add bot token scopes under **OAuth & Permissions**: `chat:write`, `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `im:write`, `users:read`
4. Subscribe to **Events**: `message.channels`, `message.groups`, `message.im`
5. Install the app to your workspace. Copy the `xoxb-` bot token.
6. Add both tokens to Infisical as `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`.
7. Invite the bot to any channels where you want it available.

#### Step 4: Choose your memory backend

| Option | What you need | Behavior |
|--------|--------------|----------|
| **Markdown (default)** | Nothing. Leave `VECTOR_DB_URL` empty in Infisical. | Each channel gets a `MEMORY.md` file. Full file injected into every prompt. Simple but doesn't scale. |
| **pgvector** | PostgreSQL with pgvector extension. Docker: `ankane/pgvector` image. | Semantic search over memories. Only relevant context injected per turn. Scales well. |

To set up pgvector:

```bash
# Run PostgreSQL + pgvector in Docker
docker run -d --name napyclaw-db \
  -e POSTGRES_DB=napyclaw \
  -e POSTGRES_PASSWORD=your-password \
  -p 5432:5432 \
  ankane/pgvector

# Apply the schema
psql postgresql://postgres:your-password@localhost:5432/napyclaw \
  -f napyclaw/migrations/001_thoughts.sql
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

### Optional: Brave Search API

napyclaw uses the Brave Search API for web search. Get a free API key at [brave.com/search/api](https://brave.com/search/api/) and add it to Infisical as `BRAVE_API_KEY`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests run on any platform (Windows, Linux, macOS) — all external dependencies are mocked. The full application runs in WSL or Docker where Infisical and all system dependencies are available.
