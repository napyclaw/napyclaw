# Webchat Comms — Design Spec

**Date:** 2026-04-23  
**Replaces:** Slack Socket Mode (inbound) + Slack API (outbound)  
**Status:** Approved

---

## 1. Goal

Replace Slack as the sole non-self-hostable component with a self-hosted, mobile-friendly web chat UI. The replacement must be transparent to the rest of the bot stack — the `Channel` abstraction, `GroupContext`, vector memory, agent loop, InjectionGuard, EgressGuard, and Scheduler are unchanged.

---

## 2. User-Facing Behavior

### 2.1 Layout

A responsive single-page app served by the `comms` container:

- **Sidebar (left, persistent):** List of specialist conversations. Active specialist highlighted. "+ New Specialist" button at bottom of list. "Admin" DM pinned below a divider at the very bottom, with a red badge when there is a pending approval.
- **Chat pane (right):** Message history for the selected specialist. **Independently scrollable** — the sidebar does not scroll with the chat. Bot avatar initial on left, user messages right-aligned. Typing indicator (animated dots) while bot is responding.
- **Chat header:** Specialist display name + job title. "✏ rename" link opens inline edit for `display_name` and the first entry in `nicknames`.
- **Input bar:** Text field + send button (↑). Placeholder text says "Message \<nicknames[0] or display_name\>...".

### 2.2 Specialist Conversations

Each specialist is a `GroupContext` row with `channel_type = "webchat"`.

**New conversation flow:**
1. Click "+ New Specialist" → optional name field (leave blank to let agent choose).
2. First message creates the `GroupContext`. Bot introduces itself.
3. If `nicknames` is empty, bot asks for one. If user says "choose one," bot picks and stores it as `nicknames[0]`.
4. After approximately 5 messages, bot self-updates `job_title` based on established role.
5. `display_name` and `nicknames[0]` are editable at any time from the chat header.

**Memory:** All specialist chats share the same vector DB pool (open-brain). Each `GroupContext` has its own scoped conversation history. Memory writes are enabled by default.

### 2.3 Admin DM

A fixed `GroupContext` row with `group_id = "admin"` and `memory_enabled = false`.

- Seeded at bot startup if it does not already exist.
- Used exclusively for egress approval prompts and injection guard alerts.
- No message history written to the vector store.
- Approval cards show four action buttons: **Approve Once**, **Approve Always**, **Deny Once**, **Deny Always**.
- Clicking a button sends the decision to `comms`, which POSTs to EgressGuard's callback.

---

## 3. Architecture

### 3.1 Container Topology

No new containers. No network rule changes. The existing `comms` container gains WebSocket and static file serving capabilities.

```
Browser
  │  WebSocket /ws  (bidirectional, real-time)
  │  GET /          (static HTML/CSS/JS)
  ▼
comms (port 8001, comms-net)
  │  POST /webhook  (inbound user message → bot)
  │  POST /send     (bot response → browser via WebSocket)
  │  POST /notify/approval  (egress approval → browser via WebSocket)
  │  POST /approval/respond (browser decision → egressguard callback)
  ▼
bot (comms-net, registers webhook on startup via POST /register)
```

Port 8001 is already published to the host. No new port mapping needed.

### 3.2 comms Service Changes

**New endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Serve static `index.html` |
| `/static/*` | GET | Serve CSS/JS assets |
| `/ws` | GET (WebSocket upgrade) | Browser connection |
| `/approval/respond` | POST | Browser → EgressGuard decision |
| `/specialists` | GET | Return list of `GroupContext` rows for sidebar |

**Modified endpoints:**

- `POST /send` — pushes `{"type": "message", "group_id": ..., "text": ...}` over WebSocket instead of calling Slack API.
- `POST /notify/approval` — pushes `{"type": "approval", "token": ..., "hostname": ..., "url": ...}` over WebSocket instead of calling Slack API.

**In-memory state:**
- One active WebSocket connection tracked (single-user).
- Message buffer: last 50 messages per `group_id`. Replayed to browser on reconnect.

**Static files:** `services/comms/static/index.html` — single file, embedded CSS and vanilla JS, no build step.

