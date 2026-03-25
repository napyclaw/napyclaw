# napyclaw Design Spec
_Date: 2026-03-24_

## Overview

napyclaw is a Python reimagining of nanoclaw — a multi-channel AI agent framework. The goal is readable, idiomatic Python that any Python developer can pick up and customize. It uses the OpenAI API (and any OpenAI-compatible endpoint) rather than the Claude SDK, supports Ollama over Tailscale for local inference, and ships with Slack (Socket Mode) out of the gate. WhatsApp and Telegram are planned but out of scope for this spec.

It is designed primarily for **personal use** — one owner per napyclaw instance. Knowledge management is handled by an optional integration with **[napyclaw/OB1](https://github.com/napyclaw/OB1)**, a fork of [Open Brain](https://github.com/NateBJones-Projects/OB1) that adds group-scoped memory and per-user OAuth credential management. OB1 is optional — napyclaw falls back to per-group `MEMORY.md` files when OB1 is not configured.

Core philosophy: **small enough to understand**. OOP architecture with clear class responsibilities, no magic, no frameworks beyond what's needed.

---

## Secrets Management

napyclaw requires **Infisical Cloud** for all secrets and configuration. There is no `.env` fallback — if Infisical is unreachable at startup, napyclaw exits with a clear error message:
`"Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET environment variables."`

The only credentials that live outside Infisical are the two bootstrap values needed to authenticate with Infisical itself (`INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET`), stored as system environment variables.

All other secrets and configuration (API keys, Slack tokens, Ollama URL, workspace paths) are loaded from Infisical at startup via the `infisical-python` SDK and held in a typed `Config` object for the process lifetime.

---

## File Structure

```
napyclaw/
├── __main__.py          # Entry point — builds and starts NapyClaw
├── app.py               # NapyClaw class — orchestrator, wires everything together
├── config.py            # Config class — loads from Infisical Cloud, typed attributes
├── db.py                # Database class — aiosqlite, all persistence operations
├── models/
│   ├── base.py          # LLMClient abstract base class, ChatResponse dataclass
│   ├── openai_client.py # OpenAIClient — cloud API (OpenAI, OpenRouter)
│   └── ollama_client.py # OllamaClient — Ollama over Tailscale base URL
├── agent.py             # Agent class — conversation state, tool loop, response streaming
├── tools/
│   ├── base.py          # Tool abstract base class — schema + execute()
│   ├── web_search.py    # WebSearchTool (Brave Search API)
│   ├── file_ops.py      # FileReadTool, FileWriteTool
│   ├── messaging.py     # SendMessageTool
│   ├── scheduling.py    # ScheduleTaskTool
│   └── identity.py      # RenameBot, AddNickname, SwitchModel tools
├── channels/
│   ├── base.py          # Channel abstract base class + Message dataclass
│   └── slack.py         # SlackChannel — Socket Mode via slack-bolt
├── scheduler.py         # Scheduler class — APScheduler, polls DB, fires due tasks
├── memory.py            # MemoryBackend ABC, MarkdownMemory, OB1Memory implementations
└── oauth.py             # OAuthCallbackServer — lightweight HTTP listener for OAuth flows
```

---

## Core Data Types

### Message

Defined in `channels/base.py`. Normalized across all channel implementations.

```python
@dataclass
class Message:
    group_id: str           # Channel/room identifier (e.g. Slack channel ID "C0123ABC")
    channel_name: str       # Human-readable channel name (e.g. "general")
                            # SlackChannel fetches this via Slack API conversations.info on
                            # first encounter and caches group_id → name in memory.
                            # On API failure, falls back to group_id as the name.
    sender_id: str          # Platform user ID (e.g. Slack user ID "U0123ABC")
    sender_name: str        # Display name of sender
    text: str               # Raw message text
    timestamp: str          # ISO-8601 UTC timestamp
    channel_type: str       # "slack" (v1 only; "telegram"/"whatsapp" added in future channels)
```

### ChatResponse

Defined in `models/base.py`. Normalizes OpenAI and Ollama responses.

```python
@dataclass
class ChatResponse:
    text: str | None                    # Final text response (None if tool calls present)
    tool_calls: list[ToolCall] | None   # Tool calls requested by the LLM
    finish_reason: str                  # "stop" | "tool_calls" | "length"

@dataclass
class ToolCall:
    id: str             # Tool call ID (passed back in tool result)
    name: str           # Tool name
    arguments: dict     # Parsed JSON arguments
```

### ScheduledTask

Defined in `db.py`. The schema for a scheduled task record.

```python
@dataclass
class ScheduledTask:
    id: str                         # UUID
    group_id: str                   # Which group/channel this task belongs to
    owner_id: str                   # Slack user ID of task creator
    prompt: str                     # The prompt to run
    schedule_type: str              # "cron" | "interval" | "once"
    schedule_value: str             # Cron expression, interval in seconds, or ISO-8601 datetime
    model: str | None               # Override model for this task (e.g. "gpt-4o")
    provider: str | None            # Override provider ("openai" | "ollama") — None = use group default
    status: str                     # "active" | "paused" | "completed" | "failed"
    next_run: str | None            # ISO-8601 UTC, computed by scheduler
    retry_count: int                # Current retry count (reset after successful run)
    created_at: str                 # ISO-8601 UTC
```

---

## Class Designs

### Config

Loaded once at startup from Infisical Cloud. All fields are required; missing fields raise `ConfigError` with the field name.

```python
class Config:
    # LLM
    openai_api_key: str
    openai_base_url: str          # e.g. https://api.openai.com/v1 or OpenRouter
    ollama_base_url: str          # e.g. http://100.x.x.x:11434/v1 (Tailscale IP)
    ollama_api_key: str           # Any non-empty string; Ollama accepts "ollama" as placeholder
    default_model: str            # "llama3.3:latest"
    default_provider: str         # "ollama" | "openai"

    # Slack
    slack_bot_token: str          # xoxb- token
    slack_app_token: str          # xapp- token (Socket Mode)

    # Web search
    brave_api_key: str            # Brave Search API key

    # OB1 knowledge backend (optional — MarkdownMemory used if absent)
    supabase_url: str | None      # e.g. https://xyz.supabase.co
    supabase_service_role_key: str | None
    ob1_access_key: str | None    # x-brain-key for OB1 MCP server

    # OAuth callback server
    oauth_callback_port: int      # Default 8765

    # Paths (stored in Infisical as config, not secrets)
    workspace_dir: Path           # Where file tools read/write. Created if missing.
    db_path: Path                 # SQLite file path. Created if missing.
    groups_dir: Path              # Parent dir for per-group MEMORY.md files. Created if missing.

    # Agent tuning
    max_history_tokens: int       # Default 8000. Estimated by character count (4 chars ≈ 1 token).

    @classmethod
    def from_infisical(cls) -> "Config":
        # Uses INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET from env
        # Raises ConfigError with clear message if Infisical unreachable or field missing
        ...
```

### LLMClient hierarchy

```python
class LLMClient(ABC):
    model: str
    provider: str   # "openai" | "ollama"

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse: ...
    async def stream(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[str]: ...

class OpenAIClient(LLMClient):
    # Uses openai Python SDK pointing at openai_base_url
    provider = "openai"

class OllamaClient(LLMClient):
    # Uses openai Python SDK with base_url = ollama_base_url, api_key = ollama_api_key
    provider = "ollama"
```

`chat()` returns a full `ChatResponse`. `stream()` yields text tokens incrementally and is used only for final text responses (no tool calls present). Tool calls always use `chat()` since the full response must be received before tools can execute.

**Streaming strategy:** `Agent.run()` always returns `str`. Internally, it uses `chat()` for tool-call turns and `stream()` for the final text turn — buffering all tokens into a single string before returning. This keeps `Agent.run()`'s interface simple while still reducing time-to-first-token latency on the LLM side. Slack receives one complete message per agent turn (no edit-in-place).

### GroupContext

```python
@dataclass
class GroupContext:
    group_id: str
    default_name: str        # "{Channel}_napy" — immutable, always a valid trigger
    display_name: str        # Official name, owner-only to change
    nicknames: list[str]     # Anyone can add; all accumulate; all are valid triggers
    owner_id: str            # Slack user ID of the first person to @-mention the bot
    active_client: LLMClient # Current LLM client (Ollama or OpenAI)
    is_first_interaction: bool
    agent: Agent             # The Agent instance for this group (holds conversation history)
```

`GroupContext` is persisted to the DB (all fields except `active_client` and `agent`, which are reconstructed at startup from stored `provider`/`model` values). `NapyClaw` holds a dict of `group_id → GroupContext` in memory.

### Name management rules

- **`default_name`**: derived when a new group is first seen. Formula: take the Slack channel name (always lowercase, e.g. `general`, `dev-ops`), capitalize the first letter, append `_napy`. Examples: `general` → `General_napy`, `dev-ops` → `Dev-ops_napy`. Never changes. Always a trigger.
- **`display_name`**: initially equals `default_name`. Can only be changed by `owner_id` via the `RenameBot` tool. First letter capitalized, enforced in the tool. Always a trigger.
- **`nicknames`**: any group member can add one via natural language ("I'll call you Kev"). Multiple nicknames accumulate. Only the `owner_id` can clear all nicknames via the `ClearNicknames` tool (natural language: "forget all your nicknames"). Individual nickname removal is not supported. All nicknames trigger the bot. Bot uses the most recently added nickname when referring to itself in conversation.

### Trigger matching

Trigger matching is performed in `NapyClaw.handle_message()` before the message is passed to the agent. A message triggers the bot if its text contains `@{name}` (case-insensitive) for any of: `default_name`, `display_name`, or any entry in `nicknames`.

For Slack Socket Mode, the bot also receives Slack's native mention payload (`<@UXXXXXXX>`). Both forms are checked: raw `@name` text match AND Slack mention payload match against the bot's own user ID.

Messages that do not match any trigger are stored in the DB (for context) but do not invoke the agent.

### `owner_id` definition

`owner_id` is set to the `sender_id` of the **first message that triggers the bot** in a given group. "First message that triggers the bot" means the first `Message` where trigger matching succeeds, after the bot is added to the channel. This is stored in the DB on `GroupContext` creation and never changes.

### First interaction flow

1. New `group_id` seen → `GroupContext` created with `default_name = display_name = "{Channel}_napy"`, `is_first_interaction = True`, `owner_id = sender_id` of the triggering message
2. Agent system prompt includes: `"Your name is {display_name}. This is your first conversation in this channel. Introduce yourself and ask if the user would like to give you a different name."`
3. After agent responds, `is_first_interaction = False`, persisted to DB

### Agent

```python
class Agent:
    def __init__(
        self,
        client: LLMClient,
        tools: list[Tool],
        memory: MemoryBackend,
        context: GroupContext,
        max_tool_iterations: int = 10,
    ): ...

    async def run(self, user_message: str) -> str:
        # 1. Build system prompt from MemoryBackend + context (names, current model, etc.)
        # 2. Append user message to self.history (list of OpenAI message dicts)
        # 3. Call client.chat(self.history, tools=self.tool_schemas)
        # 4. If response.tool_calls:
        #      execute each tool → append tool results to self.history → go to 3
        #      raise AgentLoopError if max_tool_iterations exceeded
        # 5. Append assistant response to self.history
        # 6. Persist self.history to DB
        # 7. Return response.text
```

**Conversation history persistence:** `self.history` (list of OpenAI-format message dicts) is persisted to the DB as JSON after every turn. On startup, `NapyClaw` reconstructs each group's `Agent` with its stored history. History is pruned when it exceeds `config.max_history_tokens` (default 8000, estimated at 4 chars per token): the system message is always kept. Pruning removes complete exchange blocks from oldest-first — a block is defined as one `user` message plus all subsequent `assistant` and `tool` messages up to the next `user` message. This ensures tool call / tool result pairs are never split.

### Tool

```python
class Tool(ABC):
    name: str           # Snake_case, matches OpenAI function name
    description: str    # Shown to LLM in tool schema
    parameters: dict    # JSON Schema for arguments

    @abstractmethod
    async def execute(self, **kwargs) -> str: ...
    # Returns a string result (shown to LLM as tool result content)
    # On error, returns an error string (never raises — LLM handles errors gracefully)
```

**Built-in tools for v1:**

| Tool class | File | Permission | Parameters | Returns |
|---|---|---|---|---|
| `WebSearchTool` | `tools/web_search.py` | Any user | `query: str` | Top 5 results: title + URL + snippet |
| `FileReadTool` | `tools/file_ops.py` | Any user | `path: str` (relative to `workspace_dir`) | File contents as string |
| `FileWriteTool` | `tools/file_ops.py` | Any user | `path: str`, `content: str` | `"Written: {path}"` |
| `SendMessageTool` | `tools/messaging.py` | Any user | `text: str`, `group_id: str` (optional, defaults to current group) | `"Sent"` |
| `ScheduleTaskTool` | `tools/scheduling.py` | Any user | `action: str` (`"create"/"list"/"cancel"`), `prompt: str` (create only), `schedule_type: str` (create only), `schedule_value: str` (create only), `model: str` (optional), `provider: str` (optional), `task_id: str` (cancel only) | JSON summary of task(s) |
| `RenameBot` | `tools/identity.py` | `owner_id` only | `new_name: str` | `"Renamed to {new_name}"` |
| `AddNickname` | `tools/identity.py` | Any user | `nickname: str` | `"Nickname '{nickname}' added"` |
| `ClearNicknames` | `tools/identity.py` | `owner_id` only | _(none)_ | `"All nicknames cleared"` |
| `SwitchModel` | `tools/identity.py` | `owner_id` only | `provider: str` (`"openai"/"ollama"`), `model: str` | `"Switched to {provider}/{model}"` |

Permission enforcement is done inside each tool's `execute()` — the tool receives the `sender_id` of the current message and checks it against `GroupContext.owner_id`. Unauthorized attempts return an error string (e.g. `"Only the channel owner can rename me."`).

Constructor injection per tool:
- `FileReadTool`, `FileWriteTool`: receive `Config` (for `workspace_dir`) and `MemoryBackend` (for resolving `MEMORY.md` path)
- `SendMessageTool`: receives `Channel` and `GroupContext`
- `ScheduleTaskTool`: receives `Database` and `GroupContext`
- `RenameBot`, `AddNickname`, `ClearNicknames`, `SwitchModel`: receive `Database` and `GroupContext`
- `WebSearchTool`: receives `Config` (for `brave_api_key`)

**FileWriteTool path resolution:** If `path == "MEMORY.md"` (case-insensitive), the write target is `MemoryBackend.path` for the current group (not `workspace_dir`). All other paths are resolved relative to `workspace_dir` and must not escape it (path traversal check: reject any path containing `..`).

**WebSearchTool:** Uses the Brave Search API (`https://api.search.brave.com/res/v1/web/search`). Brave API key stored in Infisical as `brave_api_key`.

**ScheduleTaskTool — `list` action:** Returns all tasks for the current `group_id` as a JSON array with fields: `id`, `prompt` (truncated to 80 chars), `schedule_type`, `schedule_value`, `status`, `next_run`. This gives the LLM the task ID needed for cancellation.

**ScheduleTaskTool — `cancel` action:** Requires `task_id`. Sets `status = "paused"` in DB. Returns error if task not found or belongs to a different group.

### MemoryBackend (knowledge management)

napyclaw supports two memory backends, selected automatically at startup based on whether `SUPABASE_URL` is present in Infisical.

```python
class MemoryBackend(ABC):
    @abstractmethod
    async def search(self, query: str, group_id: str, top_k: int = 5) -> list[str]: ...
    # Returns relevant memory strings to inject into the agent system prompt

    @abstractmethod
    async def capture(self, content: str, group_id: str | None = None) -> None: ...
    # group_id=None → global memory; set → group-scoped memory

    @abstractmethod
    async def load_context(self) -> str: ...
    # Returns a static context string (recent/summary facts, not search results)
```

**`MarkdownMemory(MemoryBackend)`** — fallback when OB1 not configured:
- Reads/writes `{groups_dir}/{group_id}/MEMORY.md`
- `search()` returns full file contents (no semantic filtering)
- `capture()` appends a line to the file
- Injected into agent system prompt in full each turn

**`OB1Memory(MemoryBackend)`** — used when `SUPABASE_URL` is present:
- Wraps the napyclaw/OB1 Supabase HTTP API
- `search()` generates an embedding for the query, calls `match_thoughts` with group-scoped + global (null `group_id`) results merged and re-ranked by similarity
- `capture()` posts to `capture_thought` with `group_id` in metadata
- Only semantically relevant thoughts are injected per turn — solves the context-window scaling problem of MEMORY.md

**Backend selection:** `NapyClaw` checks `Config` at startup. `Agent` only ever holds a `MemoryBackend` reference and is unaware of which backend is active.

**`SearchMemoryTool` / `CaptureMemoryTool`** added to the tool set — explicit tools the LLM can call to search or save memories beyond what is auto-retrieved. Both delegate to the group's `MemoryBackend`.

**napyclaw/OB1 fork** (changes tracked in the OB1 repo):
- `thoughts` table: `group_id UUID` column added (nullable — null = global)
- `match_thoughts`: accepts optional `group_id`, returns union of group-scoped + global results ordered by similarity
- `capture_thought`: accepts optional `group_id` in metadata
- Users may point napyclaw at their own OB1 deployment via `SUPABASE_URL` — the fork is the recommended path but not required

### OAuth Credential Management

A lightweight `OAuthCallbackServer` (defined in `oauth.py`) starts alongside the Slack bot at boot. It listens on a configurable port (default 8765, stored in Infisical as `OAUTH_CALLBACK_PORT`) for OAuth redirect callbacks.

```python
class OAuthCallbackServer:
    async def start(self, port: int) -> None: ...

    async def get_authorization_url(self, provider: str, user_id: str) -> str: ...
    # Generates OAuth URL; encodes provider + user_id in state parameter

    async def handle_callback(self, code: str, state: str) -> None: ...
    # Exchanges code → tokens → writes refresh_token directly to Infisical
    # Key: OAUTH_{PROVIDER}_{USER_ID}_REFRESH_TOKEN
    # e.g. OAUTH_GOOGLE_U0123ABC_REFRESH_TOKEN
    # Token value never logged, never stored in process memory beyond the exchange
```

**Credential isolation — how keys stay out of the agent:**
- OAuth tokens: `OAuthCallbackServer → Infisical` directly (not through any agent-accessible path)
- At recipe execution: `Infisical → Config → RecipeTool constructor` — credentials held by the tool object, never returned by `execute()`
- `FileReadTool` sandboxed to `workspace_dir` — cannot traverse to credential storage
- Tool `execute()` returns only outcome strings (`"Imported 1,847 thoughts"`) — never token values
- No tool exposes `Config` values or Infisical secrets to the LLM

**OAuth flow (user perspective):**
```
User: "@General_napy connect my Google account"
Bot:  "Click here to connect Google: https://accounts.google.com/o/oauth2/auth?..."
User: [completes Google login in browser]
Bot:  "Google connected. You can now say 'import my Google activity'."
```

**Per-user scoping:** Credentials are keyed by Slack `sender_id`. Each user who connects a service gets their own token. Recipe tools look up credentials by the `sender_id` of the requesting message.

**Supported OAuth providers (v1 design — implementations in Phase 2):**
- Google (Google Activity, Gmail, Drive)
- Microsoft Entra ID (OneDrive, Outlook)

**Static credentials** (no OAuth) use the same Infisical key pattern:
`{PROVIDER}_{USER_ID}_KEY` — e.g. `CHATGPT_U0123ABC_EXPORT_PATH`

### Recipe Tools (Phase 2)

Recipe tools are `Tool` subclasses in `tools/recipes/`. Each wraps a napyclaw/OB1 import recipe as an agent-callable action. Phase 1 ships without recipe implementations — the `RecipeTool` base class and directory structure are in place, files are added per recipe in Phase 2.

```python
class RecipeTool(Tool, ABC):
    async def get_credential(self, provider: str, sender_id: str) -> str | None:
        # Reads from Config (loaded from Infisical at startup)
        # Returns None if not found
        ...

    # If credential missing, execute() returns:
    # "I don't have your {provider} credentials yet. Say 'connect {provider}' to set it up."
```

```
tools/recipes/
├── base.py             # RecipeTool base class
├── chatgpt.py          # ImportChatGPTTool
├── obsidian.py         # ImportObsidianTool
├── google_activity.py  # ImportGoogleActivityTool
└── ...                 # One file per OB1 recipe
```

### Channel

```python
class Channel(ABC):
    channel_type: str   # "slack" | "telegram" | "whatsapp"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send(self, group_id: str, text: str) -> None: ...

    @abstractmethod
    async def set_typing(self, group_id: str, on: bool) -> None: ...

    def register_handler(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self._handler = handler
    # Channel implementations call self._handler(message) when a message arrives
```

`SlackChannel` uses `slack-bolt` async with Socket Mode. Incoming Slack events are normalized into `Message` objects and passed to `self._handler`. `group_id` in `Message` maps to the Slack channel ID (e.g. `C0123ABC`). The bot's own Slack user ID is stored on `SlackChannel` after connect, used for mention matching.

### GroupQueue

Defined in `app.py`. Ensures only one agent runs per group at a time, preventing interleaved responses.

```python
class GroupQueue:
    # Internally: dict[group_id, asyncio.Lock]
    # One asyncio.Lock per group_id, created on first use

    async def run(self, group_id: str, coro: Awaitable) -> Any:
        # Acquires the lock for group_id, runs coro, releases lock
        # If a second message arrives while the first is processing,
        # it waits for the lock — messages are processed in order, not dropped
```

`NapyClaw` holds a single `GroupQueue` instance. All agent invocations go through `queue.run(group_id, agent.run(prompt))`.

### NapyClaw (orchestrator)

```python
class NapyClaw:
    def __init__(self, config: Config): ...

    async def start(self) -> None:
        # 1. Init DB (create file and schema if missing)
        # 2. Create workspace_dir and groups_dir if missing
        # 3. Load all GroupContexts from DB, reconstruct Agents with stored history
        # 4. Connect channels, register handle_message as handler
        # 5. Start Scheduler
        # 6. Run forever (asyncio event loop)

    async def handle_message(self, msg: Message) -> None:
        # 1. Store message in DB
        # 2. Check trigger — if no match, return
        # 3. Get GroupContext (or create new one if first time seeing group_id)
        # 4. Schedule: queue.run(msg.group_id, _run_agent(context, msg))
        #    Returns immediately; typing + send happen inside _run_agent

    async def _run_agent(self, context: GroupContext, msg: Message) -> None:
        # Runs inside GroupQueue lock — only one per group at a time
        channel = self._channel_for(msg.group_id)
        try:
            await channel.set_typing(msg.group_id, True)
            response = await context.agent.run(msg.text)
            await channel.send(msg.group_id, response)
        except AgentLoopError:
            await channel.send(msg.group_id, "I got stuck in a loop. Please try rephrasing your request.")
        except LLMUnavailableError as e:
            await channel.send(msg.group_id, str(e))
        finally:
            await channel.set_typing(msg.group_id, False)
        # try/except/finally: set_typing(False) always runs regardless of error
```

---

## Data Flow

### Inbound message → agent response

```
Slack event (Socket Mode)
    │
    ▼
SlackChannel — normalizes to Message (fetches channel_name via API if new group_id),
               calls self._handler(message)
    │
    ▼
NapyClaw.handle_message(msg)
    │  1. Store message in DB
    │  2. Check trigger (@default_name, @display_name, or @any_nickname) — case-insensitive
    │     Also match Slack native mention <@BOT_USER_ID>
    │  3. Get or create GroupContext for msg.group_id
    │  4. queue.run(msg.group_id, _run_agent(context, msg))  ← returns immediately
    ▼
_run_agent(context, msg)  ← runs inside GroupQueue lock
    │  1. channel.set_typing(msg.group_id, True)
    │  2. context.agent.run(msg.text)
    │  3. channel.send(msg.group_id, response)
    │  4. channel.set_typing(msg.group_id, False)  ← always, via try/finally
    ▼
Agent.run(msg.text)
    │  1. Build system prompt (MemoryBackend contents + context: names, current model)
    │  2. Append user message to history
    │  3. Call LLMClient.chat(history, tools=tool_schemas)
    │  4. If tool_calls → execute tools → append results → loop (max 10 iterations)
    │  5. Append final response to history, persist history to DB
    │  6. Return final text (using stream() internally for final turn, buffered)
```

### Model switching

User: `@Kevin switch to openai gpt-4o` → LLM calls `SwitchModel(provider="openai", model="gpt-4o")` → tool verifies `sender_id == context.owner_id` → `context.active_client` swapped to new `OpenAIClient` → `context.agent.client` updated → provider/model persisted to DB → survives restarts.

### Scheduled task firing

```
Scheduler polls DB every 60 seconds
    │  finds ScheduledTasks where next_run <= now and status == "active"
    ▼
For each due task:
    │  load GroupContext for task.group_id
    │  if GroupContext not found (group deleted) → pause task, skip
    │  if task.model/provider set:
    │      create a one-off Agent with a task-specific LLMClient
    │      (same MemoryBackend as the group, but fresh empty history)
    │  else:
    │      use context.agent directly (with its existing history)
    │  queue.run(task.group_id, _run_task(task, agent))
    │  on success: compute next_run, reset retry_count, update DB
    │  on failure: retry_count += 1; if retry_count >= 3 → status = "failed"
    │              else: next retry in 5s * 2^retry_count (5s, 10s, 20s)
    ▼
Result sent to task.group_id via channel.send()
```

**Note on task agents:** Using a one-off agent for model-override tasks avoids mutating `context.active_client`. The one-off agent shares the group's `MemoryBackend` (reads persistent memory) but starts with no conversation history — the task `prompt` is its only user message. Context agent history is not modified by scheduled task runs.

---

## Error Handling & Resilience

### Infisical unavailable at startup
Hard fail: `"Cannot connect to Infisical. Check INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET environment variables."` napyclaw will not start.

### LLM errors
- Ollama unreachable (Tailscale down, machine off) → `OllamaClient` raises `LLMUnavailableError` → `Agent` catches it, returns user-friendly string: `"I can't reach the Ollama server right now. Try switching to cloud: @{display_name} switch to openai"`
- OpenAI API error (rate limit, bad key) → `LLMUnavailableError` with appropriate message
- Tool execution failure → tool returns error string; LLM receives it as tool result and decides to retry or report to user. Tools never raise exceptions out of `execute()`.

### Agent loop safety
- Max 10 tool call iterations per turn — `AgentLoopError` is raised, caught by `NapyClaw`, reported to user as `"I got stuck in a loop. Please try rephrasing your request."`
- Conversation history pruned at 8000 estimated tokens: system prompt always kept, oldest user/assistant pairs dropped first

### Scheduler resilience
- Failed tasks: up to 3 retries with exponential backoff (5s base, doubling: 5s, 10s, 20s). If a task has a model override and `LLMUnavailableError` is raised, the scheduler catches it and retries that run using `context.active_client` before counting it as a failure.
- Task's group deleted → task paused, no notification (owner may no longer be reachable)
- Task's specified model unavailable → falls back to group's `active_client`; error logged

### Slack Socket Mode disconnects
- `slack-bolt` handles reconnection automatically
- Messages received during disconnect are not replayed (Socket Mode limitation); bot resumes normally after reconnect

### Database
- aiosqlite with WAL mode for safe concurrent access (scheduler + agent runs overlap)
- All writes wrapped in transactions
- DB file and parent directory created at startup if missing

---

## DB Schema (summary)

| Table | Key columns |
|---|---|
| `messages` | `id`, `group_id`, `sender_id`, `sender_name`, `text`, `timestamp`, `channel_type` |
| `group_contexts` | `group_id`, `default_name`, `display_name`, `nicknames` (JSON), `owner_id`, `provider`, `model`, `is_first_interaction`, `history` (JSON) |
| `scheduled_tasks` | `id`, `group_id`, `owner_id`, `prompt`, `schedule_type`, `schedule_value`, `model`, `provider`, `status`, `next_run`, `retry_count`, `created_at` |
| `task_run_log` | `id`, `task_id`, `ran_at`, `status`, `result_snippet`, `duration_ms` |

---

## Testing

- Unit tests for `Agent` loop (mock `LLMClient`, assert tool calls fire correctly, assert loop limit enforced)
- Unit tests for trigger matching (all name layers, case-insensitivity, Slack mention format)
- Unit tests for `Scheduler` (mock DB, assert due tasks fire, assert retry backoff)
- Unit tests for `RenameBot`/`SwitchModel` permission enforcement
- Integration smoke test: real Slack → real Ollama round trip (manual, not CI)

---

## Out of Scope (v1)

- WhatsApp, Telegram, Discord channels (architecture supports them via Channel base class)
- Container isolation
- Multi-user / team deployments (designed for single owner)
- Web UI or dashboard
- Message replay after Slack Socket Mode disconnect
- Recipe tool implementations (Phase 2 — `RecipeTool` base class and `tools/recipes/` scaffolded in v1)
- OAuth provider implementations (Phase 2 — `OAuthCallbackServer` and base class in v1, provider-specific flows added per recipe)
- napyclaw/OB1 fork schema changes (tracked in the OB1 repo, not napyclaw)
