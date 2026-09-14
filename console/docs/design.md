# incidara-console — Design Plan

**Status:** Draft
**Owner:** yutji
**Date:** 2026-04-17
**Related:**
- Upstream gateway: `incidara` repo, `incidara_agents/agents/claude-agent/`
- Web UI requirements: `incidara/web-ui-design.md`

---

## 1. Goal

Build a multi-user web UI for interacting with Claude-based agent deployments. Users log in, see agents they have access to, start/continue sessions, and watch live activity.

**Core surfaces:**
- Login/signup (email + password)
- Home — agent overview (fleet-level dashboard)
- Tasks — flat, filterable list of all tasks I can see across agents
- Agent detail — tasks filtered to this agent + start new task (new or existing session)
- Session workspace — live timeline + adaptive composer for one session

## 2. Non-Goals

- Single sign-on (SSO), OIDC, SAML — email+password only
- Creating agents via UI (agents are declarative YAML config)
- Attach mode (gateway only supports managed sessions)
- Scheduler UI, settings page, deep analytics
- Hosting the agent gateway itself — this repo is UI-only

## 3. Entity Model

```
User                              email-identified, password-authenticated
 └─ has access to ──► Agent       defined in config/agents.yaml (YAML-driven)
 │                     └─ owns / shares ──► Session   a conversation thread
 │                                           └─ has ──► Task   a unit of work
 │
 └─ owns ──► Schedule ──(fires at trigger)──► Task      creates tasks over time
```

**Key semantics:**

- **Agent** — fixed deployment pointing at a gateway URL. Not user-created. Identifies capability (name, skill set, backend).
- **Session** — a conversation on an agent. Owned by one user; shareable with others. Maps 1:1 to a Claude SDK `session_id`.
- **Task** — a goal submitted to the agent. Has **explicit start** (a `POST .../tasks` or first prompt on a new session) and **explicit end** (a terminal gateway event: `session.completed`, `session.error`, `session.interrupted`).

**Task vs messages within a task:**

A task can include multiple user messages if the agent pauses for guidance. When the agent emits `session.waiting_input`, the user's reply is **guidance inside the same task**, not a new task. Only an explicit `POST /api/sessions/:id/tasks` or `POST /api/agents/:id/sessions` starts a new task.

**Task queue (pending state):**

New tasks submitted while a session's current task is still active are accepted as `pending`. They're auto-promoted to `running` when the prior task reaches a terminal state. Submission is never blocked; users can stack work on a session.

Task status values: `pending | running | waiting_input | completed | error | interrupted | cancelled`.

- `pending` — submitted, waiting for agent to be free
- `cancelled` — user cancelled a pending task via `DELETE /api/tasks/:id` before it started

**Task persistence:**

Tasks are stored in Postgres independently of gateway state. Even when a session is cleaned up on the gateway (TTL), the task row survives in the UI DB with final status + output preview, preserving the audit trail.

**Schedules:**

A `Schedule` is a template with a trigger (`once`, `interval`, or `cron`) that fires Tasks over time. Each firing creates a Task row. Schedules are owner-only (no sharing in MVP).

## 4. Architecture

```
┌──────────────────────────────────────┐
│ Browser (React SPA)                  │
│ - /, /tasks, /agents/:id,            │
│   /sessions/:id                      │
│ - SSE subscription per session       │
└────────────────┬─────────────────────┘
                 │ HTTP (REST) + SSE
┌────────────────▼─────────────────────┐
│ incidara-console server (Node + Express)  │
│ - Auth (argon2 + iron-session)       │
│ - Agent registry (YAML)              │
│ - Session + Task DB (Postgres)       │
│ - Gateway proxy (HTTP + SSE)         │
│ - Task status reconciler             │
└────────────────┬─────────────────────┘
                 │ HTTP + SSE
┌────────────────▼─────────────────────┐
│ Claude Agent Gateway (per agent)     │
│ - Unchanged                          │
│ - Doesn't know about users / tasks   │
└──────────────────────────────────────┘
```

Three layers, each owning distinct responsibilities:

