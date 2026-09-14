# incidara-console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-user web UI (React + Node + Postgres) that authenticates users, authorizes access to agents, and proxies live sessions to the claude-agent gateway. Tasks are first-class, persistent entities with an explicit lifecycle.

**Architecture:** Browser ↔ HTTP+SSE ↔ Node/Express UI server ↔ HTTP+SSE ↔ claude-agent gateway. Postgres for users, sessions, and tasks. Agent + group registries from YAML config. See [`design.md`](./design.md).

**Tech Stack:** Node 22, TypeScript 5, Express 5, `pg`, `argon2`, `iron-session`, `js-yaml`, `zod`, Vitest, Vite, React 18, TanStack Query, Zustand, Tailwind CSS.

**Working directory:** `incidara-console/` (this repo)

**Deployment target:** same host as claude-agent gateway (not local). Sync via rsync, run via Docker on host.

---

## Phase 1 — Server foundation

Exit criteria:
- `POST /api/auth/signup` / `/login` / `GET /api/auth/me` work
- `GET /api/agents` returns the filtered list
- DB migrations run cleanly on server startup

### Task 1: Server package scaffold

**Files:** root `package.json`, `tsconfig.json`, `vitest.config.ts`, `.env.example`, `server/package.json`, `server/tsconfig.json`

- [ ] **Step 1: Root `package.json`**

```json
{
  "name": "incidara-console",
  "private": true,
  "type": "module",
  "workspaces": ["server", "client"],
  "scripts": {
    "dev:server": "pnpm --filter server dev",
    "build:server": "pnpm --filter server build",
    "test": "pnpm -r test"
  }
}
```

- [ ] **Step 2: `server/package.json`**

```json
{
  "name": "server",
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/server.ts",
    "build": "tsc",
    "start": "node dist/server.js",
    "test": "vitest run"
  },
  "dependencies": {
    "express": "^5.1.0",
    "pg": "^8.13.0",
    "argon2": "^0.41.0",
    "iron-session": "^8.0.0",
    "js-yaml": "^4.1.0",
    "zod": "^3.23.0"
  },
  "devDependencies": {
    "@types/express": "^5.0.0",
    "@types/node": "^22.0.0",
    "@types/pg": "^8.11.0",
    "@types/js-yaml": "^4.0.9",
    "tsx": "^4.19.0",
    "typescript": "^5.6.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 3: tsconfigs** (root references `server`; server config identical to gateway's)

- [ ] **Step 4: `vitest.config.ts`** at root

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({
  test: { environment: "node", include: ["server/src/**/*.test.ts"] },
});
```

- [ ] **Step 5: `.env.example`** at root

```
PORT=3001
SESSION_SECRET=change-me-32-chars-min-change-me-32
CHAT_UI_DATABASE_URL=postgresql://chat_ui_user:change-me@127.0.0.1:5433/chat_ui
AGENTS_CONFIG_PATH=./config/agents.yaml
GROUPS_CONFIG_PATH=./config/groups.yaml
RECONCILER_INTERVAL_MS=60000
```

- [ ] **Step 6: Install + typecheck**

```bash
pnpm install
pnpm --filter server exec tsc --noEmit
```

- [ ] **Step 7: Commit.** `chore: scaffold TypeScript server package`

---

### Task 2: DB client + migration runner

**Files:** `server/src/db/client.ts`, `server/src/db/migrate.ts`, `server/src/db/migrations/` (empty dir)
**Test:** `server/src/db/migrate.test.ts`

Same as the earlier plan — uses temp dir + real DB for testing. Tests skip if `CHAT_UI_DATABASE_URL` unset.

- [ ] **Step 1:** Write `migrate.test.ts` (test creates temp migrations dir, applies, re-applies to confirm idempotency)
- [ ] **Step 2:** Write `client.ts` — single `createPool(url)` helper
- [ ] **Step 3:** Write `migrate.ts` — iterates `.sql` files in dir, skips already-applied, runs in a transaction per file
- [ ] **Step 4:** Tests PASS (when DB is reachable).
- [ ] **Step 5: Commit.** `feat(server): add pg pool + migration runner`

---

### Task 3: Auth module

**Files:** `server/src/auth.ts`
**Test:** `server/src/auth.test.ts`

- [ ] Exports: `hashPassword`, `verifyPassword`, `signup(pool, {email, password, name?})`, `login(pool, {email, password})`
- [ ] Uses `argon2id` with defaults
- [ ] `signup` throws on duplicate email
- [ ] Tests: roundtrip hash, signup + login, wrong password, duplicate email
- [ ] **Commit.** `feat(server): signup + login with argon2`

---

### Task 4: Agent registry (YAML loader)

**Files:** `server/src/agent-registry.ts`
**Test:** `server/src/agent-registry.test.ts`

- [ ] Exports: `Agent` type + `loadAgents(path)`
- [ ] Uses zod for schema validation
- [ ] Required fields: `id`, `name`, `gateway_url` (must be URL), `backend` (enum claude_code | pi_agent), `access.owners/shared_with/shared_with_groups`
- [ ] Tests: valid yaml, malformed yaml, missing required fields
- [ ] **Commit.** `feat(server): load agents from YAML with zod validation`

