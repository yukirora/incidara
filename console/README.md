# Incidara Console

Incidara Console is the interaction and task-control layer for the agent system. It consists of a React client, a Node.js API, and PostgreSQL state.

## Capabilities

- Agent discovery and access control
- Persistent sessions and tasks
- Live SSE timelines for messages, thinking, tools, and sub-agents
- Human approval and denial of gated operations
- Queued and scheduled tasks
- Interrupted-session recovery and reconciliation with agent gateways
- Usage, reliability, availability, job, utilization, and agent metrics
- Administrative users, groups, permissions, and model pricing

## Structure

```text
console/
├── client/       # React, Vite, TanStack Query, Zustand
├── server/       # Express API, scheduler, reconciler, PostgreSQL stores
├── config/       # agent and group registry examples
├── db/           # local PostgreSQL bootstrap
└── Dockerfile*   # combined or split API/web images
```

## Configure

```bash
cp .env.example .env
cp config/agents.yaml.example config/agents.yaml
cp config/groups.yaml.example config/groups.yaml
```

Required server settings:

| Variable | Purpose |
|---|---|
| `SESSION_SECRET` | Signs the Console session cookie |
| `CHAT_UI_DATABASE_URL` | Console PostgreSQL connection |
| `AGENTS_CONFIG_PATH` | Agent gateway registry |
| `GROUPS_CONFIG_PATH` | Initial access groups |

Additional report integrations use their existing environment variables and can remain unavailable when working only on task control.

## Develop

From the repository root:

```bash
make install

cd console
npx --yes pnpm@10.33.0 --filter server dev
npx --yes pnpm@10.33.0 --filter client dev
```

## Test and build

```bash
cd console
npx --yes pnpm@10.33.0 test
npx --yes pnpm@10.33.0 --filter client build
npx --yes pnpm@10.33.0 --filter server build
```

For the deployed split layout, `Dockerfile.api` builds the API and `Dockerfile.web` serves the client through nginx. The rendered Compose graph exposes them as `incidara-console-api` and `incidara-console-web`.