| Layer | Responsibility |
|---|---|
| **Client** | Rendering, local state, SSE consumption, form validation |
| **UI server** | Auth, authorization, ownership, task tracking, proxying to gateway |
| **Gateway** | Session runtime; session-native; user/task-agnostic |

### 4.1 Why the UI server owns Task records

- Gateway sessions get TTL-cleaned up; without an external record, task audit trails disappear
- UI server needs cross-session task queries (`/tasks` page) — indexing events on every request is slow
- Submitter identity is per-task info, not per-session

The gateway is the source of truth for **events**; the UI server is the source of truth for **task metadata** (who submitted it, when, its aggregate status).

## 5. Data Model

### 5.1 Postgres tables (see `db/init/001_init.sql`)

```sql
users (id, email UNIQUE, password_hash, name, created_at)

sessions (
  id, gateway_session_id UNIQUE, agent_id,
  owner_email FK, title,
  shared_with_users TEXT[], shared_with_groups TEXT[],
  created_at
)

schedules (
  id, name, agent_id, owner_email FK, prompt,
  trigger_type,            -- 'once' | 'interval' | 'cron'
  run_at, interval_seconds, cron_expr, timezone,
  session_mode,            -- 'new' | 'reuse'
  reuse_session_id FK ON DELETE SET NULL,
  enabled, last_fired_at, next_fire_at,
  created_at
)

tasks (
  id, session_id FK ON DELETE CASCADE,
  prompt, submitter_email FK,
  status,                  -- pending | running | waiting_input | completed | error | interrupted | cancelled
  created_at,              -- user submitted
  started_at,              -- promoted from pending to running (nullable)
  ended_at,                -- reached terminal (nullable)
  output_preview,          -- last message.agent in the task (200 chars), fallbacks for partial/error
  gateway_seq_start,       -- seq of the message.user event (for deep-linking)
  from_schedule_id FK ON DELETE SET NULL
)

_migrations (filename PK, applied_at)
```

Indexes:
- `sessions(owner_email, agent_id, gateway_session_id)`
- `schedules(next_fire_at) WHERE enabled` — scheduler loop scan
- `tasks(session_id, status, id)` — queue promotion query
- `tasks(created_at DESC)` — default list sort
- `tasks(from_schedule_id) WHERE NOT NULL`

### 5.2 Agent config (`config/agents.yaml`)

Loaded at server startup, cached in memory. Example:

```yaml
- id: triage-unknown
  name: Triage Unknown Nodes
  description: Investigates cluster nodes in 'triaged_unknown' state
  gateway_url: http://<gateway-host>:8000   # real URL in runtime config only
  backend: claude_code
  access:
    owners: [yutji@example.com]
    shared_with: [sre-lead@example.com]
    shared_with_groups: [sre-team]
```

Config file itself is gitignored; only `config/agents.yaml.example` lives in the repo.

### 5.3 Groups config (`config/groups.yaml`)

```yaml
- id: sre-team
  name: SRE Team
  members:
    - yutji@example.com
    - ops@example.com
```

## 6. Authorization

### 6.1 Agent access (can I start a session on agent A?)

User `me` can start a session on agent `A` if:
- `me in A.access.owners`, OR
- `me in A.access.shared_with`, OR
- `me` is a member of any group in `A.access.shared_with_groups`

### 6.2 Session visibility

User `me` can view a session if:
- `session.owner_email == me`, OR
- `me in session.shared_with_users`, OR
- `me` is in a group in `session.shared_with_groups`

**MVP rule:** session access is independent of agent access. Sessions default to **private to owner** and need explicit sharing. Agent access only governs "who can start new sessions on this agent."

### 6.3 Task visibility

A task inherits its parent session's visibility. If you can see the session, you can see its tasks.

### 6.4 Interaction rights

| Role | Can do |
|---|---|
| Session owner | Send messages/guidance, interrupt, resume, rename, share, delete |
| Shared viewer | View timeline, view tasks. **No send/interrupt.** |

Interaction endpoints check `owner_email === me`. Viewer endpoints (GET /state, /events, /events/stream) check `canViewSession`.