---

### Task 5: Groups registry

**Files:** `server/src/groups.ts`
**Test:** `server/src/groups.test.ts`

- [ ] Exports: `Group` + `loadGroups(path)` + `resolveUserGroups(groups, email)`
- [ ] Tests: load, resolve for member in multiple groups, non-member returns empty
- [ ] **Commit.** `feat(server): load groups + user membership resolver`

---

### Task 6: Authz rules

**Files:** `server/src/authz.ts`
**Test:** `server/src/authz.test.ts`

- [ ] `canAccessAgent(agent, email, userGroups): boolean` — owner / shared / group
- [ ] `canViewSession(session, email, userGroups): boolean`
- [ ] `isSessionOwner(session, email): boolean` — single-predicate helper for interaction rights
- [ ] Tests for each rule with positive + negative cases
- [ ] **Commit.** `feat(server): authorization rules for agent + session`

---

### Task 7: Session cookie + require-auth middleware

**Files:** `server/src/session-cookie.ts`, `server/src/middleware/require-auth.ts`

- [ ] `getSession(req, res, secret)` — iron-session wrapper with SessionData type
- [ ] `requireAuth(secret)` middleware — sets `req.userEmail`, 401 otherwise
- [ ] No separate tests; covered by auth routes test
- [ ] **Commit.** `feat(server): cookie session helper + auth middleware`

---

### Task 8: Auth routes

**Files:** `server/src/routes/auth.ts`
**Test:** `server/src/routes/auth.test.ts`

- [ ] `POST /api/auth/signup` — validate via zod, insert user, set cookie
- [ ] `POST /api/auth/login` — verify, set cookie
- [ ] `POST /api/auth/logout` — destroy cookie
- [ ] `GET /api/auth/me` — return current user
- [ ] Tests: signup sets cookie, login wrong password 401, me 401 without / 200 with cookie
- [ ] **Commit.** `feat(server): /api/auth/{signup,login,logout,me}`

---

### Task 9: Agents route

**Files:** `server/src/routes/agents.ts`
**Test:** `server/src/routes/agents.test.ts`

- [ ] `GET /api/agents` — filter by user access, include `access_level` per entry
- [ ] Response enriches with aggregate counters from `tasks` table: `{running, waiting, completed_today}`
- [ ] `GET /api/agents/:id` — full metadata minus `gateway_url`; 404/403 as appropriate
- [ ] Tests: filter to accessible agents only, 403 on inaccessible, `access_level` correctness
- [ ] **Commit.** `feat(server): /api/agents with access filter + counters`

---

### Task 10: Wire Phase 1 entry point

**Files:** `server/src/server.ts`

- [ ] Load env; create pool; run migrations
- [ ] Load agents.yaml + groups.yaml
- [ ] Mount `/api/auth`, `/api/agents` (with `requireAuth` on agents)
- [ ] `GET /health`
- [ ] Listen on `PORT`
- [ ] Smoke test: signup via curl, then `GET /api/agents`
- [ ] **Commit.** `feat(server): wire Phase 1 entry point`

---

## Phase 2 — Sessions + Tasks API + gateway client

Exit criteria:
- Can create sessions via `POST /api/agents/:id/sessions`
- Can start new tasks in existing sessions via `POST /api/sessions/:id/tasks`
- Can send guidance via `POST /api/sessions/:id/messages`
- SSE proxy works end-to-end AND updates task rows as side-effect
- Reconciler catches stale task statuses

### Task 11: Gateway client

**Files:** `server/src/gateway-client.ts`
**Test:** `server/src/gateway-client.test.ts` (mocks gateway with express)

- [ ] Class with methods: `createSession`, `sendMessage`, `interrupt`, `resume`, `getState`, `getEvents`, `deleteSession`, `openEventStream` (returns raw `Response` for proxying)
- [ ] Throws on non-2xx responses
- [ ] **Commit.** `feat(server): GatewayClient with HTTP + SSE methods`

---

### Task 12: SessionStore (DB wrapper)

**Files:** `server/src/session-store.ts`
**Test:** `server/src/session-store.test.ts`

- [ ] Methods: `create`, `getById`, `getByGatewayId`, `listVisible`, `updateTitle`, `updateSharing`, `delete`
- [ ] `listVisible` query uses `$1 = ANY(shared_with_users) OR shared_with_groups && $2::text[]`
- [ ] Tests: create/get/list/update/delete; group-based visibility
- [ ] **Commit.** `feat(server): SessionStore with visibility query`

---

### Task 13: TaskStore (DB wrapper)

**Files:** `server/src/task-store.ts`
**Test:** `server/src/task-store.test.ts`

