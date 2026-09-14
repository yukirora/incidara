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

```bash
cp compose/config.yaml.example compose/config.yaml
$EDITOR compose/config.yaml
```

`compose/config.yaml` is ignored by Git. Replace the `CHANGE_ME` values required by your deployment. Keep unused integrations disabled or unset rather than committing credentials.

Important path settings:

```yaml
common:
  repo_dir: /data/agents/incidara
  chat_ui_dir: /data/agents/incidara/console
```

Configure the Console registry separately:

```bash
cp console/config/agents.yaml.example console/config/agents.yaml
cp console/config/groups.yaml.example console/config/groups.yaml
```

## Validate and render

```bash
.venv/bin/python compose/render.py --check
.venv/bin/python compose/render.py

cd compose/rendered
docker compose config
docker compose config --services
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

`agent-backup` synchronizes agent workspaces and transcripts to OSS. PostgreSQL backup services use pgBackRest, separate WAL storage, retention settings, and opt-in restore profiles. Both are disabled by default in the sanitized example. See [backup.md](backup.md).