## 7. HTTP API

Base: `/api`. All `/api/*` routes require the session cookie (middleware `requireAuth`).

### 7.1 Auth

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/auth/signup` | `{email, password, name?}` | `{user}` + cookie |
| POST | `/api/auth/login` | `{email, password}` | `{user}` + cookie |
| POST | `/api/auth/logout` | — | 204 |
| GET | `/api/auth/me` | — | `{user}` or 401 |

### 7.2 Agents

| Method | Path | Description |
|---|---|---|
| GET | `/api/agents` | List agents I have access to. Returns `{agents: [{id, name, description, backend, access_level, counters: {running, waiting, total_today}}]}`. Counters aggregated from the `tasks` table. |
| GET | `/api/agents/:id` | Agent metadata (if authorized). `gateway_url` NOT included in response. |

### 7.3 Sessions

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/agents/:agentId/sessions` | `{prompt, title?}` | Create session + initial task on the agent |
| GET | `/api/sessions` | — | List sessions visible to me. Query params: `agent_id`, `scope` (mine\|shared\|all) |
| GET | `/api/sessions/:id` | — | Session metadata |
| PATCH | `/api/sessions/:id` | `{title?, shared_with_users?, shared_with_groups?}` | Owner-only |
| DELETE | `/api/sessions/:id` | — | Owner-only. Cascade-deletes tasks. Also calls gateway DELETE. |

### 7.4 Tasks (new in this design)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/tasks` | — | List tasks visible to me. Query params: `status`, `agent_id`, `scope`, `limit`, `offset`. Sort desc by `created_at`. |
| GET | `/api/tasks/:id` | — | One task's metadata |
| POST | `/api/sessions/:id/tasks` | `{prompt}` | **Submit a new task in an existing session.** Never rejects for busyness — if session has an active task, new task is inserted with `status='pending'` and auto-promoted later. Returns `{task}` (status either `running` or `pending`). |
| DELETE | `/api/tasks/:id` | — | Cancel a **pending** task. Owner-only. Returns 409 if task is not `pending` (use interrupt to stop a running task). |

### 7.5 Schedules

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/schedules` | `{agent_id, name?, prompt, trigger_type, run_at?, interval_seconds?, cron_expr?, timezone?, session_mode, reuse_session_id?}` | Create schedule. Validates agent access + trigger fields. |
| GET | `/api/schedules` | — | List my schedules. Query params: `agent_id`, `enabled`. |
| GET | `/api/schedules/:id` | — | Detail |
| PATCH | `/api/schedules/:id` | partial | Edit (prompt, trigger, session_mode, enabled). Owner-only. |
| DELETE | `/api/schedules/:id` | — | Remove. Owner-only. |
| POST | `/api/schedules/:id/run-now` | — | Fire the schedule immediately (creates a task now, regardless of `next_fire_at`). |

Trigger validation rules:
- `trigger_type='once'` → requires `run_at`
- `trigger_type='interval'` → requires `interval_seconds >= 30`
- `trigger_type='cron'` → requires valid `cron_expr` (validated via `cron-parser`) + `timezone`

Session mode validation:
- `session_mode='new'` — agent must be accessible to user
- `session_mode='reuse'` — requires `reuse_session_id`; user must own that session; session's agent must match `agent_id`

### 7.6 Messages (guidance)

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/sessions/:id/messages` | `{content}` | **Send guidance within the current (active) task.** 409 if there's no active task in the session (last task is terminal). Does NOT create a new task row. |

### 7.7 Session control + event stream (passthrough to gateway)

| Method | Path | Upstream |
|---|---|---|
| GET | `/api/sessions/:id/state` | `GET /sessions/<gw_id>/state` |
| GET | `/api/sessions/:id/events?after_seq=N` | `GET /sessions/<gw_id>/events` |
| GET | `/api/sessions/:id/events/stream` | SSE proxy; headers forwarded; **side-effect: updates task rows** |
| POST | `/api/sessions/:id/interrupt` | `POST /sessions/<gw_id>/interrupt` (owner only) |
| POST | `/api/sessions/:id/resume` | `POST /sessions/<gw_id>/resume` (owner only) |