- [ ] Methods:
  - `create({sessionId, prompt, submitterEmail, status, fromScheduleId?})` — inserts with given status (`pending` or `running`)
  - `getById(id)`
  - `latestForSession(sessionId)` — returns newest task (or null) regardless of status
  - `latestActiveForSession(sessionId)` — returns newest task in `running`/`busy`/`waiting_input` or null
  - `list({email, groups, status?, agentId?, scope?, limit, offset})` — joins sessions for visibility, sort by `created_at DESC`
  - `updateStatus(id, status, endedAt?)`
  - `updateOutputPreview(id, text)` — no-op if already terminal (preserves final preview)
  - `cancelPending(id)` — atomic `UPDATE ... SET status='cancelled' WHERE id=$1 AND status='pending'` → returns rowcount
  - `promoteNextPending(sessionId)` — atomic `UPDATE ... SET status='running', started_at=now() WHERE id = (SELECT id FROM tasks WHERE session_id=$1 AND status='pending' ORDER BY id ASC LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *` → returns task row or null
  - `sessionsWithPendingAndNoActive()` — for reconciler: find sessions needing promotion
  - `countByStatus({email, groups, agentId?})` — for agent-card counters
- [ ] Tests: each method; visibility filtering; promotion atomicity (simulate concurrent promoters via two transactions); cancel-pending-only rule
- [ ] **Commit.** `feat(server): TaskStore with queue + promotion`

---

### Task 14: SSE → Task update parser + queue promoter

**Files:** `server/src/sse-task-updater.ts`
**Test:** `server/src/sse-task-updater.test.ts`

- [ ] Function `applyEventToTask(deps, sessionId, parsedEvent)`:
  - `message.agent` → `taskStore.updateOutputPreview`
  - `session.waiting_input` → status `waiting_input`
  - `session.completed` → status `completed`, set `ended_at`, **then call `promoteNext(sessionId)`**
  - `session.error` → status `error`, set `ended_at`, set `output_preview` to error if empty, **then call `promoteNext(sessionId)`**
  - `session.interrupted` → status `interrupted`, set `ended_at`, set `output_preview` to partial if empty, **then call `promoteNext(sessionId)`**
- [ ] Function `promoteNext(deps, sessionId)`:
  - Call `taskStore.promoteNextPending(sessionId)` → atomic promotion
  - If a task was promoted, call gateway `sendMessage(gateway_session_id, prompt)` to kick it off
  - On gateway error → mark task as `error` with error message
- [ ] Function `extractCompleteSseEvents(buffer): {parsed: GatewayEvent[]; remainder: string}`:
  - Finds complete `data:` events terminated by double-newline
  - Parses JSON payload
  - Returns unparsed tail
- [ ] Tests:
  - Each event type maps correctly
  - Incomplete SSE chunk returns as remainder
  - Terminal event triggers promotion of next pending
  - Promotion gateway-call failure marks task as error
- [ ] **Commit.** `feat(server): SSE parser + task updates + queue promotion`

---

### Task 15: Sessions routes

**Files:** `server/src/routes/sessions.ts`
**Test:** `server/src/routes/sessions.test.ts`

- [ ] `POST /api/agents/:agentId/sessions { prompt, title? }`:
  - Check agent access
  - Call gateway `createSession`
  - Insert `sessions` row
  - Insert initial `tasks` row (running)
  - Return `{session, task}`
- [ ] `GET /api/sessions?scope=mine|shared|all&agent_id=X` — use SessionStore.listVisible
- [ ] `GET /api/sessions/:id` — check canViewSession, return session
- [ ] `PATCH /api/sessions/:id { title?, shared_with_users?, shared_with_groups? }` — owner-only
- [ ] `DELETE /api/sessions/:id` — owner-only. Call gateway delete (best-effort). Cascade-deletes tasks via FK.
- [ ] Tests for all cases including 403 on non-owner patch/delete
- [ ] **Commit.** `feat(server): /api/sessions CRUD`

---

### Task 16: Tasks routes

**Files:** `server/src/routes/tasks.ts`
**Test:** `server/src/routes/tasks.test.ts`

- [ ] `GET /api/tasks?status=X&agent_id=X&scope=mine|shared|all&limit=50&offset=0` — TaskStore.list, return `{tasks, total}`
- [ ] `GET /api/tasks/:id` — fetch, check canViewSession on parent
- [ ] `POST /api/sessions/:id/tasks { prompt }`:
  - Check isSessionOwner
  - Check `latestActiveForSession` — if there's an active task, insert task with `status='pending'` (no gateway call); else insert `status='running'` + call gateway `sendMessage`
  - Return `{task}` with its status
- [ ] `DELETE /api/tasks/:id`:
  - Owner-only (task.submitter_email == me)
  - `taskStore.cancelPending(id)` — returns `{cancelled: true}` on rowcount=1, else 409
- [ ] Tests:
  - List with filters (status, agent_id, scope)
  - Pagination
  - POST /tasks on idle session → task runs immediately
  - POST /tasks on busy session → task pending, no gateway call
  - DELETE /tasks on pending → cancelled
  - DELETE /tasks on running → 409