### 3.3 WebChannel (napyclaw/channels/web.py)

New `Channel` implementation mirroring `SlackChannel`:

- `start()` — registers webhook URL with `comms POST /register`. Starts an HTTP listener on a local port to receive inbound messages from `comms`.
- `send(group_id, text)` — calls `comms POST /send`.
- `stop()` — shuts down the local HTTP listener.
- Normalizes inbound POST payloads to `Message` dataclass with `channel_type = "webchat"`.
- `group_id` from the payload routes to the correct `GroupContext`.

### 3.4 Config

`napyclaw.toml` gains a `[comms]` section:

```toml
[comms]
channel = "webchat"  # or "slack"
```

`napyclaw/__main__.py` instantiates `WebChannel` or `SlackChannel` based on this value.

---

## 4. Data Model Changes

`GroupContext` gains two new columns (migration required). The existing `nicknames` (JSON text array) already stores aliases — `nicknames[0]` is used as the display nickname in the UI; no separate `nickname` column is added.

| Column | Type | Default | Notes |
|---|---|---|---|
| `job_title` | `text` | `null` | One-line role summary, agent-updated |
| `memory_enabled` | `boolean` | `true` | `false` for Admin DM |
| `channel_type` | `text` | `'slack'` | `'webchat'` for all new specialists |

Admin DM seed row:
```sql
INSERT INTO group_contexts (group_id, channel_type, memory_enabled)
VALUES ('admin', 'webchat', false)
ON CONFLICT (group_id) DO NOTHING;
```

---

## 5. Message Flow

### 5.1 User → Bot

1. User types in browser, JS sends over WebSocket: `{"type": "message", "group_id": "<id>", "text": "..."}`.
2. `comms` receives the frame, POSTs to bot webhook: `{"group_id": ..., "sender_id": "owner", "text": ...}`.
3. `WebChannel._handler()` normalizes to `Message`, enqueues to `GroupContext` queue.
4. Agent loop runs, produces response.
5. Bot calls `channel.send(group_id, text)` → `comms POST /send`.
6. `comms` pushes `{"type": "message", "group_id": ..., "text": ...}` over WebSocket.
7. Browser JS appends message to the correct conversation thread.

### 5.2 Approval Flow

1. EgressGuard calls `comms POST /notify/approval` (unchanged payload).
2. `comms` pushes approval card over WebSocket to browser.
3. Browser renders card in Admin DM pane with four action buttons.
4. User clicks a button; browser sends `{"type": "approval", "token": ..., "decision": "approve_once"|"approve_always"|"deny_once"|"deny_always"}` over WebSocket.
5. `comms` POSTs decision to EgressGuard's callback URL.
6. Approval badge on Admin DM clears.

### 5.3 Reconnection

On WebSocket reconnect, `comms` replays the last 50 messages per `group_id` from the in-memory buffer. No DB read required.

---

## 6. Frontend

Single `services/comms/static/index.html`:

- **No framework, no build step.** Vanilla JS + embedded CSS.
- WebSocket client with exponential backoff reconnect (max 30s interval).
- Renders sidebar from a local specialist list fetched via `GET /specialists` on load.
- Chat pane independently scrollable (`overflow-y: auto`, fixed height).
- Approval cards rendered as structured HTML with four buttons.
- Dark theme matching mockup (#0f172a background, #1d4ed8 accent).
- Mobile-responsive: sidebar collapses to a hamburger menu on narrow viewports.

---

## 7. What Is Not Changing

- `Channel` abstract interface — unchanged.
- `GroupContext`, agent loop, `GroupQueue` — unchanged.
- Vector memory, `VectorMemory`, `MarkdownMemory` — unchanged.
- `InjectionGuard`, `ContentShield`, `EgressGuard` — unchanged.
- All other `comms` REST endpoints (`/register`) — unchanged.
- Docker Compose networks and container count — unchanged.
- Slack support — dead code stubs remain, removable later.

---

## 8. Out of Scope

- Authentication / login (single-user, trusted local network).
- Multi-user support.
- File attachments or image uploads.
- Message editing or deletion.
- Push notifications (browser tab focus assumed).
- Slack removal cleanup (separate task).