### 7.8 Endpoint mapping summary

| User intent | UI endpoint | Gateway endpoint | Task-row effect |
|---|---|---|---|
| New task, new session | `POST /api/agents/:id/sessions` | `POST /sessions` | INSERT session + INSERT task |
| New task, existing session | `POST /api/sessions/:id/tasks` | `POST /sessions/<gw>/messages` | INSERT task (if prev terminal) |
| Guidance to current task | `POST /api/sessions/:id/messages` | `POST /sessions/<gw>/messages` | No task row change |
| Interrupt | `POST /api/sessions/:id/interrupt` | `POST /sessions/<gw>/interrupt` | Will update latest task → `interrupted` via SSE |
| Resume | `POST /api/sessions/:id/resume` | `POST /sessions/<gw>/resume` | Latest task → `running` |

## 8. Task Lifecycle

### 8.1 State machine

```
 [pending] ──(agent free)──► [running] ──waiting──► [waiting_input]
    │                            │                       │
    │ user cancels               │                       │ guidance
    │ (DELETE /tasks/:id)        │                       ▼
    ▼                            │                  [running]
 [cancelled]                     │
                                 │ terminal event
                                 ▼
                       [completed | error | interrupted]
```

### 8.2 Write path

**On `POST /api/agents/:id/sessions`:**
1. Create `sessions` row (with `gateway_session_id` from upstream)
2. Create `tasks` row: `status='running'` (fresh session, no prior task), call gateway

**On `POST /api/sessions/:id/tasks`:**
1. No 409 for busy sessions — always accept.
2. If latest task for this session is terminal OR no prior task:
   - Insert `tasks` row with `status='running'`, `started_at=now()`
   - Proxy to gateway `POST /sessions/<gw>/messages`
3. Else (session has an active task):
   - Insert `tasks` row with `status='pending'`, `started_at=null`
   - **No gateway call yet.** Queue promoter will send when the prior task ends.
4. Return `{task}` (status tells client whether it started immediately).

**On `POST /api/sessions/:id/messages`:**
1. Check: latest task for this session must be active (`running`/`busy`/`waiting_input`); else return 409 "no active task"
2. No DB write — just proxy guidance to gateway

**On `DELETE /api/tasks/:id`:**
1. Owner-only
2. If `status='pending'` → update to `'cancelled'`; 204
3. Else → 409 "only pending tasks can be cancelled"

### 8.3 Queue promotion

When a task reaches a terminal state, we check for pending tasks on the same session and promote the oldest:

```sql
-- Atomic promotion (handles multi-promoter concurrency)
UPDATE tasks
SET status='running', started_at=now()
WHERE id = (
  SELECT id FROM tasks
  WHERE session_id=$1 AND status='pending'
  ORDER BY id ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

After a successful promotion, the server calls gateway `POST /sessions/<gw>/messages` with the promoted task's prompt. On gateway failure, mark the task as `error`.

### 8.4 Update path: two mechanisms

**(a) SSE proxy side-effect** (fast path):

When the UI server proxies `GET /api/sessions/:id/events/stream`, it also parses events as they flow through and updates the latest task row:

- `message.agent` → `UPDATE tasks SET output_preview = content[:200] WHERE session_id=X AND id = latest`
- `session.waiting_input` → `status='waiting_input'`
- `session.completed` → `status='completed'`, `ended_at=now()`
- `session.error` → `status='error'`, `ended_at=now()`, `output_preview = 'Error: ...'` (if preview empty)
- `session.interrupted` → `status='interrupted'`, `ended_at=now()`, `output_preview ?= partial_content[:200]`

**(b) Reconciler** (self-healing, slow path):

Every 60s (configurable via `RECONCILER_INTERVAL_MS`), a background job:

1. Query `SELECT id, session_id FROM tasks WHERE status IN ('running', 'waiting_input')`
2. For each, fetch gateway session state via `GET /sessions/<gw>/state`
3. If gateway says session is `completed`/`error`/`interrupted`, update task accordingly
4. **Also** scans for sessions with `pending` tasks but no active task, and promotes the next pending (catches cases where no SSE listener observed the terminal event)

### 8.5 Task determination on SSE event

The UI server uses `tasks(session_id, started_at DESC)` to pick the "latest task" for each event. The SSE parser maintains a small `sessionToLatestTask` cache (invalidated on new task creation).

## 8A. Schedule Lifecycle

### Scheduler loop

Runs every `SCHEDULER_INTERVAL_MS` (default 30s). Each tick:

```sql
-- Atomic claim of due schedules (safe against multi-instance)
SELECT * FROM schedules
WHERE enabled AND next_fire_at <= now()
FOR UPDATE SKIP LOCKED;
```

For each claimed schedule:

1. **Create the task:**
   - `session_mode='new'`: internally call the same code path as `POST /api/agents/:id/sessions` to create a session + initial task; tag task with `from_schedule_id`
   - `session_mode='reuse'`: internally call `POST /api/sessions/:id/tasks` targeting `reuse_session_id`; tag task with `from_schedule_id`
     - If the reuse session has an active task, the new task lands as `pending` (normal queue behavior)

2. **Update the schedule:**
   - `last_fired_at = now()`
   - Compute `next_fire_at`:
     - `trigger_type='once'` → `next_fire_at = NULL`, `enabled = false`
     - `trigger_type='interval'` → `next_fire_at = last_fired_at + interval_seconds`
     - `trigger_type='cron'` → `next_fire_at = cronParser.next(cron_expr, timezone)`

3. On gateway error: log, do NOT disable the schedule (next tick retries).

### Manual fire: `POST /api/schedules/:id/run-now`

Same logic as the scheduler's "fire this schedule" branch, but does not update `next_fire_at`. Useful for testing or ad-hoc triggers.

### Validation

- Creating a schedule with `trigger_type='once'` in the past → reject 400
- Disabling a schedule preserves `next_fire_at` (re-enabling resumes the existing schedule unless editing trigger)

## 9. Client Pages

### 9.1 `/login`, `/signup`

Standard auth forms. Unauthenticated users are redirected here.

### 9.2 `/` — Home (agent overview)

Pure fleet dashboard, read-only. No composer here.

Layout (top to bottom):
- **Summary strip** — 4 counters: agents accessible, active sessions, waiting input, errored
- **My agents** — one card per agent owned
- **Shared with me** — one card per shared agent

Each agent card shows:
- Name + access badge (OWNER / SHARED)
- Status dot (worst-case status across its sessions)
- Counts: `X running · Y waiting · Z completed today`
- One-line live preview of the most interesting active session (title + current tool)
- Click → `/agents/:id`

If nothing needs attention: subtle "all clear" indicator on each card.

### 9.3 `/tasks` — flat task list

Filters (top): Status, Agent, Time, Scope (mine/shared/all).

Each row:
- Status icon + label
- Agent name
- Task prompt (first line, truncated)
- Metadata line: `Submitted Xm ago by foo@example.com`
- Outcome line: if running, "Currently: <tool>"; if completed, `output_preview`; if error, error message
- Click → `/sessions/:session_id#task-<task_id>`

Paginated via `limit` + `offset`. "Load older" button at bottom.

### 9.4 `/schedules` — Schedules list

Layout:
- **Header:** page title + `[+ New schedule]` button
- **Schedule rows** — each row:
  - Enabled/disabled toggle
  - Name (or prompt first line), agent name
  - Trigger summary: "every 24h · next in 3h 12m" or "at 2026-04-17 02:00 UTC" or "every day at 2:00 Asia/Shanghai"
  - Session mode indicator (`new` / `reuse <session title>`)
  - `Last fired: 21h ago` / `Last fired: never`
  - Actions: `[Run now]` `[Edit]` `[Delete]`

**New schedule modal:**