- [ ] **Commit.** `feat(server): /api/tasks with queue + cancel`

---

### Task 17: Messages (guidance) route

**Files:** `server/src/routes/messages.ts`
**Test:** `server/src/routes/messages.test.ts`

- [ ] `POST /api/sessions/:id/messages { content }`:
  - Check isSessionOwner
  - Check latest task status is `running|busy|waiting_input`; else 409 "No active task"
  - Call gateway `sendMessage`
  - NO task row insert
  - Return `{accepted: true}`
- [ ] Tests:
  - 409 when latest task terminal
  - Success path proxies to gateway and does not insert task
- [ ] **Commit.** `feat(server): /api/sessions/:id/messages for guidance`

---

### Task 18: Proxy route (state, events, stream, interrupt, resume)

**Files:** `server/src/routes/proxy.ts`
**Test:** `server/src/routes/proxy.test.ts`

- [ ] `GET /api/sessions/:id/state` — check canViewSession, proxy
- [ ] `GET /api/sessions/:id/events?after_seq=N` — check canViewSession, proxy
- [ ] `GET /api/sessions/:id/events/stream`:
  - Check canViewSession
  - Open upstream SSE
  - Stream bytes through + parse + call `applyEventToTask` on each event
  - Cancel upstream on client close
- [ ] `POST /api/sessions/:id/interrupt` — owner-only, proxy
- [ ] `POST /api/sessions/:id/resume` — owner-only, proxy
- [ ] Tests:
  - 403 for non-viewer on GET
  - 403 for non-owner on POST interrupt/resume
  - SSE proxies bytes + triggers task update (via injected `applyEventToTask` spy)
- [ ] **Commit.** `feat(server): /api/sessions/:id/{state,events,stream,interrupt,resume}`

---

### Task 19: Task reconciler (status + queue)

**Files:** `server/src/task-reconciler.ts`
**Test:** `server/src/task-reconciler.test.ts`

- [ ] `reconcileOnce({taskStore, sessionStore, getAgents, makeClient})`:
  - **Pass A (status):** for each task in `running`/`waiting_input`, fetch gateway `getState`; if gateway state is terminal, update task accordingly
  - **Pass B (queue):** for each session in `taskStore.sessionsWithPendingAndNoActive()`, call `promoteNext(sessionId)` (same helper from sse-task-updater)
  - Graceful: log + continue on individual fetch errors
- [ ] `startReconciler(deps, intervalMs)` — returns stop function; calls reconcileOnce on an interval
- [ ] Tests:
  - reconciles running → completed
  - leaves unaffected tasks alone
  - handles gateway error
  - promotes pending when session has no active task
  - skips promotion when session has active task
- [ ] **Commit.** `feat(server): reconciler — status check + queue promotion`

---

### Task 20: Wire Phase 2 into server.ts

**Files:** `server/src/server.ts`

- [ ] Instantiate `SessionStore`, `TaskStore`, `GatewayClient` factory
- [ ] Mount `/api` sessions, tasks, messages, proxy (all behind `requireAuth`)
- [ ] Start reconciler interval
- [ ] Update env handling to include `RECONCILER_INTERVAL_MS`
- [ ] **Commit.** `feat(server): wire Phase 2 sessions + tasks + proxy + reconciler`

---

## Phase 3 — Client shell + auth pages

### Task 21: Vite + React scaffold

**Files:** `client/package.json`, `client/vite.config.ts`, `client/tsconfig.json`, `client/tailwind.config.js`, `client/postcss.config.js`, `client/index.html`, `client/src/{main,App,index.css}.tsx`

- [ ] `client/package.json` — react 18, react-router-dom 6, @tanstack/react-query 5, zustand 5, tailwind
- [ ] `vite.config.ts` — proxy `/api` + `/health` to `http://127.0.0.1:3001`
- [ ] Tailwind + PostCSS configured
- [ ] `main.tsx` mounts App inside `QueryClientProvider` + `BrowserRouter`
- [ ] Smoke: `pnpm --filter client dev`; see placeholder "Home"
- [ ] **Commit.** `feat(client): scaffold Vite + React + Tailwind`

---

### Task 22: Auth API + hooks + pages

**Files:** `client/src/lib/api.ts`, `client/src/api/auth.ts`, `client/src/hooks/useMe.ts`, `client/src/components/Protected.tsx`, `client/src/pages/Login.tsx`, `client/src/pages/Signup.tsx`

- [ ] `lib/api.ts` — fetch wrapper with credentials: 'include'
- [ ] `api/auth.ts` — signup, login, logout, me
- [ ] `useMe()` — TanStack Query
- [ ] `Protected` — redirects to `/login` if unauthenticated
- [ ] Login.tsx + Signup.tsx — minimal Tailwind forms
- [ ] Routes in `App.tsx`: `/login`, `/signup`, `/` (wrapped in Protected)
- [ ] Smoke: sign up, get redirected home, log out, return to login
- [ ] **Commit.** `feat(client): auth API + login/signup pages`

---

