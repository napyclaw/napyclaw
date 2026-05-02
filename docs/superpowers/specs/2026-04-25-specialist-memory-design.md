# Specialist Memory, Onboarding, and Backstage UI
**Date:** 2026-04-25
**Issues:** #9 (specialist onboarding + structured memory), #10 (smarter vector capture)
**Out of scope:** Knowledge base ingestion (→ issue #11)

---

## 1. Data Model

### Migration 004

Add columns to `group_contexts`:

```sql
ALTER TABLE group_contexts
    ADD COLUMN IF NOT EXISTS job_description  TEXT,
    ADD COLUMN IF NOT EXISTS verbatim_turns   INTEGER NOT NULL DEFAULT 7,
    ADD COLUMN IF NOT EXISTS summary_turns    INTEGER NOT NULL DEFAULT 5;
```

New `specialist_memory` table:

```sql
CREATE TABLE specialist_memory (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN (
                    'responsibility','task','tool','resource','preference','fact')),
    content     TEXT NOT NULL,
    embedding   vector(768),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS specialist_memory_group_idx
    ON specialist_memory (group_id);
CREATE INDEX IF NOT EXISTS specialist_memory_embedding_idx
    ON specialist_memory USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
```

All entries are specialist-scoped via `group_id`. Cross-specialist access control is deferred to a future design.

`thoughts` table is unchanged. Already per-specialist via `group_id`.

---

## 2. System Prompt Layering

Prompt built by a dedicated `napyclaw/prompt_builder.py` module. Takes `GroupContext` + retrieved memory rows + format config, returns a rendered string. `app.py` calls it; knows nothing about internal structure.

### Block order (earlier = higher LLM attention)

```
Block 1: IDENTITY
  - name, job_title, job_description, owner_name
  - trust tier rules: which memory types require approval vs. notify
  - instruction: always speak in first person ("I", "my role", "I can help with...")
    — the user is talking to a named specialist, not reading a description of one

Block 2: RESPONSIBILITIES
  - ALL responsibility rows for this group_id (never truncated)
  - These are non-negotiable facts about what this specialist does

Block 3: WORKING CONTEXT
  - Top-k semantic: task/tool/resource/preference/fact for this group_id
  - Matched to current message embedding

Block 4: EPISODIC MEMORY
  - Top-k from thoughts for this group_id
  - Matched to current message embedding

Block 5: PRUNED HISTORY
  - Older exchanges beyond the 2-4 recent turns
  - Lowest background priority; cut first by _prune_history()

[Messages array — handled by agent.py, not system prompt]
Block 6: RECENT CONTEXT — last 2-4 exchanges verbatim (high attention tail)
Block 7: CURRENT MESSAGE — user's actual input
```

### Render formats

`PromptBuilder` supports `fmt: Literal["markdown", "json"]`. Default: `markdown`. Configurable per model via config. JSON available for benchmarking against specific models.

```python
class PromptBuilder:
    def build(self, ctx: GroupContext, memory: RetrievedMemory,
              owner_name: str, fmt: str = "markdown") -> str: ...
```

---

## 3. Trust Tiers

Memory writes are gated by type:

| Type | Tier | Behavior |
|------|------|----------|
| `responsibility` | Ask first | Agent proposes → user confirms → then write |
| `job_description` | Ask first | Agent proposes → user confirms → then write |
| `task` | Write + notify | Write immediately, notify in Backstage |
| `tool` | Write + notify | Write immediately, notify in Backstage |
| `resource` | Write + notify | Write immediately, notify in Backstage |
| `preference` | Write + notify | Write immediately, notify in Backstage |
| `fact` | Write + notify | Write immediately, notify in Backstage |

**Correction window:** Write + notify items are queued for 2-3 turns before committing. During the window, the user sees two actions in the Backstage sticky area per queued item:
- **Adjust** — opens an inline edit field in the Backstage sticky area; user rewrites the content; does not post anything to the chat column
- **Exclude** — removes the item from the queue silently; nothing posted to chat

Both actions are handled entirely in the Backstage column ("btw" interaction) — the chat column is never interrupted. After the window expires with no action, auto-commit.

The agent can also suggest adding new scope: "This request is outside my current responsibilities — should I add this as a new responsibility?" This follows the ask-first flow.

---

## 4. Agent Tools

### `set_job_description(description: str)`
Trust tier: ask first. Agent proposes, waits for user confirmation, then writes to `group_contexts.job_description`. Used during onboarding and when user explicitly requests a role update.

### `manage_specialist_memory(action, type, content, entry_id=None, scope="specialist")`
- `action`: `add` | `update` | `delete`
- `type`: `responsibility` | `task` | `tool` | `resource` | `preference` | `fact`
- `scope`: `"specialist"` (only value for now — cross-specialist access deferred)
- Trust tier determined by type per table above.

### `save_to_memory(content: str)`
Agent-gated episodic capture to `thoughts` for this specialist. Used directly by agent and by background summarizer. Always write + notify. No ask-first — content is agent-synthesized, not raw user text.

---

## 5. Onboarding Flow

No explicit mode flag. System prompt Block 1 includes: **"If job_description is empty, work collaboratively with the user to define your role before proceeding with other work."**

Agent-driven onboarding:
1. Agent asks open questions about the user's needs from this specialist
2. After a few turns, agent proposes a summary in first person: "Here's what I understand my role to be — does this look right?"
3. User confirms → agent calls `set_job_description()` and seeds initial `specialist_memory` entries
4. Agent announces it's ready for normal work
5. Agent proactively asks for resources: draws on job_description context to ask specifically where possible ("Do you have a preferred forecasting framework I should know about?"), then broadly: "Are there any other resources or knowledge I'll need to do this role at the highest level?" — any resources provided are saved via `manage_specialist_memory(add, resource, ...)`

Transition is natural — no mode switch required. Updates follow the same flow at any future point in the relationship.

---

## 6. Background Summarizer

Triggered in `_run_agent` **after** the response is sent, only when `_prune_history()` is about to drop exchanges. Runs as a non-blocking async background task — zero added latency to the user.

### Flow

```
1. Detect that _prune_history() will drop exchanges this turn
2. Fire asyncio background task
3. Build summarization prompt:
   - Block 1 + Block 2 context (identity + responsibilities)
   - The exchanges about to be pruned
   - Instruction: "Summarize what was learned, decided, or established.
     Correct typos and ignore abandoned trains of thought.
     Return a list of structured items: [{type, content, scope}, ...]
     Each distinct fact, task, resource, or role change is a separate item."
4. LLM returns a list of typed items — typically 1-5 per summarization batch
5. Route each item independently by trust tier:
   - `responsibility` or `job_description` → queue as pending approval in Backstage sticky area
   - all other types → enter correction window (2-3 turns), notify in Backstage
6. Each queued item gets its own Adjust / Exclude action in the sticky area
7. After correction window expires per item, call save_to_memory() for that item
```

### History window configuration

Each specialist has two configurable values stored in `group_contexts`:

| Column | Default | Meaning |
|--------|---------|---------|
| `verbatim_turns` | 7 | Turns kept verbatim in the high-attention tail — never summarized |
| `summary_turns` | 5 | Turns fed to the summarizer before being dropped |

Total buffer = `verbatim_turns + summary_turns` (default 12). When the buffer exceeds this, the oldest `summary_turns` batch is summarized and dropped. The recent `verbatim_turns` are untouched.

Token cap applies alongside turn count — whichever limit is hit first triggers summarization. This handles specialists with long per-message exchanges (e.g. a research specialist pasting large documents) without waiting for turn count.

Global defaults live in config. Per-specialist overrides stored as columns on `group_contexts`. Specialists with wide verbatim context needs (e.g. code review) can set `verbatim_turns = 12`; lightweight specialists can run leaner.

Summarization cadence: triggered when buffer exceeds `verbatim_turns + summary_turns`, not every turn.

---

## 7. User Identity

`comms/main.py` extracts `Tailscale-User-Name` header from inbound webhook requests.

Parse: `nate@betterforecasting.com` → `Nate` (first segment before `@`, title-cased).

Passes `owner_name` in webhook payload to bot. Bot stores on `GroupContext.owner_id`. Used in Block 1 of system prompt: "You are working for {owner_name}."

Displayed in webchat UI top-left so the user can see their identity.

---

## 8. Backstage Column — UI

Three-panel layout:

```
[ Specialists ] [ Chat ] [ Backstage ]
```

### Backstage panel structure

**Top — sticky:**
- Pending approvals: proposed `responsibility` or `job_description` changes with approve/reject
- Correction window items: queued memory entries with turn countdown, **Adjust** or **Exclude** per item — both handled inline in the Backstage sticky area without interrupting the chat column
- Egress approvals (existing) migrate here

**Bottom — current turn:**
- Context used (D): memory blocks retrieved, counts
- Memory activity (A): what was written or queued this turn
- Background tasks (E): summarizer ran, exchanges pruned, correction window opened
- Tool calls as containers: tool name → content/result inline

### Interaction model

- Current turn block: always expanded, distinct background color A
- Focused turn block: expanded when user clicks a message bubble in chat, distinct background color B
- Only current + focused turn open simultaneously — all others collapsed
- Clicking a chat message auto-scrolls the Backstage column to that turn's block and expands it, collapsing any previously focused turn
- Prior turns collapsed by default but accessible

### New WebSocket event types needed

| Event | Payload |
|-------|---------|
| `context_used` | `{group_id, blocks: [{type, count}]}` |
| `memory_queued` | `{group_id, token, type, content, window_turns_remaining}` |
| `memory_adjusted` | `{group_id, token, revised_content}` — user submitted Adjust |
| `memory_excluded` | `{group_id, token}` — user clicked Exclude |
| `memory_pending_approval` | `{group_id, type, content, token}` |
| `memory_committed` | `{group_id, type, content}` |
| `background_task` | `{group_id, event: "summarizer_ran" \| "exchanges_pruned" \| "window_opened"}` |
| `tool_call` | `{group_id, tool_name, args, result}` |

Approval response flows through existing `/approval/respond` endpoint pattern.

---

## 9. New Module: `napyclaw/prompt_builder.py`

Responsibility: build system prompts from structured layers. Knows about block order, render formats, token budget per block. Tested independently of `app.py`.

```python
@dataclass
class RetrievedMemory:
    responsibilities: list[SpecialistMemoryRow]
    working_context: list[SpecialistMemoryRow]
    episodic: list[str]  # thought contents

class PromptBuilder:
    def build(self, ctx: GroupContext, memory: RetrievedMemory,
              owner_name: str, fmt: str = "markdown") -> str: ...
    def _render_markdown(self, blocks: dict[str, str]) -> str: ...
    def _render_json(self, blocks: dict[str, str]) -> str: ...
```

`app.py` currently builds prompts inline via `_default_system_prompt` and `_run_agent`. Both merge into `PromptBuilder.build()` calls.

---

## 10. Implementation Order

1. Migration 004 (job_description + specialist_memory table)
2. `db.py` — add CRUD for specialist_memory, update save/load for job_description
3. `prompt_builder.py` — new module, unit tested
4. `app.py` — wire PromptBuilder, replace _default_system_prompt
5. Agent tools — set_job_description, manage_specialist_memory, save_to_memory
6. Background summarizer — trigger in _run_agent, non-blocking
7. User identity — comms header extraction, owner_name propagation
8. Backstage WebSocket events — new event types in comms/main.py
9. Webchat UI — three-panel layout, Backstage column, sticky area, turn linking
10. Onboarding system prompt language — Block 1 copy for empty job_description

---

## Resolved Decisions

- **Correction window:** 3 turns. Configurable per specialist later if needed, hardcoded for now.
- **Global memory pool:** deferred. All `specialist_memory` and `thoughts` entries are specialist-scoped only. Cross-specialist access control (global, roles, manager, etc.) is a separate future design — keeping specialist framing clean now makes that easier to layer on later.
- **Backstage background colors:** current turn = same as page background (no special treatment); focused turn = `#1e3a5f` (darker variant of the chat bubble blue).
- **Summarization model:** same model as the specialist. Tunable later but no separate model config for now.