```
Name (optional):  [________________]
Agent:            [ <agent picker, limited to my accessible agents> ▾ ]
Prompt:           [________________________]

Trigger:          ● Once at    [date/time picker]
                  ○ Every      [N] [seconds ▾]
                  ○ Cron       [0 2 * * *] Timezone [UTC ▾]

Session:          ● New session each firing
                  ○ Reuse session [ <my sessions on this agent ▾> ]

Enabled:          [✓]
                                          [Cancel] [Save]
```

Cron and timezone inputs live-validate on the client using a shared helper; invalid cron expressions show inline error.

### 9.5 `/agents/:id` — Agent detail

- **Header:** agent name, description, backend, access badge, shared-with info (owner-visible)
- **Start new task** composer:
  - Prompt textarea
  - "Send to:" choice:
    - ● New session (creates fresh session + task)
    - ○ Existing session: dropdown of my sessions on this agent where **last task is terminal** (= can accept new task)
  - Submit → `POST /api/agents/:id/sessions` or `POST /api/sessions/:id/tasks`, navigate to session workspace
- **Tasks** — the `/tasks` list component, filtered to this agent

### 9.6 `/sessions/:id` — Session Workspace

Layout:
- **Header:** session title (editable, owner-only), agent link, status badge, interrupt/resume/share buttons
- **Timeline** — task-grouped cards (see 9.6)
- **Right inspector** (collapsible, default off) — current gateway state + raw event feed
- **Adaptive composer** (bottom) — see 9.7

Hash fragment `#task-<id>` on load → scroll to that task card + subtle highlight flash.

### 9.7 Timeline rendering

Each task renders as a card. Within a task card:

- First `message.user` = "prompt" bubble at top
- Subsequent `message.user` events (guidance) = inline bubbles mid-card
- `tool.started` / `tool.stdout` / `tool.finished` = tool activity cards, with collapsible stdout
- `message.delta` events stream into an "in-progress assistant" block until a `message.agent` finalizes it
- `permission.requested` = prominent action card (auto-approved in managed mode; displayed for visibility)
- `session.waiting_input` = amber "Agent is waiting for your input" banner at the bottom of the task
- `session.completed` / `error` / `interrupted` = card footer with outcome

Completed task cards collapse by default to show: prompt + `output_preview` + "expand to see full trace".

### 9.8 Adaptive composer (session workspace)

Composer intent depends on the current session's latest task status:

| Latest task status | Composer label | Indicator | Endpoint |
|---|---|---|---|
| `waiting_input` | **Reply to agent** | ⚠ "Agent is waiting" banner | `POST /api/sessions/:id/messages` |
| `running` / `busy` | **Queue next task** | ⏳ "Will run after current task" chip | `POST /api/sessions/:id/tasks` (creates `pending`) |
| `pending` (queue already has items) | **Queue next task** | ⏳ "X tasks queued" chip | `POST /api/sessions/:id/tasks` (creates `pending`) |
| `completed` / `error` / `interrupted` / `cancelled` | **Start a new task** | ✓ "Last task done" chip | `POST /api/sessions/:id/tasks` (creates `running`) |

Keyboard: `⌘↵` submits. Endpoint is auto-selected — user never has to choose between "message" and "task". Submission always succeeds (no 409 on busy); the status chip tells the user whether it runs now or queues.

Edge cases:
- Viewer (not owner) sees the composer disabled, with a "View only — contact <owner> to interact" note.
- If gateway returns 409 (state changed between UI render and submit), show inline error, refresh task state.

### 9.9 Modals & overlays

| Modal | Purpose |
|---|---|
| **New task (global)** | Triggered by sidebar button or ⌘N. Agent picker + prompt textarea + session choice. Same logic as agent detail composer, but with an agent dropdown. |
| **Share session** | Owner-only. Fields: users (emails), groups (from groups config). Calls `PATCH /api/sessions/:id`. |
| **Delete session confirm** | Destructive; requires confirmation. |
| **⌘K palette** | Fuzzy search: sessions by title, agents by name, quick actions ("new task on <agent>", "interrupt current"). |

### 9.10 Sidebar

Always visible (collapsible on narrow screens):