## Phase 4 — Home (agent overview) + Tasks + Agent Detail

Exit criteria:
- Home shows agent cards with aggregate counters
- Tasks page shows flat list with filters
- Agent detail shows filtered tasks + new-task composer with session choice

### Task 23: Sidebar + layout shell

**Files:** `client/src/components/Sidebar.tsx`, `client/src/components/AppLayout.tsx`, `client/src/hooks/useSidebarState.ts`

- [ ] `Sidebar.tsx` — top section: New task button + Search button; nav: Overview, Tasks; Agents (my + shared) from `useAgents`; user menu at bottom
- [ ] `AppLayout.tsx` — renders `<Sidebar>` + `<Outlet>`, applies to all Protected routes
- [ ] `useSidebarState` — collapsed/expanded in localStorage
- [ ] **Commit.** `feat(client): persistent sidebar layout`

---

### Task 24: Agents API + hook

**Files:** `client/src/api/agents.ts`, `client/src/hooks/useAgents.ts`

- [ ] API: `listAgents()`, `getAgent(id)`
- [ ] Hook: `useAgents()` + `useAgent(id)`
- [ ] **Commit.** `feat(client): agents API + hooks`

---

### Task 25: Home — agent overview page

**Files:** `client/src/pages/Home.tsx`, `client/src/components/AgentCard.tsx`, `client/src/components/SummaryStrip.tsx`

- [ ] `SummaryStrip.tsx` — 4 counters computed from agent list
- [ ] `AgentCard.tsx` — name, badge, status dot, counters, one-line live preview (via `getState` of the most interesting active session; optional in MVP)
- [ ] `Home.tsx` — strip + "My agents" + "Shared with me"
- [ ] **Commit.** `feat(client): Home = agent overview`

---

### Task 26: Tasks API + hook + page

**Files:** `client/src/api/tasks.ts`, `client/src/hooks/useTasks.ts`, `client/src/pages/Tasks.tsx`, `client/src/components/TaskRow.tsx`, `client/src/components/TaskStatusBadge.tsx`, `client/src/components/TasksFilterBar.tsx`

- [ ] API: `listTasks({status, agent_id, scope, limit, offset})`, `getTask(id)`
- [ ] Hook: `useTasks(filters)` with keepPreviousData for pagination
- [ ] `TaskStatusBadge.tsx` — 5-state color mapping
- [ ] `TaskRow.tsx` — icon, agent, prompt, submitted-by, outcome; click → navigate `/sessions/:session_id#task-:task_id`
- [ ] `TasksFilterBar.tsx` — status dropdown, agent dropdown, time dropdown, scope dropdown
- [ ] `Tasks.tsx` — filter bar + `<TaskRow>` list + "Load older" button
- [ ] **Commit.** `feat(client): Tasks page with filters`

---

### Task 27: Agent detail page + NewTaskForm

**Files:** `client/src/pages/AgentDetail.tsx`, `client/src/components/NewTaskForm.tsx`

- [ ] `NewTaskForm.tsx`:
  - Textarea for prompt
  - Radio: "New session" vs "Existing session" with dropdown
  - Existing session dropdown — list my sessions on this agent where latest task is terminal
  - Submit: calls `createSession(agentId, {prompt})` OR `createTask(sessionId, {prompt})`, navigates to session workspace
- [ ] `AgentDetail.tsx` — agent header + `<NewTaskForm>` + `<Tasks>` component filtered to `agent_id`
- [ ] Route `/agents/:id` in `App.tsx`
- [ ] **Commit.** `feat(client): Agent detail with new-task form`

---

## Phase 5 — Session Workspace

Exit criteria:
- Live timeline renders task-grouped events as they stream
- Adaptive composer switches between guidance and new-task based on current task status
- Interrupt/resume work

### Task 28: Sessions API (client)

**Files:** `client/src/api/sessions.ts`

- [ ] `getSession(id)`, `patchSession`, `deleteSession`, `getState`, `getEventsHistory`, `sendGuidance`, `startNewTask`, `interrupt`, `resume`
- [ ] `eventStreamUrl(id, afterSeq?)` returns string URL (for EventSource)
- [ ] **Commit.** `feat(client): sessions API`

---

### Task 29: useSse hook

**Files:** `client/src/hooks/useSse.ts`

- [ ] Opens `EventSource` with `withCredentials: true`
- [ ] Listens to all known event types; calls `onEvent(evt)` for each
- [ ] Returns `{connected, reconnect}`
- [ ] Reconnect on manual trigger (e.g. after network blip)
- [ ] **Commit.** `feat(client): useSse hook`

---

### Task 30: Timeline with task grouping

**Files:** `client/src/components/Timeline.tsx`, `client/src/components/TaskCard.tsx`, `client/src/components/ToolCard.tsx`, `client/src/components/MessageBubble.tsx`

