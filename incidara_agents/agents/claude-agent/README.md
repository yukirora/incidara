# Claude Agent

HTTP gateway that wraps the Claude Code SDK (`@anthropic-ai/claude-code`) into a stateful, multi-session API server. All LTP agents (triage, repair, etc.) extend this base image with their own MCP servers and skills.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Express HTTP Server (port 8000)                │
│                                                 │
│  /sessions          → Session CRUD              │
│  /sessions/:id/messages → Send prompt / resume  │
│  /sessions/:id/interrupt → Abort running query  │
│  /sessions/:id/events/stream → SSE event feed   │
│  /sessions/:id/state → Current state snapshot   │
│  /sessions/:id/permissions → Approve/deny tools │
│  /v1/chat/completions → OpenAI-compatible API   │
│  /v1/schedule       → Schedule recurring prompts│
│  /health             → Liveness check           │
│                                                 │
│  ClaudeAdapter ──→ Claude Code SDK (query())    │
│       ↕                                          │
│  SessionStore ──→ in-memory + FsPersistence     │
│       ↕                                          │
│  EventBus ──→ SSE stream + JSONL disk files     │
│                                                 │
│  PermissionManager ──→ auto-approve / ask gate  │
│  MCP Servers (stdio subprocesses)               │
│  Skills (filesystem, mounted at runtime)        │
└─────────────────────────────────────────────────┘
```

## Design

### Session Lifecycle

Sessions are the core abstraction. Each session wraps one Claude Code `query()` loop:

```
starting → running → busy → waiting_input → running → ... → completed
                 ↘ interrupted (user abort)
                 ↘ error
```

- **running**: Claude is processing, streaming text deltas via SSE
- **busy**: Claude is executing a tool (MCP call, Bash, etc.)
- **waiting_input**: Permission gate waiting for user approval
- **interrupted**: User sent `POST /sessions/:id/interrupt`, query aborted
- **completed**: Claude finished its response
- **error**: Unrecoverable error

### Persistence

- **In-memory**: `SessionStore` holds active sessions for fast access
- **Disk**: `FsPersistence` writes event JSONL + session metadata to `SESSIONS_DIR`
- **Recovery**: On restart, non-terminal sessions are restored as `interrupted`
- **Periodic snapshot**: Metadata is snapshotted every 10s so titles/status survive crashes

### Permission Model

Tool calls go through `PermissionManager` which evaluates rules from `PERMISSION_RULES` env var:

```json
[{"pattern":"*","decision":"allow"}]
```

When a tool needs approval, the session enters `waiting_input` and an SSE event is emitted. Clients approve/deny via `POST /sessions/:id/permissions/:permId/approve|deny`.

### MCP Servers

Configured in `~/.claude.json` (written by `setup-mcp.sh` at container start). Each MCP server runs as a stdio subprocess. The Claude SDK discovers and calls them automatically.

### SDK Sandbox

Disabled (`sandbox: { enabled: false }`) because the container IS the sandbox — no need for the SDK's namespace isolation, which requires `CAP_SYS_ADMIN` and causes EROFS errors.

## API Reference

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Create session with `{ prompt, workspace_path, title }` |
| `GET` | `/sessions` | List all sessions |
| `GET` | `/sessions/:id` | Get session details |
| `DELETE` | `/sessions/:id` | Delete session |

### Messages & Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/:id/messages` | Send follow-up prompt `{ prompt }` |
| `POST` | `/sessions/:id/interrupt` | Abort running query |
| `POST` | `/sessions/:id/resume` | Resume from `interrupted` or `error` state |
| `POST` | `/sessions/:id/permissions/:permId/approve` | Approve pending tool call |
| `POST` | `/sessions/:id/permissions/:permId/deny` | Deny pending tool call |

### Events (SSE)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions/:id/events/stream` | SSE stream of gateway events |
| `GET` | `/sessions/:id/events` | Fetch historical events (paginated) |
| `GET` | `/sessions/:id/rounds` | Fetch conversation rounds |
| `GET` | `/sessions/:id/state` | Current state snapshot |

### Event Types

| Event | When |
|-------|------|
| `session.started` | Session created |
| `session.state_changed` | Status transition |
| `session.completed` | Claude finished |
| `session.interrupted` | User aborted |
| `session.error` | Unrecoverable error |
| `message.user` | User prompt received |
| `message.agent` | Agent response (complete) |
| `message.delta` | Agent response (streaming chunk) |
| `tool.started` | Tool call begins |
| `tool.stdout` | Tool output chunk |
| `tool.finished` | Tool call ends |
| `permission.requested` | Tool needs approval |
| `permission.resolved` | Approval decision made |
| `artifact.created` | File created by agent |

### OpenAI-Compatible API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat/completions` | OpenAI-format chat completion (non-streaming) |
| `GET` | `/v1/models` | List available models |

### Scheduling

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/schedule` | Schedule recurring prompt `{ prompt, cron, workspace_path }` |
| `GET` | `/v1/schedule` | List scheduled jobs |
| `DELETE` | `/v1/schedule/:id` | Cancel scheduled job |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{ status: "ok", sessions: N }` |

## How Agent Images Extend This

Each agent (triage, repair, etc.) creates a Dockerfile that:

1. `FROM claude-agent:latest`
2. `COPY` its MCP server packages to `/opt/`
3. `pip install` the MCP servers + ltp-platform SDK
4. Optionally installs tools like `ipmitool`

At runtime, `setup-mcp.sh` writes `~/.claude.json` with the agent's MCP server config and env vars. The entrypoint then launches the gateway server.

```
claude-agent:latest          ← this image (generic runtime)
  ├── repair-agent           ← + node-operations + agent-evidence + ipmitool (ops role)
  └── triage-agent           ← + node-operations + agent-evidence + ipmitool (diagnosis role)
```

## Build

```bash
cd agents/claude-agent && make build
# Produces: claude-agent:latest
```

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | HTTP server port |
| `SESSIONS_DIR` | `/app/workspace/sessions` | JSONL persistence directory |
| `MAX_TURNS` | `50` | Max Claude SDK turns per session |
| `PERMISSION_RULES` | (see defaults) | JSON array of permission rules |
| `PERMISSION_TIMEOUT_MS` | `300000` | Auto-deny timeout for permission prompts |
| `SESSION_TTL_MS` | `3600000` | Session time-to-live (cleanup, currently disabled) |
| `CLAUDE_SKILL_DIR` | — | Path to skill files for the agent |
| `ANTHROPIC_AUTH_TOKEN` | — | Claude API key (required) |
| `ANTHROPIC_BASE_URL` | — | Custom API base URL |
