# Deploying Incidara

Incidara uses a configuration renderer rather than a checked-in deployment file. `compose/config.yaml` supplies deployment-specific values; Jinja templates produce service environment files and a Compose graph under `compose/rendered/`.

## Prerequisites

- Linux host with Docker Engine and Docker Compose
- Python 3.10+ with PyYAML and Jinja2
- Checked-out Incidara repository at the path configured by `common.repo_dir`
- SSH agent socket for agents that access nodes or switches
- Model, platform, database, BMC, ticket, Feishu, Codeup, and OSS credentials for enabled integrations
- Writable agent data, database, WAL, and backup directories

## Configure

The example deploys as it ships. It runs the Console, both databases, the MCP services and the agents on `127.0.0.1` with internal credentials filled in, so a clone can be started without editing anything:

```bash
cp compose/config.yaml.example compose/config.yaml
```

`compose/config.yaml` is ignored by Git. The values to set for real work are the external integrations, which no default can supply: the model endpoint and token (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`), cluster endpoints and tokens (`LTP_*`, `PAI_TOKEN`, `BMC_*`, `*_SSH_*`), and the platform database (`POSTGRES_CONNECTION_STR`). Replace the demo passwords before exposing a deployment beyond localhost. Keep unused integrations disabled or unset rather than committing credentials.

Host locations, ports and internal URLs are derived by the renderer, so they rarely need editing:

| Setting | Default | Purpose |
| --- | --- | --- |
| `common.state_root` | `<repo>/state` | Parent of every service's state directory. Paths in `_deploy` use `{state}`, `{repo}` and `{service}` tokens. |
| `common.bind_host` | `127.0.0.1` | Address services and the Console bind to. Set to `0.0.0.0` to expose the Console. |
| `_deploy.port` | as listed | One port per service; all services share the host network. |
| internal `*_DB_URL` | from `agent-db` / `chat-ui-db` | Built from the database section's user, password, port and database name. |

The derived values can be overridden: an explicit `EVIDENCE_DB_URL`, `CHAT_UI_DB_URL` or `state_root` in `config.yaml` is always used as written.

On a host that already runs a deployment, shift every port and rename the containers instead of editing the stack:

```bash
PORT_OFFSET=3000 make config   # and set common.name_prefix in config.yaml
```

## Console registry

The Console reads its agent and group registries from files. The deployment mounts the shipped `*.example` files until you create the real ones, so copy them only when you want to change the registry:

```bash
cp console/config/agents.yaml.example console/config/agents.yaml
cp console/config/groups.yaml.example console/config/groups.yaml
```

Each agent's `gateway_url` must reach the agent's `_deploy.port` on `127.0.0.1`, because every service shares the host network.

## Console access

The Console accepts gateway SSO (`/api/auth/gateway/login`, which needs `AUTH_GATEWAY_URL`) and local accounts (`/api/auth/signup`, then `/api/auth/login`).

A fresh deployment creates two local accounts from `compose/config.yaml`, so the Console is usable immediately:

| Account | Config keys | Groups | Role |
| --- | --- | --- | --- |
| `admin@example.com` | `incidara-console.ADMIN_EMAIL`, `ADMIN_PASSWORD` | `admins`, `sre-team` | Admin: user and permission management |
| `agent-delegate@example.com` | `common.CHAT_UI_USER`, `CHAT_UI_PASSWORD` | `sre-team` | Delegate: submits and follows tasks |

Accounts are created only when they do not exist yet, so changing a password in the config does not reset an existing account. Clear an email or password to stop creating that account, and change both passwords before exposing a deployment.

Access is group-based: `console/config/groups.yaml` decides which groups an email belongs to, and `console/config/agents.yaml` decides which groups see which agents. Passwords are at least 8 characters. `SESSION_SECRET` must be at least 32 characters or the Console API exits at startup.

## Validate and render

```bash
.venv/bin/python compose/render.py --check
.venv/bin/python compose/render.py

cd compose/rendered
docker compose config
docker compose config --services
```

`--config`, `--rendered-dir` and `--port-offset` validate a candidate configuration without touching `compose/config.yaml`:

```bash
.venv/bin/python compose/render.py --check --config /tmp/candidate.yaml --port-offset 3000
```

The renderer writes:

```text
compose/rendered/
├── docker-compose.yml
├── databases.yml
├── mcp-servers.yml
├── agents.yml
├── chat-ui.yml
├── backup.yml
└── <service>.env
```

The entire directory is ignored because rendered environment files contain deployment credentials.

## Build and start

Start the complete configured stack:

```bash
cd compose/rendered
docker compose up -d --build
```

Or deploy in layers:

```bash
# Databases
docker compose up -d agent-db chat-ui-db

# MCP services
docker compose up -d \
  agent-evidence agent-feedback agent-feedback-write \
  patrol-cron job-patrol switch-operations \
  node-ops-diagnosis node-ops-feedback node-ops-ops

# Lifecycle agents
docker compose up -d \
  detection-agent triage-agent repair-agent \
  recycler-agent feedback-agent attention-agent

# Console
docker compose up -d incidara-console-api incidara-console-web
```

Compose dependencies and health checks prevent agents from starting before their required databases and MCP services are ready.

## First deployment on a clean host

```bash
git clone <repository> && cd incidara
make up
```

That copies the example, renders it, and starts the whole stack. `make up` builds the images it cannot find; a first start on an empty host takes a few minutes.

`make fresh-deploy-check` verifies that the shipped example still deploys from scratch: it renders the example without editing it, starts both databases on empty state directories, and compares the initialised tables against the `CREATE TABLE` statements in `infra/postgresql/init.sql` and `console/db/init/`. Run it after changing the database schema, the database templates, or the example config. It skips with an explanation when no Docker daemon is available or when an `incidara-agent-db` container is already running.

The database entrypoint runs init scripts with `ON_ERROR_STOP=1`. A single bad statement therefore aborts the rest of the file, so a fresh deployment can end up with a partial schema while the container still reports healthy. The check above and the schema guards in `tests/unit_tests/infra/test_postgresql_init_schema.py` exist to catch that class of failure before release.

State lives under `common.state_root` (`state/` in the checkout by default) and is ignored by Git. The renderer creates these directories as your user, so `rm -rf state` cleans a deployment up without root. To keep state on a separate volume, set `common.state_root` to an absolute path and make sure your user can write there.

## Operate

```bash
cd compose/rendered

docker compose ps
docker compose logs --tail=100 <service>
docker compose build <service>
docker compose up -d --no-deps --force-recreate <service>
```

The remote helper in `compose/Makefile` supports configuration transfer, repository synchronization, rendering, and per-service deployment. Override its deployment values rather than editing them into source:

```bash
make -C compose status REMOTE_HOST=<user@host> REMOTE_DIR=/data/agents/incidara
make -C compose deploy SVC=detection-agent REMOTE_HOST=<user@host> REMOTE_DIR=/data/agents/incidara
```

## Incremental CI deployment

The retained Codeup pipeline performs:

```text
changed paths
→ ci/service-path-map.tsv
→ affected Compose services
→ repository sync
→ configuration render
→ build/recreate
→ health check
```

Configure the source connection and private runner placeholders in `ci/flow.yml`. Store the rendered deployment configuration in the encrypted `CONFIG_YAML_B64` pipeline variable:

```bash
bash ci/generate-config-b64.sh compose/config.yaml
```

Do not commit that output.

## Backup and restore

Backup is optional and disabled in `compose/config.yaml.example`. Leave it disabled for the first boot: a clean host has no database and no repository for pgBackRest to write into. Enable it after the deployment is running and the bucket exists.

### Agent state

The `agent-backup` service uses `infra/backup/agent/agent-sync.sh` to incrementally synchronize agent state to OSS:

- gateway session events and reports under `workspace/`;
- Claude session transcripts and task state under `claude-home/`;
- investigation artifacts stored as files.

Database directories and the Git repository are excluded because they have separate durability mechanisms. Configure `agent-backup` in `compose/config.yaml`, enable it, render again, and start it with:

```bash
cd compose/rendered
docker compose up -d agent-backup
docker compose logs -f agent-backup
```

Restore one agent or subdirectory with `ossutil` into a separate location, inspect it, and only then replace active state:

```bash
ossutil cp -r \
  oss://<bucket>/<prefix>/<agent>/ \
  /mntsys/agents/<agent>-restored/
```

### PostgreSQL

`agent-db` and `chat-ui-db` support pgBackRest with:

- separate PostgreSQL data, WAL, and pgBackRest repository paths;
- full and incremental schedules;
- retention controls;
- archive-health checks and WAL-volume limits;
- opt-in restore profiles.

Set each database service’s `BACKUP_ENABLED` and backup credentials in `compose/config.yaml`, then render and recreate the database plus its cron sidecar. Restore services are intentionally profile-gated so a normal deployment cannot enter restore mode accidentally:

```bash
cd compose/rendered
docker compose --profile restore config
docker compose --profile restore up agent-db-restore
# or
docker compose --profile restore up chat-ui-db-restore
```

A restore is a controlled operation: stop writers, verify the selected backup, restore into the configured data path, promote, confirm database health, and only then restart dependent MCP and Console services.