- [ ] `Timeline.tsx` — receives events stream; groups into tasks via `message.user` boundaries (aligned with task rows from DB via seq); scrolls to hash on mount
- [ ] `TaskCard.tsx`:
  - Header: "Task N · <status>"
  - **Pending tasks:** render a compact placeholder card with prompt + "Queued · will start when Task N-1 completes" + `[Cancel]` button (calls `DELETE /api/tasks/:id`)
  - **Running/completed tasks:** full card with:
    - Prompt bubble
    - Subsequent user bubbles inline (guidance)
    - Tool cards
    - Assistant bubbles (streaming via deltas, finalized by message.agent)
    - Footer: outcome line
  - Collapsible when completed (default collapsed after new task starts)
- [ ] `ToolCard.tsx` — icon + name + status + expandable stdout
- [ ] `MessageBubble.tsx` — role (user/assistant), text, copy button
- [ ] **Commit.** `feat(client): task-grouped timeline`

---

### Task 31: Adaptive composer

**Files:** `client/src/components/Composer.tsx`

- [ ] Props: `sessionId`, `currentTaskStatus`, `pendingCount`, `isOwner`
- [ ] If not owner: disabled with "View only" note
- [ ] Label + endpoint selection (never blocks submission):
  - `waiting_input` → "Reply to agent" + ⚠ banner → `sendGuidance` (/messages)
  - `running`/`busy` → "Queue next task" + ⏳ chip → `startNewTask` (/tasks) — creates pending
  - `pending` with items queued → "Queue next task" + "⏳ N tasks queued" → `startNewTask` (/tasks)
  - `completed`/`error`/`interrupted`/`cancelled` → "Start a new task" + ✓ chip → `startNewTask` (/tasks) — creates running
- [ ] Submission always succeeds (optimistic); on gateway error (non-409), show inline error + refetch state
- [ ] Hotkeys: `⌘↵` submit, `Esc` blur
- [ ] **Commit.** `feat(client): adaptive composer (always accepts, queues when busy)`

---

### Task 32: Session workspace page

**Files:** `client/src/pages/SessionWorkspace.tsx`, `client/src/components/SessionHeader.tsx`

- [ ] `SessionHeader.tsx` — title (editable owner-only), status badge, buttons (interrupt/resume, share, delete, inspector toggle); back link to agent
- [ ] `SessionWorkspace.tsx`:
  - Load session + initial events history
  - Subscribe to SSE via useSse
  - Apply events to local state (tasks + events)
  - Render Header + Timeline + Composer (+ optional Inspector)
  - Handle hash `#task-N` → scroll on mount + on hash change
- [ ] Route `/sessions/:id` in `App.tsx`
- [ ] Smoke test end-to-end against deployed gateway
- [ ] **Commit.** `feat(client): session workspace page`

---

## Phase 6 — Polish

### Task 33: Share session modal
Owner-only. Emails + group picks. Calls `PATCH /api/sessions/:id`. Commit.

### Task 34: Delete session confirm
Destructive button in workspace header → confirm modal → `DELETE /api/sessions/:id` → navigate back. Commit.

### Task 35: ⌘K command palette
Fuzzy search across sessions, agents, quick actions. Uses `cmdk` or a minimal custom implementation. Commit.

### Task 36: Right inspector
In workspace, toggle reveals right panel: current state JSON + last N raw events. Commit.

### Task 37: Keyboard shortcuts
Single `useKeyboardShortcuts` hook registering: `⌘N` (new task modal), `⌘K`, `⌘↵`, `i` (interrupt), `j`/`k` (cycle sidebar agents), `Esc`. Commit.

### Task 38: Global new-task modal
Triggered by sidebar button + `⌘N`. Agent picker + prompt + session choice. Commit.

### Task 39: Empty states + error states
- No agents accessible: "Ask an admin to grant access"
- No tasks matching filter: "No tasks found"
- No events (fresh session): "Waiting for the agent to start…"
- Network error on SSE: "Disconnected · Reconnecting…" with retry button

Commit.

---

## Phase 7 — Schedules

Exit criteria:
- Users can create `once`, `interval`, and `cron` schedules via UI + API
- Scheduler loop fires due schedules, creating tasks with `from_schedule_id` tagged
- `/schedules` page lists my schedules with enable/disable/delete/run-now/edit

### Task 40: ScheduleStore

**Files:** `server/src/schedule-store.ts`
**Test:** `server/src/schedule-store.test.ts`

- [ ] Methods:
  - `create(input)` — insert, compute initial `next_fire_at` from trigger
  - `getById(id)`, `list({ownerEmail, agentId?, enabled?})`
  - `update(id, patch)` — recompute `next_fire_at` when trigger fields change
  - `delete(id)`
  - `claimDue({now, limit})` — `SELECT ... FOR UPDATE SKIP LOCKED WHERE enabled AND next_fire_at <= $now`
  - `recordFiring(id, {firedAt, nextFireAt})`
- [ ] Helper `computeNextFire({triggerType, runAt, intervalSeconds, cronExpr, timezone, from})`:
  - `once` → `runAt` (or null if in past / already fired)
  - `interval` → `from + intervalSeconds`
  - `cron` → parse via `cron-parser`, return next occurrence after `from` in timezone