```
- [ + New task  ⌘N ]
- [ 🔎 Search   ⌘K ]
---
- 🏠 Overview
- ✓  Tasks
- ⏱  Schedules
---
- AGENTS
  - <agent rows: dot + name; warn ⚠ if any task on the agent needs attention>
- SHARED
  - <shared agents with the same treatment>
---
- user menu
```

Click an agent → `/agents/:id`. No nested sessions in the sidebar (keeps it scannable). Fast session switching uses ⌘K palette instead.

## 10. SSE Proxy with Task Update Side-Effect

Sketch (`server/src/routes/proxy.ts`):

```ts
app.get('/api/sessions/:id/events/stream', requireAuth, async (req, res) => {
  const { session, client } = await resolve(req, res);
  if (!session) return;

  const upstream = await client.openEventStream(
    session.gateway_session_id,
    req.query.after_seq ? Number(req.query.after_seq) : null,
    req.header('Last-Event-ID') ?? null,
  );

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const reader = upstream.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  req.on('close', () => reader.cancel().catch(() => {}));

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    res.write(value);                            // proxy bytes immediately

    // Parse out complete SSE events and update tasks as side-effect
    buffer += decoder.decode(value, { stream: true });
    const events = extractCompleteEvents(buffer);
    for (const evt of events.parsed) {
      await applyEventToTask(session.id, evt);   // updates tasks table
    }
    buffer = events.remainder;
  }
  res.end();
});
```

The proxy never buffers — bytes flow through as they arrive. Task updates happen on the same flow, asynchronously.

## 11. Migration Runner

Runs on server startup:

```ts
async function migrate(pool: Pool, dir: string) {
  await pool.query(`CREATE TABLE IF NOT EXISTS _migrations (...)`);
  const applied = new Set(
    (await pool.query('SELECT filename FROM _migrations')).rows.map((r) => r.filename),
  );
  const files = readdirSync(dir).sort();
  for (const f of files) {
    if (applied.has(f)) continue;
    const sql = readFileSync(`${dir}/${f}`, 'utf8');
    await pool.query('BEGIN');
    try {
      await pool.query(sql);
      await pool.query('INSERT INTO _migrations (filename) VALUES ($1)', [f]);
      await pool.query('COMMIT');
    } catch (err) {
      await pool.query('ROLLBACK');
      throw err;
    }
  }
}
```

`001_init.sql` is applied by the DB container on first boot (via `docker-entrypoint-initdb.d`) and records itself in `_migrations`. Future migrations go in `server/src/db/migrations/`.

## 12. Tech Stack

### Server
- Node 22 + TypeScript
- Express 5
- `pg` (node-postgres) + pool
- `argon2` for password hashing
- `iron-session` for signed cookie sessions
- `js-yaml` for config
- `zod` for request validation
- Vitest for tests

### Client
- Vite + React 18 + TypeScript
- TanStack Query (REST caching)
- Zustand (local UI state)
- Tailwind CSS
- Native `EventSource` for SSE
- React Router

### Infra
- DB: Postgres 16 container (`db/docker-compose.yml`)
- App: single container (compiled server + built client static)
- Runs on dev host, port `3001` by default

## 13. File Structure (target)