- [ ] Install: `pnpm --filter server add cron-parser`
- [ ] Tests: create each trigger type + compute next; claim atomicity; recordFiring updates correctly
- [ ] **Commit.** `feat(server): ScheduleStore + trigger computation`

---

### Task 41: Scheduler loop

**Files:** `server/src/scheduler.ts`
**Test:** `server/src/scheduler.test.ts`

- [ ] `fireOnce({scheduleStore, taskStore, sessionStore, getAgents, makeClient})`:
  - `scheduleStore.claimDue` → schedules to fire
  - For each schedule:
    - `session_mode='new'`: create new session + task on agent (tag `from_schedule_id`)
    - `session_mode='reuse'`: create task in `reuse_session_id` (pending or running per queue rules; tag `from_schedule_id`)
  - `recordFiring(id, ...)` with new `next_fire_at` computed
  - For `once` type, set `enabled=false` and `next_fire_at=null`
- [ ] `startScheduler(deps, intervalMs=30_000)` — periodic loop; returns stop fn
- [ ] Tests:
  - interval schedule fires, next_fire_at advances
  - once schedule fires, then disables
  - cron schedule computes next correctly in timezone
  - gateway error does not disable schedule
- [ ] **Commit.** `feat(server): scheduler loop`

---

### Task 42: Schedules routes

**Files:** `server/src/routes/schedules.ts`
**Test:** `server/src/routes/schedules.test.ts`

- [ ] `POST /api/schedules` — validate body via zod, check agent access + reuse_session ownership, insert
- [ ] `GET /api/schedules?agent_id=X&enabled=true` — scoped to owner
- [ ] `GET /api/schedules/:id` — owner only
- [ ] `PATCH /api/schedules/:id` — edit; if trigger fields change, recompute next_fire_at
- [ ] `DELETE /api/schedules/:id`
- [ ] `POST /api/schedules/:id/run-now` — fire now (same firing logic as scheduler), does not touch `next_fire_at`
- [ ] Tests for all endpoints + validation failures (400 on bad cron, 400 on `once` in the past, 403 on non-owner, 404 on not found)
- [ ] **Commit.** `feat(server): /api/schedules CRUD + run-now`

---

### Task 43: Wire scheduler into server.ts

- [ ] Instantiate `ScheduleStore`, start `scheduler` interval
- [ ] Mount `/api/schedules` route (requireAuth)
- [ ] Env var `SCHEDULER_INTERVAL_MS` (default 30000)
- [ ] **Commit.** `feat(server): wire scheduler + /api/schedules`

---

### Task 44: Client — schedules API + hook

**Files:** `client/src/api/schedules.ts`, `client/src/hooks/useSchedules.ts`

- [ ] API: `listSchedules(params)`, `getSchedule(id)`, `createSchedule`, `patchSchedule`, `deleteSchedule`, `runNow(id)`
- [ ] Hook: `useSchedules(filters)` with invalidation on mutations
- [ ] **Commit.** `feat(client): schedules API + hook`

---

### Task 45: `/schedules` page + components

**Files:** `client/src/pages/Schedules.tsx`, `client/src/components/ScheduleRow.tsx`, `client/src/components/ScheduleFormModal.tsx`

- [ ] `ScheduleRow.tsx` — row with name, agent, trigger summary (human-readable), next/last fired, actions (run-now, edit, toggle enabled, delete)
- [ ] `ScheduleFormModal.tsx`:
  - Fields per design.md §9.4
  - Client-side cron validation via `cron-parser` (install in client too)
  - Agent picker (from `useAgents`)
  - Reuse session picker (only when `session_mode='reuse'`)
  - Submit: create or patch
- [ ] `Schedules.tsx` — header + `[+ New schedule]` + list of `<ScheduleRow>`
- [ ] Route `/schedules` in `App.tsx` (Protected)
- [ ] Sidebar: add `⏱ Schedules` row below Tasks
- [ ] **Commit.** `feat(client): /schedules page + schedule form`

---

### Task 46: Task row + timeline "from schedule" badge

**Files:** update `client/src/components/TaskRow.tsx` and `TaskCard.tsx`

- [ ] If task has `from_schedule_id`, render a small `⏱ via "<schedule name>"` badge
- [ ] Clicking the badge → `/schedules` filtered to that schedule
- [ ] **Commit.** `feat(client): show schedule linkage on task views`

---

### Task 47: "Schedule this task" entry point on agent detail

**Files:** update `client/src/components/NewTaskForm.tsx`

- [ ] Add "Schedule" button next to "Start task"
- [ ] Clicking opens `ScheduleFormModal` pre-filled with agent + prompt
- [ ] On save, close modal + toast "Schedule saved"
- [ ] **Commit.** `feat(client): "Schedule this task" entry on agent detail`

---

## Phase 8 — Deploy

### Task 48: Dockerfile (multi-stage)

**Files:** `Dockerfile`, `.dockerignore`

- [ ] Multi-stage: build client → build server → runtime with compiled server + client static
- [ ] `server.ts` serves `public/` for client assets + falls back to `index.html` for SPA routes

```ts
// in server.ts
const publicDir = path.resolve("public");
if (existsSync(publicDir)) {
  app.use(express.static(publicDir));
  app.get(/^(?!\/api|\/health).*$/, (_req, res) => res.sendFile(path.join(publicDir, "index.html")));
}
```

- [ ] **Commit.** `chore: Dockerfile for combined server + client bundle`

---

### Task 49: Makefile

**Files:** `Makefile`

```makefile
.PHONY: build run stop logs

build:
	docker build -t incidara-console .

run:
	docker run -d --name incidara-console --env-file .env --network host \
		-v $(PWD)/config:/app/config:ro \
		incidara-console

stop:
	docker stop incidara-console && docker rm incidara-console

logs:
	docker logs -f incidara-console
```

Commit.

---

### Task 50: Deploy to dev host

- [ ] Sync repo via rsync (exclude node_modules, db/data, .env)
- [ ] On dev host: start DB (`cd db && make up`), bootstrap agents.yaml + groups.yaml, `make build && make run`
- [ ] Verify: signup → create task against triage-unknown agent → live events → interrupt → resume → send guidance → reply after waiting_input
- [ ] **Commit.** `deploy: Phase 7 live on dev host`

---

## Summary — file structure (final)

```
incidara-console/
├── Dockerfile
├── Makefile
├── .dockerignore
├── .env.example
├── .gitignore
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── db/                            [done]
│   ├── docker-compose.yml
│   ├── Makefile
│   ├── README.md
│   ├── .env.example
│   └── init/001_init.sql          (users, sessions, tasks, _migrations)
├── docs/
│   ├── design.md
│   └── plan.md
├── config/                        [runtime; gitignored; examples checked in]
│   ├── agents.yaml
│   └── groups.yaml
├── server/
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── server.ts
│       ├── auth.ts
│       ├── session-cookie.ts
│       ├── session-store.ts
│       ├── task-store.ts
│       ├── task-reconciler.ts
│       ├── schedule-store.ts
│       ├── scheduler.ts
│       ├── agent-registry.ts
│       ├── groups.ts
│       ├── authz.ts
│       ├── gateway-client.ts
│       ├── sse-task-updater.ts
│       ├── db/{client,migrate}.ts
│       ├── db/migrations/
│       ├── middleware/require-auth.ts
│       └── routes/
│           ├── auth.ts
│           ├── agents.ts
│           ├── sessions.ts
│           ├── tasks.ts
│           ├── messages.ts
│           ├── schedules.ts
│           └── proxy.ts
└── client/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── index.css
        ├── lib/api.ts
        ├── api/{auth,agents,sessions,tasks,schedules}.ts
        ├── hooks/{useMe,useAgents,useTasks,useSchedules,useSse,useKeyboardShortcuts,useSidebarState}.ts
        ├── components/
        │   ├── AppLayout.tsx
        │   ├── Sidebar.tsx
        │   ├── Protected.tsx
        │   ├── AgentCard.tsx
        │   ├── SummaryStrip.tsx
        │   ├── TaskRow.tsx
        │   ├── TaskStatusBadge.tsx
        │   ├── TasksFilterBar.tsx
        │   ├── NewTaskForm.tsx
        │   ├── Composer.tsx
        │   ├── Timeline.tsx
        │   ├── TaskCard.tsx
        │   ├── ToolCard.tsx
        │   ├── MessageBubble.tsx
        │   ├── SessionHeader.tsx
        │   ├── Inspector.tsx
        │   ├── ShareSessionModal.tsx
        │   ├── NewTaskModal.tsx
        │   ├── ScheduleRow.tsx
        │   ├── ScheduleFormModal.tsx
        │   └── CommandPalette.tsx
        └── pages/
            ├── Login.tsx
            ├── Signup.tsx
            ├── Home.tsx
            ├── Tasks.tsx
            ├── Schedules.tsx
            ├── AgentDetail.tsx
            └── SessionWorkspace.tsx
```

## Phase summary

| Phase | Tasks | Goal |
|---|---|---|
| 1 — Server foundation | 1–10 | Auth + agents + DB migrations working |
| 2 — Sessions + Tasks API | 11–20 | Full task lifecycle (incl. queue/pending/cancel) through API; SSE proxy updates + promotes |
| 3 — Client shell + auth | 21–22 | Vite + Router + login |
| 4 — Home + Tasks + Agent Detail | 23–27 | Navigate agents and tasks; start new tasks (new or existing session, always accepts) |
| 5 — Session Workspace | 28–32 | Live timeline (with pending cards) + adaptive composer (never blocks) |
| 6 — Polish | 33–39 | Share, palette, inspector, shortcuts, empty states |
| 7 — Schedules | 40–47 | Schedule CRUD + scheduler loop + UI; recurring/once/cron tasks |
| 8 — Deploy | 48–50 | Live on dev host at `:3001` |