```
incidara-console/
├── Dockerfile
├── Makefile
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── .env.example
├── db/                              [done]
│   ├── docker-compose.yml
│   ├── Makefile
│   ├── .env.example
│   └── init/001_init.sql            (includes users, sessions, tasks, _migrations)
├── config/                          [runtime; gitignored]
│   ├── agents.yaml
│   └── groups.yaml
├── docs/
│   ├── design.md                    (this file)
│   └── plan.md
├── server/
│   ├── package.json
│   └── src/
│       ├── server.ts
│       ├── auth.ts
│       ├── session-cookie.ts
│       ├── session-store.ts
│       ├── task-store.ts
│       ├── task-reconciler.ts         (status + queue-promotion reconciler)
│       ├── schedule-store.ts          (NEW)
│       ├── scheduler.ts               (NEW: periodic schedule firing loop)
│       ├── agent-registry.ts
│       ├── groups.ts
│       ├── authz.ts
│       ├── gateway-client.ts
│       ├── sse-task-updater.ts        (SSE parser + task update side-effect + queue promoter)
│       ├── db/
│       │   ├── client.ts
│       │   ├── migrate.ts
│       │   └── migrations/
│       ├── middleware/require-auth.ts
│       └── routes/
│           ├── auth.ts
│           ├── agents.ts
│           ├── sessions.ts
│           ├── tasks.ts               (list, get, create-in-session, cancel-pending)
│           ├── messages.ts            (guidance)
│           ├── schedules.ts           (NEW)
│           └── proxy.ts
└── client/
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api/{auth,agents,sessions,tasks,schedules}.ts
        ├── hooks/{useMe,useAgents,useTasks,useSchedules,useSse}.ts
        ├── pages/
        │   ├── Login.tsx
        │   ├── Signup.tsx
        │   ├── Home.tsx                (agent overview)
        │   ├── Tasks.tsx                (flat task list)
        │   ├── Schedules.tsx            (NEW)
        │   ├── AgentDetail.tsx
        │   └── SessionWorkspace.tsx
        └── components/
            ├── Sidebar.tsx
            ├── AgentCard.tsx
            ├── TaskRow.tsx              (shared by Tasks + AgentDetail)
            ├── TaskStatusBadge.tsx      (pending/running/waiting/completed/error/interrupted/cancelled)
            ├── NewTaskForm.tsx          (new/existing session selector; always accepts)
            ├── Timeline.tsx
            ├── TaskCard.tsx             (in timeline; pending cards have Cancel button)
            ├── ToolCard.tsx
            ├── MessageBubble.tsx
            ├── Composer.tsx             (adaptive: guidance vs queue vs start)
            ├── Inspector.tsx
            ├── ShareSessionModal.tsx
            ├── NewTaskModal.tsx         (global, with agent picker)
            ├── ScheduleRow.tsx          (NEW)
            ├── ScheduleFormModal.tsx    (NEW: create/edit schedule)
            ├── CommandPalette.tsx
            └── Protected.tsx
```

## 14. Phasing (see plan.md for task-level detail)

| Phase | Scope | Exit |
|---|---|---|
| **1 — Server foundation** | DB, migrations, auth, agent/groups registry, authz | signup/login/me/agents work |
| **2 — Sessions + Tasks API + gateway client** | Sessions CRUD, Tasks REST (with queue/pending/cancel), messages proxy, SSE proxy with side-effect, reconciler (status + queue promotion) | Full task lifecycle with queue through API |
| **3 — Client shell + auth** | Vite + React + Tailwind + Router + Protected | user can log in |
| **4 — Home + Tasks + Agent Detail** | Agent overview, flat task list, agent page w/ new task form | can see state + create tasks |
| **5 — Session Workspace** | Timeline (task-grouped with pending cards + cancel), SSE, adaptive composer (never blocks) | full live interaction |
| **6 — Polish** | Share modal, ⌘K palette, right inspector, keyboard shortcuts, empty/error states | UX complete |
| **7 — Schedules** | Schedule CRUD API, scheduler loop, `/schedules` page + modal, "Schedule this task" entry points, task linkage | recurring + one-shot tasks work |
| **8 — Deploy** | Dockerfile + Makefile, deploy to dev host | live at port 3001 |

## 15. Open Questions

1. **Reconciler cadence** — 60s default. Make env-configurable (`RECONCILER_INTERVAL_MS`).
2. **Sidebar "needs attention" indicator** — aggregate from sessions' task statuses. Recompute client-side from `/api/agents` counters.
3. **Stuck-task detection** — should the reconciler flag `running` tasks older than N minutes as suspicious? Not in MVP; log only.
4. **Password reset / email verification** — not in MVP.
5. **Audit log** — not in MVP. Could add `audit_events` table later.
6. **Rate limiting** — not in MVP.

## 16. References

- `incidara/web-ui-design.md`
- `incidara/docs/design/agent-gateway.md`
- [iron-session](https://github.com/vvo/iron-session)
- [argon2 npm](https://www.npmjs.com/package/argon2)
- [TanStack Query](https://tanstack.com/query)
